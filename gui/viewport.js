// 3D viewport — loads a skin's meshes (decoded to glTF by AtelierMesh/CUE4Parse), renders them
// in rest pose with three.js, and tints each part from its MI material params with live recolor.
// The glTF names every material slot after its real MI (e.g. MI_1028500_Body), so mesh parts map
// straight to the game's material instances — no guessing.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader }    from 'three/addons/loaders/GLTFLoader.js';

const $ = id => document.getElementById(id);
const loader    = new GLTFLoader();
const texLoader = new THREE.TextureLoader();
const texCache  = new Map();   // texGameRel -> THREE.Texture, shared/reused across opens (never re-uploaded)

let renderer, scene, camera, controls, grid, modelRoot;
let raf = 0, inited = false;
let generation = 0;   // bumped on every clear/open; async texture loads bail if it changed under them

// ── material state (per open) ─────────────────────────────────────────────────
let matsByName = {};   // "MI_..." -> [THREE.Material, ...]   (mesh materials sharing that slot)
let matData    = {};   // "MI_..." -> {game_rel, colors:[{name,rgba}], scalars, baseIdx}
let edited      = {};  // "MI_..." -> { paramName: [r,g,b,a] }  (unsaved session edits)
let currentSkin = null;
let objects     = [];  // [{ name, root: THREE.Object3D }] — one entry per top-level mesh added to modelRoot

const clamp01 = v => Math.min(1, Math.max(0, v));

// UE material colors are LINEAR floats; <input type=color> is sRGB. Convert via THREE.Color.
function linToHex(rgba) {
  const c = new THREE.Color();
  c.setRGB(clamp01(rgba[0]), clamp01(rgba[1]), clamp01(rgba[2]), THREE.LinearSRGBColorSpace);
  return '#' + c.getHexString(THREE.SRGBColorSpace);
}
function hexToLin(hex) {
  const c = new THREE.Color().setStyle(hex, THREE.SRGBColorSpace);
  return [c.r, c.g, c.b];  // linear working space
}

function pickBaseIdx(colors) {
  if (!colors || !colors.length) return -1;
  const score = n => {
    n = (n || '').toLowerCase();
    if (/emissive|glow|spec|rim|fresnel|subsurf|sss|ambient|shadow|_ao\b/.test(n)) return -10;
    if (/base.?color|albedo|diffuse/.test(n)) return 6;
    if (/main.?color|body.?color|skin.?color/.test(n)) return 5;
    if (/^color$|_color\b|tint/.test(n)) return 4;
    if (/color/.test(n)) return 2;
    return 0;
  };
  let bi = 0, bs = score(colors[0].name);
  for (let i = 1; i < colors.length; i++) { const s = score(colors[i].name); if (s > bs) { bs = s; bi = i; } }
  return bs >= 4 ? bi : -1;   // only tint from a genuine base/tint/color param; else leave the map/grey
}

// The emissive glow is Emissive-mask-texture × EmissiveColor param × EmissiveStrength scalar — a
// separate channel from base colour. Recolouring the glow means changing EmissiveColor, applied
// through the emissive map, NOT multiplying the whole part's albedo.
const EMISSIVE_RE = /emiss|glow/i;
function pickEmissiveIdx(colors) {
  if (!colors) return -1;
  return colors.findIndex(c => EMISSIVE_RE.test(c.name || ''));
}
function paramRgb(name, idx) {
  const d = matData[name];
  if (!d || idx < 0 || !d.colors[idx]) return null;
  const p = d.colors[idx];
  const rgba = (edited[name] && edited[name][p.name]) || p.rgba;
  return [clamp01(rgba[0]), clamp01(rgba[1]), clamp01(rgba[2])];
}
function scalarVal(name, re) {
  const d = matData[name]; if (!d) return null;
  const s = (d.scalars || []).find(x => re.test(x.name || ''));
  return s ? s.value : null;
}

function init() {
  if (inited) return;
  const canvas = $('viewport-canvas');
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  scene  = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1e6);

  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.1));
  const key  = new THREE.DirectionalLight(0xffffff, 1.7); key.position.set(1, 2, 1.5);   scene.add(key);
  const fill = new THREE.DirectionalLight(0xaaccff, 0.5); fill.position.set(-1.5, 0.6, -1); scene.add(fill);
  const rim  = new THREE.DirectionalLight(0xffffff, 0.6); rim.position.set(0, 1, -2);     scene.add(rim);

  grid = new THREE.GridHelper(400, 20, 0x3a5575, 0x252533);
  scene.add(grid);

  modelRoot = new THREE.Group();
  scene.add(modelRoot);

  $('viewport-mat-save').addEventListener('click', saveEdits);
  $('viewport-mat-reset').addEventListener('click', revertEdits);

  window.addEventListener('resize', onResize);
  inited = true;
}

function onResize() {
  if (!renderer) return;
  const c = $('viewport-canvas');
  const w = c.clientWidth, h = c.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  raf = requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function disposeTree(obj) {
  // Dispose per-load geometry + materials. Do NOT dispose material.map here: our maps all come from
  // texCache and are reused across opens; disposing them would force re-upload every open (the GPU
  // churn that froze the desktop). Cached textures live for the session — a small, bounded set.
  obj.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose());
  });
}

function clearModel() {
  generation++;   // invalidate any in-flight texture loads from the previous model
  for (const ch of [...modelRoot.children]) { modelRoot.remove(ch); disposeTree(ch); }
  matsByName = {}; matData = {}; edited = {}; currentSkin = null; objects = [];
  const list = $('viewport-mat-list'); if (list) list.innerHTML = '';
  const objList = $('viewport-obj-list'); if (objList) objList.innerHTML = '';
  updateSidebarVisibility();
}

// Sidebar shows the Objects panel and/or Materials panel independently, and hides itself entirely
// (full canvas width) only when there's nothing to show in either.
function updateSidebarVisibility() {
  const hasObjects = objects.length > 0;
  const hasMats    = Object.keys(matData).length > 0;
  $('viewport-obj-section').style.display = hasObjects ? 'flex' : 'none';
  $('viewport-mat-section').style.display = hasMats ? 'flex' : 'none';
  $('viewport-materials').classList.toggle('empty', !hasObjects && !hasMats);
}

// One row per top-level mesh loaded into modelRoot. Toggling hides/shows that mesh and re-frames
// the camera — some skins carry extra objects (unused attachments, effects meshes, etc.) that are
// wildly oversized or offset, which blows up the auto-framed scale and makes orbit/pan feel broken.
function buildObjectPanel() {
  const list = $('viewport-obj-list');
  list.innerHTML = '';
  for (const o of objects) {
    const row = document.createElement('div');
    row.className = 'vp-obj' + (o.root.visible ? '' : ' hidden-obj');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = o.root.visible;
    cb.addEventListener('change', () => {
      o.root.visible = cb.checked;
      row.classList.toggle('hidden-obj', !cb.checked);
      frameCamera();
    });
    const lbl = document.createElement('span');
    lbl.textContent = o.name || 'Object';
    lbl.title = o.name || '';
    row.appendChild(cb); row.appendChild(lbl);
    list.appendChild(row);
  }
  $('viewport-obj-count').textContent = objects.length + (objects.length === 1 ? ' object' : ' objects');
  updateSidebarVisibility();
}

// Make a mesh material matte (glTF's default is fully metallic → renders black w/o env map) and
// register it under its MI slot name so we can tint it from material params later.
function prepMaterial(m) {
  if (!m || !m.isMeshStandardMaterial) return;
  m.metalness = 0.0;
  m.roughness = 0.85;
  m.color.setHex(0xb8b8c0);
  // MR meshes carry COLOR_0 vertex colors that are mostly (0,0,0) mask/data channels — NOT display
  // colors. GLTFLoader auto-enables vertexColors, so the shader multiplies texture × tint × black =
  // pure black. Ignore them so the albedo map and tint actually show.
  m.vertexColors = false;
  m.needsUpdate = true;
  const nm = m.name || '';
  if (nm) (matsByName[nm] || (matsByName[nm] = [])).push(m);
}

function collectMats(root) {
  root.traverse(o => {
    if (!o.isMesh) return;
    o.frustumCulled = false;
    for (const m of (Array.isArray(o.material) ? o.material : [o.material])) prepMaterial(m);
  });
}

// Set each part's color: the base/tint param if there is one (multiplies over the map), else white
// when a texture map is present, else the matte grey placeholder.
function retint(name) {
  const d = matData[name]; if (!d) return;
  const baseRgb = paramRgb(name, d.baseIdx);
  const emiRgb  = paramRgb(name, d.emiIdx);
  const emiStr  = scalarVal(name, /emiss.*stre|glow.*stre|emissivestrength/i);
  for (const m of (matsByName[name] || [])) {
    // albedo tint (base colour param, else white over the map / grey placeholder)
    if (baseRgb) m.color.setRGB(baseRgb[0], baseRgb[1], baseRgb[2], THREE.LinearSRGBColorSpace);
    else m.color.setHex(m.map ? 0xffffff : 0xb8b8c0);
    // emissive glow: EmissiveColor tints the emissive mask (only meaningful once the map is applied)
    if (m.emissiveMap) {
      if (emiRgb) m.emissive.setRGB(emiRgb[0], emiRgb[1], emiRgb[2], THREE.LinearSRGBColorSpace);
      else m.emissive.setHex(0xffffff);
      m.emissiveIntensity = emiStr != null ? Math.min(3, Math.max(0.4, emiStr * 0.03)) : 1.2;
    }
  }
}

async function applyMatTexture(name, texGameRel, slot = 'map', ver = '0') {
  // Textures are big (2048²) and expensive to upload — load each once, cache, and reuse across opens.
  // We apply to whatever model is CURRENT (matsByName), so no generation-dispose race: a load that
  // finishes after the user switched meshes simply applies to the current model's slot of that name
  // (or nothing if it isn't present). This kills both the "white mesh" race and the GPU churn.
  // The cache key + URL include `ver` (edited-PNG mtime): unedited textures stay cached across opens,
  // but once the user edits one its stamp changes → we refetch and show the edit.
  ver = ver || '0';
  const key = texGameRel + '@' + ver;
  let tex = texCache.get(key);
  if (!tex) {
    try {
      tex = await texLoader.loadAsync(`/api/texture_png?game_rel=${encodeURIComponent(texGameRel)}&v=${encodeURIComponent(ver)}`);
    } catch (e) { console.warn('viewport: texture load failed', name, slot, texGameRel, e); return; }
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.flipY = false;                              // glTF UV convention (origin top-left)
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    if (renderer) tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    texCache.set(key, tex);
  }
  const mats = matsByName[name] || [];
  if (!mats.length) return;   // the current model has no slot of this name (user switched meshes)
  for (const m of mats) { m[slot] = tex; m.needsUpdate = true; }
  retint(name);   // re-derive colour/emissive now that the map exists
}

function frameCamera() {
  // Frame only visible top-level objects — Box3 doesn't respect .visible on its own, and a toggled-
  // off mesh (e.g. an oversized stray) would otherwise still blow out the auto-framed scale.
  const box = new THREE.Box3();
  for (const child of modelRoot.children) { if (child.visible) box.expandByObject(child); }
  if (box.isEmpty()) return;
  const size   = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const dist   = (maxDim / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)))) * 1.5;
  camera.near = maxDim / 200;
  camera.far  = maxDim * 100;
  camera.position.set(center.x + dist * 0.55, center.y + dist * 0.15, center.z + dist);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
  grid.position.y = box.min.y;
  grid.scale.setScalar(Math.max(1, maxDim / 200));
}

// ── materials: fetch params, tint mesh parts, build the recolor panel ─────────
// Runs fully in the background: the mesh is already up and orbitable (viewport-loading is long
// gone by the time this is called), so we surface progress with a toast spinner — same as every
// other long-running job in the app — instead of the blocking mesh-load overlay. The sidebar
// (#viewport-materials) appears as soon as params are back; textures fill in live on whatever
// model is current, no reopen needed — this just keeps the user informed while that happens.
async function loadMaterials(skinId) {
  if (!skinId) return;
  currentSkin = skinId;
  const gen = generation;
  const slots = Object.keys(matsByName);
  const spinner = typeof window.toastSpinner === 'function' ? window.toastSpinner('Loading materials…') : null;
  const spinnerMsg = spinner ? spinner.querySelector('span') : null;

  let res;
  try {
    res = await fetch('/api/skin_materials', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skin_id: skinId, names: slots }),
    }).then(r => r.json());
  } catch (e) {
    console.warn('viewport: skin_materials request failed', e);
    if (spinner) spinner.remove();
    return;
  }
  if (gen !== generation) { if (spinner) spinner.remove(); return; }   // model changed while fetching params
  if (!res || !res.ok || !res.materials) {
    console.warn('viewport: skin_materials bad response', res);
    if (spinner) spinner.remove();
    return;
  }

  // Keep only materials that actually appear as slots on the loaded mesh; wire each part.
  let matched = 0;
  const texJobs = [];
  for (const [name, info] of Object.entries(res.materials)) {
    if (!matsByName[name]) continue;
    matched++;
    info.baseIdx = pickBaseIdx(info.colors);
    info.emiIdx  = pickEmissiveIdx(info.colors);
    matData[name] = info;
    retint(name);
    const tex = info.textures || {};
    const ver = info.tex_ver || {};
    if (tex.BaseColor) texJobs.push(applyMatTexture(name, tex.BaseColor, 'map', ver[tex.BaseColor]));       // albedo
    if (tex.Emissive)  texJobs.push(applyMatTexture(name, tex.Emissive, 'emissiveMap', ver[tex.Emissive])); // glow mask
  }
  buildPanel();   // sidebar shows up now — params are extracted, even though textures are still in flight

  if (!texJobs.length) { if (spinner) spinner.remove(); return; }

  // All texture fetches are already in flight together (fired above, not awaited one at a time) —
  // the spinner just reports on that single batch as it lands, part-by-part, until every texture
  // for this model has been decoded and applied.
  const total = texJobs.length;
  let done = 0;
  const tick = () => { if (spinnerMsg && gen === generation) spinnerMsg.textContent = `Loading textures… ${done}/${total}`; };
  tick();
  await Promise.allSettled(texJobs.map(p => p.then(() => { done++; tick(); })));
  if (spinner) spinner.remove();
  if (gen === generation) $('viewport-status').textContent = `${matched} part${matched !== 1 ? 's' : ''}`;
}

function buildPanel() {
  const list = $('viewport-mat-list');
  list.innerHTML = '';
  const names = Object.keys(matData).sort();

  for (const name of names) {
    const d = matData[name];
    if (!d.colors || !d.colors.length) continue;
    const wrap = document.createElement('div');
    wrap.className = 'vp-mat';
    const h = document.createElement('div');
    h.className = 'vp-mat-name'; h.textContent = name;
    wrap.appendChild(h);
    d.colors.forEach((col, idx) => {
      const row = document.createElement('div');
      const kind = idx === d.emiIdx ? ' emissive' : (idx === d.baseIdx ? ' base' : '');
      row.className = 'vp-param' + kind;
      const inp = document.createElement('input');
      inp.type = 'color';
      inp.value = linToHex(col.rgba);
      inp.addEventListener('input', () => onColorInput(name, col.name, inp.value));
      const lbl = document.createElement('span');
      lbl.textContent = col.name; lbl.title = col.name + (idx === d.emiIdx ? '  (glow — previews live)' : idx === d.baseIdx ? '  (base tint — previews live)' : '');
      row.appendChild(inp); row.appendChild(lbl);
      wrap.appendChild(row);
    });
    list.appendChild(wrap);
  }
  $('viewport-mat-count').textContent = names.length + (names.length === 1 ? ' material' : ' materials');
  updateSidebarVisibility();
  refreshFoot();
}

function onColorInput(matName, paramName, hex) {
  const [r, g, b] = hexToLin(hex);
  const d = matData[matName];
  const orig = (d.colors.find(c => c.name === paramName) || {}).rgba || [r, g, b, 1];
  (edited[matName] || (edited[matName] = {}))[paramName] = [r, g, b, orig[3] ?? 1];
  // retint re-derives the mesh from the params, routing each to its real channel: EmissiveColor →
  // the emissive glow (through the emissive mask), a base/albedo tint → material.color. Editing the
  // emissive swatch recolours the glow; other params are saved but don't have a faithful preview.
  retint(matName);
  refreshFoot();
}

function refreshFoot() {
  const n = Object.values(edited).reduce((a, m) => a + Object.keys(m).length, 0);
  const save = $('viewport-mat-save');
  save.disabled = n === 0;
  save.textContent = n ? `Save recolors (${n})` : 'Save recolors';
}

async function saveEdits() {
  const names = Object.keys(edited).filter(n => Object.keys(edited[n]).length);
  if (!names.length) return;
  const save = $('viewport-mat-save');
  save.disabled = true; save.textContent = 'Saving…';
  let ok = 0;
  for (const name of names) {
    const d = matData[name];
    try {
      const r = await fetch('/api/material_save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game_rel: d.game_rel, colors: edited[name], scalars: {} }),
      }).then(r => r.json());
      if (r && r.ok) { ok++; d.colors = r.colors; }  // adopt persisted params as the new baseline
    } catch (e) { console.warn('viewport: material_save failed', name, e); }
  }
  edited = {};
  note(ok ? `Saved recolors to ${ok} material${ok !== 1 ? 's' : ''}` : 'Save failed', ok ? 'success' : 'warning');
  refreshFoot();
}

function revertEdits() {
  edited = {};
  for (const name of Object.keys(matData)) retint(name);   // no edits left → uses originals
  buildPanel();                                            // resets the pickers too
  note('Reverted unsaved changes', 'info');
}

function note(msg, kind) {
  if (typeof window.toast === 'function') window.toast(msg, kind);
  $('viewport-status').textContent = msg;
}

function skinFromGameRel(gr) {
  const m = /characters\/\d+\/(\d+)\//i.exec(gr || '');
  return m ? m[1] : null;
}

async function open(skinId, title) {
  init();
  $('viewport-title').textContent  = title || `3D Preview — ${skinId}`;
  $('viewport-status').textContent = '';
  $('viewport-overlay').classList.add('active');
  $('viewport-loading').style.display = 'flex';
  requestAnimationFrame(onResize);
  if (!raf) animate();
  clearModel();

  try {
    const meshes = await fetch(`/api/skin_meshes?skin_id=${encodeURIComponent(skinId)}`).then(r => r.json());
    if (!Array.isArray(meshes) || !meshes.length) {
      $('viewport-loading-msg').textContent = 'No meshes found for this skin.';
      return;
    }
    let loaded = 0;
    for (let i = 0; i < meshes.length; i++) {
      $('viewport-loading-msg').textContent =
        `Decoding ${meshes[i].name} (${i + 1}/${meshes.length})… first time can take a moment`;
      try {
        const gltf = await loader.loadAsync(`/api/model_gltf?game_rel=${encodeURIComponent(meshes[i].game_rel)}`);
        collectMats(gltf.scene);
        modelRoot.add(gltf.scene);
        objects.push({ name: meshes[i].name, root: gltf.scene });
        loaded++;
        frameCamera();
      } catch (e) { console.warn('viewport: mesh load failed', meshes[i].name, e); }
    }
    $('viewport-status').textContent = loaded ? `${loaded} part${loaded !== 1 ? 's' : ''}` : '';
    if (!loaded) { $('viewport-loading-msg').textContent = 'Failed to load meshes.'; return; }
    buildObjectPanel();
    frameCamera();
    $('viewport-loading').style.display = 'none';
    loadMaterials(skinId);
  } catch (e) {
    $('viewport-loading-msg').textContent = 'Error: ' + e.message;
  }
}

async function openMesh(gameRel, name, skinId) {
  init();
  $('viewport-title').textContent  = name || '3D Preview';
  $('viewport-status').textContent = '';
  $('viewport-overlay').classList.add('active');
  $('viewport-loading').style.display = 'flex';
  $('viewport-loading-msg').textContent = `Decoding ${name || 'mesh'}… first time can take a moment`;
  requestAnimationFrame(onResize);
  if (!raf) animate();
  clearModel();
  try {
    const gltf = await loader.loadAsync(`/api/model_gltf?game_rel=${encodeURIComponent(gameRel)}`);
    collectMats(gltf.scene);
    modelRoot.add(gltf.scene);
    objects.push({ name: name || 'Mesh', root: gltf.scene });
    buildObjectPanel();
    frameCamera();
    $('viewport-status').textContent = name || '';
    $('viewport-loading').style.display = 'none';
    loadMaterials(skinId || skinFromGameRel(gameRel));
  } catch (e) {
    $('viewport-loading-msg').textContent = 'Failed to load: ' + (e && e.message || e);
  }
}

function close() {
  $('viewport-overlay').classList.remove('active');
  if (raf) { cancelAnimationFrame(raf); raf = 0; }   // stop rendering while hidden (frees the GPU)
}

$('viewport-close').addEventListener('click', close);
$('viewport-overlay').addEventListener('click', e => { if (e.target.id === 'viewport-overlay') close(); });
window.addEventListener('keydown', e => {
  if (e.key === 'Escape' && $('viewport-overlay').classList.contains('active')) close();
});

window.AtelierViewport = { open, openMesh, close };
