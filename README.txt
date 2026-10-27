# Web Preflight (Blender add-on)

A pre-flight check for 3D artists shipping to the web. It estimates what a scene
will actually cost in the browser and flags the glTF export pitfalls that normally
only surface after you load the file, so you catch them in Blender instead.

A clean Blender viewport does not guarantee a usable web asset. The Khronos glTF
Validator checks spec conformance and the Asset Auditor checks file-size and
dimension limits, but neither estimates runtime cost. Draw calls, the main web
bottleneck, are usually discovered the hard way, in the browser.

## What it checks

Against a target budget (mobile, desktop, or custom):

- Triangles across the audited meshes.
- Draw calls, estimated as one per material a mesh uses, which is how glTF and a
  Three.js or WebGL renderer split the geometry.
- Texture megapixels and the GPU memory they take, flagging oversized and
  non power of two textures.
- Procedural node materials that will not export to glTF and need baking first.
- Non-uniform scale, which distorts normals after export.
- Meshes that use image textures but have no UV map.
- Image textures whose files are missing and are not packed.

Each finding names the objects or materials responsible and gives a one line fix.
The result is a pass, warning, or fail, shown in the sidebar and written to
`web_preflight_report.json`.

## Install

1. Edit > Preferences > Add-ons > Install, pick `web_preflight.py`, enable it.
2. Open the viewport sidebar (press N) and select the "Web Preflight" tab.
3. Choose a target, then Run pre-flight.

## Tested

A headless test passes on Blender 4.2.9 LTS:

    blender --background --factory-startup --python tests/test_preflight.py

It builds a scene that is over budget on triangles and draw calls, with a
procedural material and a non-uniform scale, and checks that each problem is
caught and the overall verdict is a fail.

## Author

Nadeschda Barenje.
