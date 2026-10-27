# Web Preflight, a Blender add-on.
# Estimates the real web runtime cost of a scene (triangles, draw calls, texture
# memory) and flags the glTF export pitfalls that otherwise only show up in the
# browser, before you export. Author: Nadeschda Barenje.

bl_info = {
    "name": "Web Preflight",
    "author": "Nadeschda Barenje",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Web Preflight",
    "description": "Audit a scene against a web performance budget and flag glTF export pitfalls before export",
    "category": "Import-Export",
}

import bpy, os, json, tempfile
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, FloatProperty, PointerProperty
from bpy.types import PropertyGroup, Operator, Panel

# Budgets drawn from common web guidance (Don McCurdy: aim for < ~100 draw calls
# and < ~100k verts on mobile). All of this is editable under the Custom target.
BUDGETS = {
    "MOBILE":  {"tris": 100_000,   "draw_calls": 100, "tex_mp": 16.0, "tex_dim": 2048},
    "DESKTOP": {"tris": 1_000_000, "draw_calls": 500, "tex_mp": 64.0, "tex_dim": 4096},
}
PROCEDURAL = {"TEX_NOISE", "TEX_MUSGRAVE", "TEX_VORONOI", "TEX_WAVE", "TEX_MAGIC",
              "TEX_GRADIENT", "TEX_BRICK", "TEX_CHECKER"}


def grade(value, budget):
    if value > budget:
        return "FAIL"
    if value >= 0.8 * budget:
        return "WARN"
    return "PASS"


def tri_count(obj, dg):
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    ev.to_mesh_clear()
    return n


def draw_calls(mesh):
    # glTF makes one primitive, and so one draw call, per material a mesh uses
    if not mesh.polygons:
        return 0
    idx = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", idx)
    return max(1, len(set(idx)))


def image_nodes(mat):
    if not mat or not mat.use_nodes:
        return []
    return [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]


def uses_image(obj):
    return any(image_nodes(s.material) for s in obj.material_slots)


def live_procedural(mat):
    if not mat or not mat.use_nodes:
        return False
    return any(n.type in PROCEDURAL and any(o.links for o in n.outputs)
               for n in mat.node_tree.nodes)


class WebPreflightProps(PropertyGroup):
    target: EnumProperty(name="Target", default="MOBILE", items=[
        ("MOBILE", "Mobile web", "Strict budget for phones"),
        ("DESKTOP", "Desktop web", "Looser budget for desktop browsers"),
        ("CUSTOM", "Custom", "Set your own budget")])
    max_tris: IntProperty(name="Max triangles", default=100_000, min=100)
    max_draw_calls: IntProperty(name="Max draw calls", default=100, min=1)
    max_tex_mp: FloatProperty(name="Max texture megapixels", default=16.0, min=0.1)
    max_tex_dim: IntProperty(name="Max texture size", default=2048, min=16)
    selection_only: BoolProperty(name="Selected only", default=False)
    output_dir: StringProperty(name="Report folder", subtype="DIR_PATH", default="//")
    report_text: StringProperty(default="")
    verdict: StringProperty(default="")


def budget_of(p):
    if p.target == "CUSTOM":
        return {"tris": p.max_tris, "draw_calls": p.max_draw_calls,
                "tex_mp": p.max_tex_mp, "tex_dim": p.max_tex_dim}
    return dict(BUDGETS[p.target])


def is_pot(n):
    return n > 0 and (n & (n - 1)) == 0


class WEBPREFLIGHT_OT_run(Operator):
    bl_idname = "webpreflight.run"
    bl_label = "Run pre-flight"
    bl_description = "Audit the scene against a web performance budget and list the fixes"

    def execute(self, context):
        p = context.scene.web_preflight
        b = budget_of(p)
        pool = context.selected_objects if p.selection_only else context.view_layer.objects
        objs = [o for o in pool if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "No mesh objects to check")
            return {"CANCELLED"}

        dg = context.evaluated_depsgraph_get()
        tris = sorted(((tri_count(o, dg), o.name) for o in objs), reverse=True)
        calls = sorted(((draw_calls(o.data), o.name) for o in objs), reverse=True)
        total_tris = sum(t for t, _ in tris)
        total_calls = sum(c for c, _ in calls)

        mats = {s.material for o in objs for s in o.material_slots if s.material}
        images = {n.image for m in mats for n in image_nodes(m)}
        tex_mp = sum(i.size[0] * i.size[1] for i in images) / 1e6
        tex_mb = sum(i.size[0] * i.size[1] * 4 for i in images) * 1.333 / (1024 * 1024)
        bad_tex = ([f"{i.name} ({max(i.size)} px)" for i in images if max(i.size) > b["tex_dim"]]
                   + [f"{i.name} (not power of two)" for i in images
                      if i.size[0] and not (is_pot(i.size[0]) and is_pot(i.size[1]))])

        procedural = sorted(m.name for m in mats if live_procedural(m))
        non_uniform = sorted(o.name for o in objs
                             if max(abs(s - o.scale[0]) for s in o.scale) > 1e-4)
        missing_uv = sorted(o.name for o in objs if uses_image(o) and not o.data.uv_layers)
        missing_tex = sorted(i.name for i in images
                             if not i.packed_file and i.filepath
                             and not os.path.exists(bpy.path.abspath(i.filepath)))

        checks = [
            {"name": "Triangles", "status": grade(total_tris, b["tris"]),
             "value": total_tris, "budget": b["tris"],
             "offenders": [f"{n} ({t:,})" for t, n in tris[:5] if t],
             "fix": "Decimate or retopologize the heaviest meshes; web targets are low poly."},
            {"name": "Draw calls", "status": grade(total_calls, b["draw_calls"]),
             "value": total_calls, "budget": b["draw_calls"],
             "offenders": [f"{n} ({c} materials)" for c, n in calls[:5] if c > 1],
             "fix": "Each material is a draw call; merge materials and meshes or use instancing."},
            {"name": "Textures", "status": ("FAIL" if tex_mp > b["tex_mp"]
                                            else "WARN" if bad_tex or tex_mp >= 0.8 * b["tex_mp"]
                                            else "PASS"),
             "value": round(tex_mp, 1), "budget": b["tex_mp"],
             "detail": "%.0f MB in GPU memory across %d textures" % (tex_mb, len(images)),
             "offenders": bad_tex[:5],
             "fix": "Keep textures power of two and at most %d px; reduce count or resolution." % b["tex_dim"]},
            {"name": "Procedural materials", "status": "FAIL" if procedural else "PASS",
             "value": len(procedural), "budget": 0, "offenders": procedural[:5],
             "fix": "Procedural nodes do not export to glTF; bake them to image textures first."},
            {"name": "Transforms", "status": "WARN" if non_uniform else "PASS",
             "value": len(non_uniform), "budget": 0, "offenders": non_uniform[:5],
             "fix": "Apply non-uniform scale (Ctrl A); it distorts normals after export."},
            {"name": "UV maps", "status": "FAIL" if missing_uv else "PASS",
             "value": len(missing_uv), "budget": 0, "offenders": missing_uv[:5],
             "fix": "Add a UV map; textured meshes export with broken UVs otherwise."},
            {"name": "Missing textures", "status": "FAIL" if missing_tex else "PASS",
             "value": len(missing_tex), "budget": 0, "offenders": missing_tex[:5],
             "fix": "Pack textures into the .blend or relink the missing files."},
        ]
        verdict = ("FAIL" if any(c["status"] == "FAIL" for c in checks)
                   else "WARN" if any(c["status"] == "WARN" for c in checks) else "PASS")

        outdir = bpy.path.abspath(p.output_dir) or tempfile.gettempdir()
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "web_preflight_report.json"), "w", encoding="utf-8") as f:
            json.dump({"target": p.target, "budget": b, "verdict": verdict,
                       "totals": {"tris": total_tris, "draw_calls": total_calls,
                                  "materials": len(mats), "textures": len(images),
                                  "texture_megapixels": round(tex_mp, 2)},
                       "checks": checks}, f, indent=2, ensure_ascii=False)

        lines = []
        for c in checks:
            line = "[%s] %s: %s" % (c["status"], c["name"], c["value"])
            if c["budget"]:
                line += " / %s" % c["budget"]
            lines.append(line)
            if c["status"] != "PASS":
                for off in c["offenders"][:2]:
                    lines.append("    " + off)
                lines.append("    fix: " + c["fix"])
        p.report_text = "\n".join(lines)
        p.verdict = verdict
        self.report({"INFO"}, "Web preflight: %s" % verdict)
        return {"FINISHED"}


class WEBPREFLIGHT_PT_panel(Panel):
    bl_label = "Web Preflight"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Web Preflight"

    def draw(self, context):
        p = context.scene.web_preflight
        col = self.layout.column()
        col.prop(p, "target")
        if p.target == "CUSTOM":
            col.prop(p, "max_tris")
            col.prop(p, "max_draw_calls")
            col.prop(p, "max_tex_mp")
            col.prop(p, "max_tex_dim")
        col.prop(p, "selection_only")
        col.prop(p, "output_dir")
        col.separator()
        col.operator("webpreflight.run", icon="PLAY")

        if p.verdict:
            icon = {"PASS": "CHECKMARK", "WARN": "ERROR", "FAIL": "CANCEL"}.get(p.verdict, "INFO")
            box = self.layout.box()
            box.label(text="Verdict: " + p.verdict, icon=icon)
            for line in p.report_text.split("\n"):
                box.label(text=line)


classes = (WebPreflightProps, WEBPREFLIGHT_OT_run, WEBPREFLIGHT_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.web_preflight = PointerProperty(type=WebPreflightProps)


def unregister():
    del bpy.types.Scene.web_preflight
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
