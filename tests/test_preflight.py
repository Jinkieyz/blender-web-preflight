# Headless test for the Web Preflight add-on.
#   blender --background --factory-startup --python tests/test_preflight.py
#
# Builds a scene that breaks several budgets and checks that each problem is
# caught: too many triangles, too many draw calls, a procedural material that
# will not export, and a non-uniform scale.

import bpy, os, sys, json

HERE = os.path.dirname(os.path.realpath(__file__))
ADDON_DIR = os.path.dirname(HERE)
OUT = "/tmp/webpreflight_test"
fails = []

def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sys.path.insert(0, ADDON_DIR)
    import web_preflight as wp
    wp.register()

    # heavy mesh, well over a tiny triangle budget
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4)
    bpy.context.active_object.name = "Heavy"

    # cube with two materials, so two draw calls, one of them procedural
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "MultiMat"
    plain = bpy.data.materials.new("Plain")
    proc = bpy.data.materials.new("Proc")
    proc.use_nodes = True
    nt = proc.node_tree
    noise = nt.nodes.new("ShaderNodeTexNoise")
    bsdf = nt.nodes.get("Principled BSDF")
    nt.links.new(noise.outputs["Color"], bsdf.inputs["Base Color"])
    cube.data.materials.append(plain)
    cube.data.materials.append(proc)
    for i, poly in enumerate(cube.data.polygons):
        poly.material_index = i % 2

    # a non-uniformly scaled object
    bpy.ops.mesh.primitive_cube_add()
    scaled = bpy.context.active_object
    scaled.name = "Scaled"
    scaled.scale = (2.0, 1.0, 1.0)

    p = bpy.context.scene.web_preflight
    p.target = "CUSTOM"
    p.max_tris = 100
    p.max_draw_calls = 1
    p.max_tex_dim = 256
    p.max_tex_mp = 0.1
    p.output_dir = OUT
    p.selection_only = False

    print("operator result:", bpy.ops.webpreflight.run())

    report_file = os.path.join(OUT, "web_preflight_report.json")
    check(os.path.exists(report_file), "report json written")
    if os.path.exists(report_file):
        with open(report_file, encoding="utf-8") as f:
            report = json.load(f)
        print(json.dumps(report, indent=2))
        st = {c["name"]: c["status"] for c in report["checks"]}
        check(report["verdict"] == "FAIL", "overall verdict is FAIL")
        check(st.get("Triangles") == "FAIL", "too many triangles caught")
        check(st.get("Draw calls") == "FAIL", "too many draw calls caught")
        check(st.get("Procedural materials") == "FAIL", "procedural material caught")
        check(st.get("Transforms") == "WARN", "non-uniform scale caught")
        check(report["totals"]["draw_calls"] == 4, "draw calls counted correctly (4)")

    print("\n=== RESULT ===")
    if fails:
        print("FAILED:", len(fails))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
