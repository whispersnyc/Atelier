viewport meshes seem horizontally compressed a little
optimize 3d view re-open
diagnose import/export times (ui mods for LQ using UAT fallback?)
test pak override order, maybe write a test that checks cases where two patches override the same base asset to ensure latest patch's item is used

### regular
bugs: preview thumbnails sometimes infinitely load until revisit/triggered refresh, all refreshes reset edited asset thumbnails to original
keep reset data button updated during expansion
confirmation before export files override files with same name
descriptive spinner text during initial (extra long loading for index)
replace on-boot win11 toast with extremely fast lightweight splash screen
hovering over item in sidebar should show tooltip with pak name + full path (cache index)
toggle select/deselect all when clicking the sidebar's circled number

### partially formed ideas
better filetype classification system?
allow multiple copies of texture files in sidebar, export only allows one of each texture to be exported at a time (still developing this idea, in the form of a completely optional advanced mode with mod profiles)
im pretty sure theres more that im forgetting rn

### 3D viewport (skin preview) — new, for whoever picks this up
Bottom-center "3D View" FAB opens an overlay that renders a skin's meshes in rest pose (static, no anim).
- decode: `Tools/AtelierMesh/AtelierMesh.exe` (self-contained CUE4Parse build) converts MR meshes -> glTF (.glb). REQUIRED at runtime with `oo2core_9_win64.dll` + `CUE4Parse-Natives.dll` beside it. Its C# source lives outside this tree (`_viewport/AtelierMesh`, refs a CUE4Parse *master* clone — NuGet rejects MR's usmap v4). glb exported geometry-only (ExportMaterials=false) to dodge the detex native dep.
- backend: `atelier/tools.py::atelier_mesh()`, routes in `atelier/web/routes.py`: `/api/skin_meshes`, `/api/model_gltf` (glb cached in `_cache/gltf`), `/api/skin_materials`, `/api/texture_png`. mesh classification added in `atelier/web/browse.py`; MI texture refs resolved in `atelier/handlers/material.py::_mat_textures` (read_material now also returns `textures`).
- frontend: `gui/viewport.js` (ES module, `window.AtelierViewport`), overlay markup in `gui/index.html`, styles in `gui/style.css`. three.js r185 vendored at `gui/vendor/three` — module build needs BOTH `three.module.min.js` AND `three.core.min.js` plus `jsm/` (GLTFLoader/OrbitControls + their utils).
- material model: the glTF names every slot after its MI, so mesh parts map straight to game materials. base albedo = `BaseColor` texture -> `material.map`; glow = `Emissive` mask texture -> `emissiveMap`, tinted by the `EmissiveColor` param * `EmissiveStrength`. live recolor: `EmissiveColor` swatch -> glow, base/albedo tint -> `material.color`; Save writes the same MI JSON the build consumes (other params save but have no faithful preview).
- GOTCHAS (cost real debugging time): (1) MR meshes carry `COLOR_0` vertex colors = mask/data, mostly (0,0,0); GLTFLoader auto-enables `vertexColors` -> everything multiplies to BLACK -> we force `material.vertexColors=false`. (2) textures are 2048^2 and heavy -> `texCache` loads each once and reuses across opens (never dispose the shared textures); don't reintroduce per-open loads or WebView2's GPU chokes/freezes. (3) `/api/texture_png` PREFERS the user's edited PNG in the active project over vanilla; `tex_ver` (edited mtime) is threaded into the texture URL + cache key so an edit invalidates and shows on viewport reopen.
- DEBUG builds: a file named `DEBUG` next to the exe turns on webview devtools (`window.py`). Shipped builds have no `DEBUG`.
- decided NOT to do: Normal/ORM maps (works, but more 2048^2 uploads than WebView2 wants — skipped for GPU headroom); VFX/Niagara + material flow effects (needs the game's shader HLSL/bytecode we don't have — can't reproduce faithfully, only the static emissive approximation).