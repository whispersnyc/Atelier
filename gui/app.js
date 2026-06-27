"use strict";

// ── icons ─────────────────────────────────────────────────────────────────────
lucide.createIcons();

// ── state ─────────────────────────────────────────────────────────────────────
const NAV_ROOT    = { path: "" };
let   nav         = { ...NAV_ROOT };
let   history     = [{ ...NAV_ROOT }];
let   histIdx     = 0;
let   allItems    = [];      // current grid items (raw from server)
let   sidebarData = {};      // token -> {name, game_rel, skin_id, char_name, skin_name, selected}
let   importing   = false;
let   pendingImport = null;
let   pendingClear  = null;
let   pendingImportAll = null;
let   suppressChangeToastUntil = Date.now() + 5000;
let   suppressedImportGameRels = new Set();
let   _pathLabels = {};      // "Characters/1234" -> "1234 — Spider-Man" (cached from browse results)

// ── handler registry ──────────────────────────────────────────────────────────
const ASSET_HANDLERS = {
  texture:  { import_endpoint: "/api/import_texture",  preview: true,  icon: "image"        },
  material: { import_endpoint: "/api/import_material", preview: false, icon: "circle-star"  },
  vfx:      { import_endpoint: "/api/import_vfx",      preview: false, icon: "sparkles"     },
};
function handlerFor(ft) { return ASSET_HANDLERS[ft] || { import_endpoint: "/api/import", preview: false, icon: "file-question" }; }

const ASSET_ICON_CLS = {
  texture:  "texture-icon",
  vfx:      "vfx-icon",
  material: "material-icon",
};
function assetIconCls(ft) { return ASSET_ICON_CLS[ft] || "unhandled-icon"; }

const FOLDER_ICON_PATTERNS = [
  [/^characters?$/i,    "char-icon"],
  [/^ui$/i,             "ui-folder-icon"],
  [/^textures?$/i,      "texture-folder-icon"],
  [/^materials?$/i,     "material-folder-icon"],
  [/^(vfx|effects?)$/i, "vfx-folder-icon"],
];
function folderIconCls(name) {
  const hit = FOLDER_ICON_PATTERNS.find(([re]) => re.test(name));
  return hit ? hit[1] : "folder-icon";
}

const ICON_CLS_TO_LUCIDE = {
  "folder-icon":          "folder",
  "texture-folder-icon":  "image",
  "material-folder-icon": "circle-star",
  "vfx-folder-icon":      "sparkles",
  "char-icon":            "square-user-round",
  "ui-folder-icon":       "swatch-book",
  "texture-icon":         "image",
  "vfx-icon":             "sparkles",
  "material-icon":        "circle-star",
  "unhandled-icon":       "file-question",
};

// ── helpers ───────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  return res.json();
}

function toastSpinner(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<div class="spinner"></div><span>${msg}</span>`;
  document.getElementById("toast-area").appendChild(el);
  return el;
}

function toast(msg, type = "info", duration = 3200) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  const icon = type === "success" ? "check-circle"
             : type === "warning" ? "alert-triangle"
             : "info";
  el.innerHTML = `<i data-lucide="${icon}" size="14"></i><span>${msg}</span>`;
  document.getElementById("toast-area").appendChild(el);
  lucide.createIcons({ nodes: [el] });
  setTimeout(() => el.remove(), duration);
}

function setStatus(msg) {
  document.getElementById("status-msg").textContent = msg;
}

function skinIdFromPath(path) {
  const m = (path || "").match(/^Characters\/\d{4}\/(\d{7})/i);
  return m ? m[1] : null;
}

// ── navigation ────────────────────────────────────────────────────────────────
function pushNav(newNav) {
  history = history.slice(0, histIdx + 1);
  history.push({ ...newNav });
  histIdx = history.length - 1;
  nav = { ...newNav };
  updateNavBtns();
  document.getElementById("search-input").value = "";
  renderGrid();
  renderBreadcrumbs();
  renderSidebar();
}

function updateNavBtns() {
  document.getElementById("btn-back").disabled    = histIdx <= 0;
  document.getElementById("btn-forward").disabled = histIdx >= history.length - 1;
}

document.getElementById("btn-back").addEventListener("click", () => {
  if (histIdx <= 0) return;
  histIdx--;
  nav = { ...history[histIdx] };
  document.getElementById("search-input").value = "";
  updateNavBtns();
  renderGrid();
  renderBreadcrumbs();
  renderSidebar();
});
document.getElementById("btn-forward").addEventListener("click", () => {
  if (histIdx >= history.length - 1) return;
  histIdx++;
  nav = { ...history[histIdx] };
  document.getElementById("search-input").value = "";
  updateNavBtns();
  renderGrid();
  renderBreadcrumbs();
  renderSidebar();
});

// ── breadcrumbs ───────────────────────────────────────────────────────────────
function renderBreadcrumbs() {
  const bc = document.getElementById("breadcrumbs");
  bc.innerHTML = "";

  const crumb = (label, navState) => {
    const el = document.createElement("span");
    el.className = "crumb" + (navState === null ? " active" : "");
    el.textContent = label;
    if (navState !== null) el.addEventListener("click", () => pushNav(navState));
    return el;
  };
  const sep = () => {
    const el = document.createElement("span");
    el.className = "sep";
    el.innerHTML = '<i data-lucide="chevron-right" size="12"></i>';
    return el;
  };

  const parts = nav.path ? nav.path.split("/") : [];
  bc.appendChild(crumb("Import", parts.length > 0 ? { path: "" } : null));

  let accumulated = "";
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    accumulated = accumulated ? `${accumulated}/${part}` : part;
    const isLast = i === parts.length - 1;
    const label  = _pathLabels[accumulated] || part;
    bc.appendChild(sep());
    bc.appendChild(crumb(label, isLast ? null : { path: accumulated }));
  }

  lucide.createIcons({ nodes: [bc] });
}

// ── grid rendering ────────────────────────────────────────────────────────────
function _makeCard(item) {
  if (item.type === "folder") {
    return {
      type:    "folder",
      label:   item.label || item.name,
      iconCls: folderIconCls(item.name),
      onClick: () => pushNav({ path: item.rel_path }),
    };
  }
  const ft = item.file_type || "other";
  return {
    type:      "asset",
    file_type: ft,
    label:     item.name,
    iconCls:   assetIconCls(ft),
    imported:  item.imported,
    token:     item.token,
    game_rel:  item.game_rel,
    rel_path:  item.rel_path,
    onClick:   () => handleAssetClick(item),
  };
}

async function renderGrid() {
  const area = document.getElementById("grid-area");
  area.innerHTML = '<div id="empty-state"><div class="spinner" style="margin:0 auto 12px"></div><div>Loading…</div></div>';
  document.getElementById("import-all-btn").disabled = true;

  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(nav.path || "")}`);
    if (data.error) throw new Error(data.error);
    allItems = data;

    // Cache folder labels for breadcrumbs
    for (const item of data) {
      if (item.type === "folder" && item.label && item.label !== item.name) {
        _pathLabels[item.rel_path] = item.label;
      }
    }

    buildGrid(data.map(_makeCard));

    const importable = data.filter(d => d.type === "asset" && d.file_type === "texture");
    document.getElementById("import-all-btn").disabled = importable.length === 0;

    const unimportedTextures = data.filter(d => d.type === "asset" && d.file_type === "texture" && !d.imported);
    if (unimportedTextures.length) {
      try {
        const pf = await api("/api/prefetch_thumbs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ game_rels: unimportedTextures.map(t => t.game_rel) }),
        });
        (pf.cached || []).forEach(gr => {
          document.querySelectorAll(`img[data-game-rel="${CSS.escape(gr)}"]`).forEach(img => {
            img.src = `/api/thumb?game_rel=${encodeURIComponent(gr)}`;
          });
        });
      } catch (_) {}
    }
  } catch (e) {
    area.innerHTML = `<div id="empty-state" style="color:var(--acc)">${e.message}</div>`;
  }
}

function buildGrid(cards) {
  const area = document.getElementById("grid-area");
  const q = document.getElementById("search-input").value.trim().toLowerCase();

  const filtered = q ? cards.filter(c => c.label.toLowerCase().includes(q)) : cards;

  if (!filtered.length) {
    area.innerHTML = `<div id="empty-state">
      <i data-lucide="${q ? "search-x" : "folder-open"}" size="32" style="color:var(--muted)"></i>
      <div style="margin-top:8px">${q ? "No matches" : "Empty folder"}</div>
    </div>`;
    lucide.createIcons({ nodes: [area] });
    return;
  }

  const grid = document.createElement("div");
  grid.className = "grid";

  filtered.forEach(card => {
    const el = document.createElement("div");
    el.className = "card" + (card.imported ? " imported" : "");
    el.title = card.label;

    const thumb = document.createElement("div");
    thumb.className = "card-thumb";

    if (card.type === "asset" && handlerFor(card.file_type).preview && card.game_rel) {
      const img = document.createElement("img");
      img.dataset.gameRel = card.game_rel;
      img.alt = card.label;
      if (card.imported) {
        if (card.token) img.dataset.token = card.token;
        img.src    = `/api/thumb?game_rel=${encodeURIComponent(card.game_rel)}`;
        img.onerror = () => {
          img.style.display = "none";
          const icon = makeIcon(card);
          thumb.appendChild(icon);
          lucide.createIcons({ nodes: [thumb] });
        };
      } else {
        img.style.display = "none";
        const spin = document.createElement("div");
        spin.className = "spinner";
        thumb.appendChild(spin);
        img.onload  = () => { img.style.display = ""; spin.style.display = "none"; };
        img.onerror = () => {
          img.style.display = "none";
          spin.replaceWith(makeIcon(card));
          lucide.createIcons({ nodes: [thumb] });
        };
      }
      thumb.appendChild(img);
    } else {
      thumb.appendChild(makeIcon(card));
    }

    const name = document.createElement("div");
    name.className = "card-name";
    name.textContent = card.label;

    el.appendChild(thumb);
    el.appendChild(name);
    el.addEventListener("click", card.onClick);
    if (card.type === "asset") {
      el.addEventListener("contextmenu", e => _ctxShow(e, _ctxItemsCard(card)));
    }
    grid.appendChild(el);
  });

  area.innerHTML = "";
  area.appendChild(grid);
  lucide.createIcons({ nodes: [grid] });
}

function makeIcon(card) {
  const i = document.createElement("i");
  i.dataset.lucide = ICON_CLS_TO_LUCIDE[card.iconCls] || "file-question";
  i.className = `card-icon ${card.iconCls || ""}`;
  i.setAttribute("size", "40");
  return i;
}

// ── search ────────────────────────────────────────────────────────────────────
document.getElementById("search-input").addEventListener("input", () => {
  if (allItems.length) {
    buildGrid(allItems.map(_makeCard));
  }
  renderSidebar();
});

document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key === "f") {
    e.preventDefault();
    const si = document.getElementById("search-input");
    si.focus();
    si.select();
  }
});

// ── asset click / single import ───────────────────────────────────────────────
function handleImportedFileAction(item) {
  const ft = item.file_type || "texture";
  switch (ft) {
    case "material":
      openMaterialEditor(item);
      return;
    default:
      fetch(`/api/open_with?game_rel=${encodeURIComponent(item.game_rel)}`);
  }
}

function handleAssetClick(item) {
  if (item.imported && item.token) {
    handleImportedFileAction(item);
    return;
  }
  const ft   = item.file_type || "other";
  const kind = ft.charAt(0).toUpperCase() + ft.slice(1);
  const sid  = skinIdFromPath(nav.path);
  document.getElementById("confirm-title").textContent = `Edit ${kind}?`;
  document.getElementById("confirm-msg").textContent =
    `Edit ${ft} "${item.name}"${sid ? ` from skin ${sid}` : ""}?`;
  pendingImport = { skin_id: sid, rel_path: item.rel_path, game_rel: item.game_rel, name: item.name, file_type: ft };
  document.getElementById("confirm-overlay").classList.add("active");
}

document.getElementById("confirm-cancel").addEventListener("click", () => {
  document.getElementById("confirm-overlay").classList.remove("active");
  pendingImport = null;
});

document.getElementById("confirm-ok").addEventListener("click", async () => {
  document.getElementById("confirm-overlay").classList.remove("active");
  if (!pendingImport) return;
  const item = pendingImport; pendingImport = null;
  suppressedImportGameRels.add(item.game_rel);
  const loadingToast = toastSpinner(`Loading ${item.name}…`);
  setStatus(`Loading ${item.name}…`);
  try {
    let res;
    if (item.file_type === "material") {
      // materials: api_material_params triggers mat_json (extraction) for any game_rel path
      res = await api(`/api/material_params?game_rel=${encodeURIComponent(item.game_rel)}`);
    } else {
      res = await api(handlerFor(item.file_type).import_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skin_id: item.skin_id, rel_path: item.rel_path, game_rel: item.game_rel }),
      });
    }
    loadingToast.remove();
    if (res.ok) {
      suppressChangeToastUntil = Date.now() + 1500;
      suppressedImportGameRels.delete(item.game_rel);
      toast(`Loaded: ${item.name}`, "success");
      setStatus("");
      refreshSidebarEntry(item.game_rel, item.name, item.skin_id);
      const gridArea   = document.getElementById("grid-area");
      const savedScroll = gridArea.scrollTop;
      await renderGrid();
      gridArea.scrollTop = savedScroll;
      const importedItem = allItems.find(i => i.game_rel === item.game_rel) || item;
      handleImportedFileAction(importedItem);
    } else {
      suppressedImportGameRels.delete(item.game_rel);
      toast(`Edit failed: ${res.error}`, "warning");
      setStatus("");
    }
  } catch (e) {
    loadingToast.remove();
    suppressedImportGameRels.delete(item.game_rel);
    toast(`Error: ${e.message}`, "warning");
    setStatus("");
  }
});

// ── material parameter editor ──────────────────────────────────────────────────
let matEditor = null;

function _hx2(c) { return ("0" + Math.round(Math.min(255, Math.max(0, c * 255))).toString(16)).slice(-2); }
function _rgbHex(r, g, b, inten) { const n = Math.max(inten, 1e-6); return "#" + _hx2(r / n) + _hx2(g / n) + _hx2(b / n); }

function _seedColors(arr) {
  return (arr || []).map(c => ({ name: c.name, rgba: c.rgba.slice(),
                                 inten: Math.max(c.rgba[0], c.rgba[1], c.rgba[2], 1) }));
}
function _seedScalars(arr) {
  return (arr || []).map(s => ({ name: s.name, value: s.value, orig: s.value,
                                 max: Math.max(Math.abs(s.value) * 3, 1) }));
}

async function openMaterialEditor(item) {
  const ov = document.getElementById("material-overlay");
  document.getElementById("mat-title").textContent = item.name;
  document.getElementById("mat-sub").textContent = item.game_rel || "";
  document.getElementById("mat-status").textContent = "";
  document.getElementById("mat-body").innerHTML = '<div class="spinner" style="margin:44px auto"></div>';
  ov.classList.add("active");
  let res;
  try { res = await api(`/api/material_params?game_rel=${encodeURIComponent(item.game_rel)}`); }
  catch (e) { document.getElementById("mat-body").innerHTML = `<div class="mat-empty">Error: ${e.message}</div>`; return; }
  if (!res.ok) { document.getElementById("mat-body").innerHTML = `<div class="mat-empty">${res.error || "failed to read material"}</div>`; return; }
  matEditor = { game_rel: item.game_rel, name: item.name,
                colors: _seedColors(res.colors), scalars: _seedScalars(res.scalars) };
  renderMatEditor();
  loadSidebar();
}

function renderMatEditor() {
  const m = matEditor; if (!m) return;
  let h = "";
  if (m.colors.length) {
    h += `<div class="mat-section">Colors</div>`;
    m.colors.forEach((c, i) => {
      h += `<div class="mat-row">
        <label title="${c.name}">${c.name}</label>
        <input type="color" value="${_rgbHex(c.rgba[0], c.rgba[1], c.rgba[2], c.inten)}" oninput="matColor(${i},this.value)">
        <span class="mat-tag">intensity</span>
        <input type="range" id="mir${i}" min="0" max="10" step="0.05" value="${Math.min(c.inten, 10)}" oninput="matInten(${i},this.value,1)">
        <input class="mat-num" id="min${i}" type="number" step="0.05" value="${+c.inten.toFixed(3)}" oninput="matInten(${i},this.value,0)">
        <span class="mat-tag">A</span>
        <input class="mat-num" type="number" step="0.05" min="0" max="1" value="${+c.rgba[3].toFixed(3)}" oninput="matAlpha(${i},this.value)">
      </div>`;
    });
  }
  if (m.scalars.length) {
    h += `<div class="mat-section">Scalars</div>`;
    m.scalars.forEach((s, i) => {
      h += `<div class="mat-row">
        <label title="${s.name}">${s.name}</label>
        <input type="range" id="msr${i}" min="${Math.min(0, s.orig)}" max="${s.max}" step="${s.max / 1000}" value="${s.value}" oninput="matScalar(${i},this.value,1)">
        <input class="mat-num wide" id="msn${i}" type="number" step="any" value="${s.value}" oninput="matScalar(${i},this.value,0)">
      </div>`;
    });
  }
  if (!m.colors.length && !m.scalars.length)
    h = `<div class="mat-empty">This material exposes no editable color or scalar parameters.</div>`;
  document.getElementById("mat-body").innerHTML = h;
}

function matColor(i, hex) {
  const c = matEditor.colors[i], n = Math.max(c.inten, 1e-6);
  c.rgba[0] = parseInt(hex.substr(1, 2), 16) / 255 * n;
  c.rgba[1] = parseInt(hex.substr(3, 2), 16) / 255 * n;
  c.rgba[2] = parseInt(hex.substr(5, 2), 16) / 255 * n;
}
function matInten(i, v, fromRange) {
  const c = matEditor.colors[i], o = Math.max(c.inten, 1e-6), nv = parseFloat(v) || 0;
  c.rgba[0] = c.rgba[0] / o * nv; c.rgba[1] = c.rgba[1] / o * nv; c.rgba[2] = c.rgba[2] / o * nv; c.inten = nv;
  const other = document.getElementById((fromRange ? "min" : "mir") + i); if (other) other.value = v;
}
function matAlpha(i, v) { matEditor.colors[i].rgba[3] = parseFloat(v) || 0; }
function matScalar(i, v, fromRange) {
  matEditor.scalars[i].value = parseFloat(v) || 0;
  const other = document.getElementById((fromRange ? "msn" : "msr") + i); if (other) other.value = v;
}

async function saveMaterial() {
  if (!matEditor) return;
  const colors = {}, scalars = {};
  matEditor.colors.forEach(c => { colors[c.name] = [c.rgba[0], c.rgba[1], c.rgba[2], c.rgba[3]]; });
  matEditor.scalars.forEach(s => { scalars[s.name] = s.value; });
  document.getElementById("mat-status").textContent = "Saving…";
  try {
    const res = await api("/api/material_save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_rel: matEditor.game_rel, colors, scalars }),
    });
    if (res.ok) {
      document.getElementById("mat-status").textContent = "Saved — staged for export.";
      toast(`Saved: ${matEditor.name}`, "success");
      loadSidebar();
    } else {
      document.getElementById("mat-status").textContent = "Error: " + (res.error || "save failed");
    }
  } catch (e) { document.getElementById("mat-status").textContent = "Error: " + e.message; }
}

async function resetMaterial() {
  if (!matEditor) return;
  document.getElementById("mat-status").textContent = "Resetting…";
  try {
    const res = await api("/api/material_reset", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_rel: matEditor.game_rel }),
    });
    if (res.ok) {
      matEditor.colors = _seedColors(res.colors); matEditor.scalars = _seedScalars(res.scalars);
      renderMatEditor();
      document.getElementById("mat-status").textContent = "Reset to vanilla.";
      toast(`Reset: ${matEditor.name}`, "info");
    } else { document.getElementById("mat-status").textContent = "Error: " + (res.error || "reset failed"); }
  } catch (e) { document.getElementById("mat-status").textContent = "Error: " + e.message; }
}

function closeMaterialEditor() { document.getElementById("material-overlay").classList.remove("active"); matEditor = null; }

document.getElementById("mat-save").addEventListener("click", saveMaterial);
document.getElementById("mat-reset").addEventListener("click", resetMaterial);
document.getElementById("mat-close").addEventListener("click", closeMaterialEditor);
document.getElementById("material-overlay").addEventListener("click", e => {
  if (e.target.id === "material-overlay") closeMaterialEditor();
});

// ── import all ────────────────────────────────────────────────────────────────
function _shownTextures() {
  const q = document.getElementById("search-input").value.trim().toLowerCase();
  return allItems.filter(i => i.type === "asset" && i.file_type === "texture"
    && (!q || (i.name || i.label || "").toLowerCase().includes(q)));
}

document.getElementById("import-all-btn").addEventListener("click", () => {
  const shown   = _shownTextures();
  const pending = shown.filter(i => !i.imported);
  const q       = document.getElementById("search-input").value.trim();
  if (!pending.length) { toast(q ? "All shown textures already edited" : "All textures already edited", "success"); return; }
  const sid = skinIdFromPath(nav.path);
  pendingImportAll = pending;
  document.getElementById("confirm-all-msg").textContent =
    `Extract and decode ${pending.length} texture${pending.length !== 1 ? "s" : ""}`
    + (pending.length < shown.length ? ` (${shown.length - pending.length} already edited)` : "")
    + (q ? ` matching "${q}"` : "")
    + (sid ? ` from "${sid}"` : "") + "?";
  document.getElementById("confirm-all-overlay").classList.add("active");
});

document.getElementById("confirm-all-cancel").addEventListener("click", () => {
  document.getElementById("confirm-all-overlay").classList.remove("active");
});

document.getElementById("confirm-all-ok").addEventListener("click", async () => {
  document.getElementById("confirm-all-overlay").classList.remove("active");
  const textures = pendingImportAll || [];
  pendingImportAll = null;
  if (!textures.length) return;

  const sid   = skinIdFromPath(nav.path);
  const items = textures.map(t => ({
    skin_id:  sid,
    rel_path: t.rel_path,
    game_rel: t.game_rel,
    name:     t.name || t.label,
  }));

  importing = true;
  showProgress(0, items.length);
  document.getElementById("prog-overlay").classList.add("active");

  const res = await api("/api/import_all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) {
    document.getElementById("prog-overlay").classList.remove("active");
    importing = false;
    toast(`Edit failed: ${res.error}`, "warning");
  }
});

function showProgress(current, total) {
  document.getElementById("prog-counter").textContent = `Downloading ${current} / ${total} assets…`;
}

// ── prevent accidental close during import ────────────────────────────────────
window.addEventListener("beforeunload", e => {
  if (importing) {
    e.preventDefault();
    e.returnValue = "An edit is in progress. Closing may leave assets incomplete.";
    return e.returnValue;
  }
});

// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource("/api/events");
  es.addEventListener("message", e => {
    try {
      const d = JSON.parse(e.data);
      handleSSE(d);
    } catch {}
  });
  es.onerror = () => setTimeout(connectSSE, 3000);
}

function handleSSE(d) {
  if (d.usmap_updated) {
    const ov = document.getElementById("usmap-update-overlay");
    document.getElementById("usmap-update-name").textContent = d.name || "";
    ov.classList.add("active");
    lucide.createIcons({ nodes: [ov] });
    return;
  }
  if (d.toast) {
    toast(d.toast, d.toast_type || "info", 5000);
    return;
  }
  if (d.thumb_ready && d.game_rel) {
    const sel = `img[data-game-rel="${CSS.escape(d.game_rel)}"]`;
    document.querySelectorAll(sel).forEach(img => {
      img.src = `/api/thumb?game_rel=${encodeURIComponent(d.game_rel)}&_t=${Date.now()}`;
    });
    return;
  }
  if (d.file_changed) {
    const bust = `?token=${d.token}&gr=${encodeURIComponent(d.game_rel)}&t=${Date.now()}`;
    document.querySelectorAll(`img[data-token="${d.token}"]`).forEach(img => {
      img.src = `/api/preview${bust}`;
    });
    document.querySelectorAll(`#sidebar-list .sb-item[data-token="${d.token}"] .sb-thumb img`).forEach(img => {
      img.src = `/api/preview${bust}`;
    });
    if (!importing && !suppressedImportGameRels.has(d.game_rel) && Date.now() >= suppressChangeToastUntil) {
      toast("Asset edited", "warning", 4000);
    }
    return;
  }
  if (!d.done && importing) {
    showProgress(d.current, d.total);
    return;
  }
  if (d.done && importing) {
    importing = false;
    suppressChangeToastUntil = Date.now() + 2500;
    document.getElementById("prog-overlay").classList.remove("active");
    if (d.error) {
      toast(`Load failed: ${d.error}`, "warning", 8000);
    } else {
      toast(`Loaded ${d.current} texture${d.current !== 1 ? "s" : ""}`, "success");
    }
    setStatus("");
    loadSidebar();
    const gridArea   = document.getElementById("grid-area");
    const savedScroll = gridArea.scrollTop;
    renderGrid().then(() => { gridArea.scrollTop = savedScroll; }).catch(() => {});
  }
}

connectSSE();

// ── sidebar / export ──────────────────────────────────────────────────────────
async function loadSidebar() {
  const data = await api("/api/imported");
  data.forEach(item => {
    if (!sidebarData[item.token]) {
      sidebarData[item.token] = { ...item, selected: true };
    } else {
      Object.assign(sidebarData[item.token], item);
    }
  });
  const live = new Set(data.map(d => d.token));
  Object.keys(sidebarData).forEach(t => { if (!live.has(t)) delete sidebarData[t]; });
  renderSidebar();
}

function refreshSidebarEntry(game_rel, name, skin_id) {
  api("/api/imported").then(data => {
    data.forEach(item => {
      if (!sidebarData[item.token]) sidebarData[item.token] = { ...item, selected: true };
      else Object.assign(sidebarData[item.token], item);
    });
    renderSidebar();
  });
}

function renderSidebar() {
  const list = document.getElementById("sidebar-list");
  list.innerHTML = "";
  const all = Object.values(sidebarData);
  if (!all.length) {
    list.innerHTML = '<div style="padding:20px 14px;font-size:12px;color:var(--muted)">No edited assets yet.</div>';
    updateExportBtn();
    return;
  }
  const q     = document.getElementById("search-input").value.trim().toLowerCase();
  const items = q ? all.filter(i =>
        (i.name || "").toLowerCase().includes(q) ||
        (i.skin_name || "").toLowerCase().includes(q) ||
        (i.char_name || "").toLowerCase().includes(q)) : all;
  if (!items.length) {
    list.innerHTML = '<div style="padding:20px 14px;font-size:12px;color:var(--muted)">No edited assets match the search.</div>';
    updateExportBtn();
    return;
  }
  items.forEach(item => {
    const el = document.createElement("div");
    el.className = "sb-item" + (item.selected ? " selected" : "");
    el.dataset.token = item.token;
    const h = handlerFor(item.file_type);
    el.innerHTML = `
      <button class="sb-clear" title="Delete"><i data-lucide="x" size="12"></i></button>
      <div class="sb-thumb">
        ${h.preview
          ? `<img src="/api/preview?token=${item.token}&game_rel=${encodeURIComponent(item.game_rel)}"
               alt="" onerror="this.style.opacity='.3'">`
          : `<i data-lucide="${h.icon || 'file-question'}" size="32" class="card-icon ${assetIconCls(item.file_type)}"></i>`}
      </div>
      <div class="sb-info">
        <div class="sb-name">${item.name}</div>
        <div class="sb-sub">${item.char_name || item.skin_id || ""} / ${item.skin_name || ""}</div>
      </div>
      <div class="sb-check">${item.selected ? '<i data-lucide="check" size="12"></i>' : ""}</div>
    `;
    el.querySelector(".sb-clear").addEventListener("click", e => {
      e.stopPropagation();
      clearImported(item.token);
    });
    el.querySelector(".sb-check").addEventListener("click", e => {
      e.stopPropagation();
      item.selected = !item.selected;
      renderSidebar();
    });
    el.addEventListener("click", () => handleImportedFileAction(item));
    el.addEventListener("contextmenu", e => _ctxShow(e, _ctxItemsSidebar(item)));
    list.appendChild(el);
  });
  lucide.createIcons({ nodes: [list] });
  updateExportBtn();
}

function updateExportBtn() {
  const sel = Object.values(sidebarData).filter(i => i.selected).length;
  const badge = document.getElementById("sel-count");
  badge.textContent = `${sel}`;
  badge.classList.toggle("active", sel > 0);
  document.getElementById("export-btn").disabled = sel === 0;
}

async function doExport() {
  const selected = Object.values(sidebarData).filter(i => i.selected);
  if (!selected.length) return;
  const modName = document.getElementById("mod-name-input").value.trim() || "ModFilename";
  const exportable = selected.filter(i => ["texture", "material"].includes(i.file_type || ""));
  const skipped    = selected.length - exportable.length;
  if (!exportable.length) {
    toast("Nothing exportable selected — VFX export isn't implemented yet", "info");
    return;
  }
  document.getElementById("export-btn").disabled = true;
  setStatus(`Exporting ${exportable.length} asset${exportable.length !== 1 ? "s" : ""}…`);
  const exportingToast = toastSpinner(`Exporting ${modName}…`);
  try {
    const res = await api("/api/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mod_name: modName, items: exportable.map(i => i.game_rel) }),
    });
    exportingToast.remove();
    if (res.ok && res.pak_path) {
      toast(`Exported: ${modName}_9999999_P.pak` + (skipped ? ` (${skipped} VFX/other skipped)` : ""), "success", 5000);
      setStatus(`Exported → ${res.pak_path}`);
      fetch(`/api/open_explorer?path=${encodeURIComponent(res.pak_path.replace(/\//g, "\\"))}`);
    } else {
      toast(`Export failed: ${res.error || "unknown error"}`, "warning");
      setStatus("");
    }
  } catch (e) {
    exportingToast.remove();
    toast(`Error: ${e.message}`, "warning"); setStatus("");
  } finally {
    updateExportBtn();
  }
}

document.getElementById("export-btn").addEventListener("click", doExport);

// ── clear individual / clear all ──────────────────────────────────────────────
function clearImported(token) {
  const item = sidebarData[token];
  if (!item) return;
  pendingClear = item;
  const ft   = item.file_type || "asset";
  const kind = ft.charAt(0).toUpperCase() + ft.slice(1);
  document.getElementById("confirm-clear-title").textContent = `Delete ${kind}?`;
  document.getElementById("confirm-clear-msg").textContent =
    `Delete "${item.name}" from local assets? This will remove the imported file.`;
  document.getElementById("confirm-clear-overlay").classList.add("active");
}

document.getElementById("confirm-clear-cancel").addEventListener("click", () => {
  document.getElementById("confirm-clear-overlay").classList.remove("active");
  pendingClear = null;
});

document.getElementById("confirm-clear-ok").addEventListener("click", async () => {
  document.getElementById("confirm-clear-overlay").classList.remove("active");
  if (!pendingClear) return;
  const item = pendingClear; pendingClear = null;
  try {
    const res = await api("/api/delete_imported", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_rel: item.game_rel }),
    });
    if (res.ok) {
      delete sidebarData[item.token];
      renderSidebar();
      toast(`Deleted: ${item.name}`, "warning", 3000);
      renderGrid().catch(() => {});
    } else {
      toast(`Delete failed: ${res.error}`, "warning");
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
  }
});

document.getElementById("clear-all-btn").addEventListener("click", () => {
  const count = Object.keys(sidebarData).length;
  if (!count) { toast("No edited assets to clear", "info"); return; }
  document.getElementById("confirm-clear-all-msg").textContent =
    `All ${count} edited asset${count !== 1 ? "s" : ""} will be permanently deleted from local assets.`;
  document.getElementById("confirm-clear-all-overlay").classList.add("active");
});

document.getElementById("confirm-clear-all-cancel").addEventListener("click", () => {
  document.getElementById("confirm-clear-all-overlay").classList.remove("active");
});

document.getElementById("confirm-clear-all-ok").addEventListener("click", async () => {
  document.getElementById("confirm-clear-all-overlay").classList.remove("active");
  try {
    const res = await api("/api/delete_all_imported", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (res.ok) {
      sidebarData = {};
      renderSidebar();
      toast(`Deleted ${res.deleted} edited asset${res.deleted !== 1 ? "s" : ""}`, "warning", 4000);
      renderGrid().catch(() => {});
    } else {
      toast(`Delete failed: ${res.error}`, "warning");
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
  }
});

// ── prereq check ─────────────────────────────────────────────────────────────
async function checkPrereqs() {
  try {
    const res = await api("/api/prereqs");
    if (!res.issues || !res.issues.length) return;
    res.issues.forEach(issue => {
      const isError = issue.level === "error";
      toast(issue.message, isError ? "warning" : "info", isError ? 10000 : 6000);
    });
  } catch (e) {}
}

// ── first-run setup ───────────────────────────────────────────────────────────
async function _fetchAesKeyValue() {
  try {
    const r = await fetch("https://raw.githubusercontent.com/SpaceDepot/rivals-depot/refs/heads/main/AES");
    if (!r.ok) return null;
    const text = (await r.text()).trim();
    if (!text) return null;
    return /^0x/i.test(text) ? text : "0x" + text;
  } catch (_) { return null; }
}

async function _fetchUsmapPath() {
  try {
    const res = await api("/api/download_usmap", { method: "POST" });
    return (res.ok && res.path) ? res.path : null;
  } catch (_) { return null; }
}

function _setSetupLoading(on) {
  document.getElementById("setup-loading").classList.toggle("active", on);
}

async function checkSetup() {
  document.getElementById("setup-overlay").classList.add("active");
  _setSetupLoading(true);
  try {
    const [statusRes, aes, usmapPath] = await Promise.all([
      api("/api/setup_status"),
      _fetchAesKeyValue(),
      _fetchUsmapPath(),
    ]);
    if (statusRes.configured) {
      document.getElementById("setup-overlay").classList.remove("active");
      return false;
    }
    document.getElementById("setup-path").value  = statusRes.paks_prefill  || "";
    document.getElementById("setup-aes").value   = aes  || statusRes.aes_prefill  || "";
    document.getElementById("setup-usmap").value = usmapPath || statusRes.usmap_prefill || "";
    _setSetupLoading(false);
    await validateSetup();
    return true;
  } catch (e) {
    _setSetupLoading(false);
    return false;
  }
}

let _validateGen = 0;
async function validateSetup() {
  const gen     = ++_validateGen;
  const path    = document.getElementById("setup-path").value.trim();
  const usmap   = document.getElementById("setup-usmap").value.trim();
  const key     = document.getElementById("setup-aes").value.trim();
  const el      = document.getElementById("setup-status");
  const saveBtn = document.getElementById("setup-save");

  let pakStatus = "", pakMsg = "";
  if (path) {
    try {
      const r = await fetch(`/api/validate_paks?path=${encodeURIComponent(path)}`);
      const d = await r.json();
      pakStatus = d.status;
      if (d.status === "wrong_folder") pakMsg = "MarvelRivals folder not found";
      else if (d.status === "missing")  pakMsg = "Path doesn't exist";
    } catch (_) {}
  }

  let usmapStatus = "";
  if (usmap) {
    try {
      const r = await fetch(`/api/validate_usmap?path=${encodeURIComponent(usmap)}`);
      const d = await r.json();
      usmapStatus = d.status;
    } catch (_) {}
  }

  if (gen !== _validateGen) return;

  let keyStatus = "";
  if (key) {
    keyStatus = /^0x[0-9A-Fa-f]{60,68}$/.test(key) ? "ok" : "invalid";
  }

  const pakOk    = pakStatus === "ok";
  const usmapOk  = usmapStatus === "ok";
  const keyOk    = keyStatus === "ok";
  const pakBad   = pakStatus === "wrong_folder" || pakStatus === "missing";
  const usmapBad = usmapStatus === "invalid" || usmapStatus === "missing";
  const keyBad   = keyStatus === "invalid";

  if (pakOk && usmapOk && keyOk) {
    el.className = "ok";
    el.innerHTML = '<i data-lucide="check-circle" size="13"></i> All Valid';
    saveBtn.disabled = false;
  } else {
    saveBtn.disabled = true;
    if (pakBad) {
      el.className = "error";
      el.innerHTML = `<i data-lucide="x-circle" size="13"></i> ${pakMsg}`;
    } else if (usmapBad) {
      el.className = "error";
      el.innerHTML = usmapStatus === "missing"
        ? '<i data-lucide="x-circle" size="13"></i> USMAP file not found'
        : '<i data-lucide="x-circle" size="13"></i> Not a valid .usmap file';
    } else if (keyBad) {
      el.className = "error";
      el.innerHTML = '<i data-lucide="x-circle" size="13"></i> Invalid AES key format';
    } else if (pakOk && usmapOk && !key) {
      el.className = "error";
      el.innerHTML = '<i data-lucide="x-circle" size="13"></i> Key missing';
    } else {
      el.className = ""; el.innerHTML = "";
    }
  }
  lucide.createIcons({ nodes: [el] });

  const pathEl  = document.getElementById("setup-path");
  const usmapEl = document.getElementById("setup-usmap");
  const aesEl   = document.getElementById("setup-aes");
  pathEl.classList.toggle("setup-valid",   pakOk);
  pathEl.classList.toggle("setup-invalid", pakBad);
  usmapEl.classList.toggle("setup-valid",   usmapOk);
  usmapEl.classList.toggle("setup-invalid", usmapBad);
  aesEl.classList.toggle("setup-valid",   keyOk);
  aesEl.classList.toggle("setup-invalid", keyBad || (pakOk && usmapOk && !key));
}

document.getElementById("setup-path").addEventListener("input", validateSetup);
document.getElementById("setup-usmap").addEventListener("input", validateSetup);
document.getElementById("setup-aes").addEventListener("input", validateSetup);
document.getElementById("setup-paste-key").addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      document.getElementById("setup-aes").value = text.trim();
      validateSetup();
    }
  } catch {}
});

document.getElementById("setup-browse").addEventListener("click", async () => {
  const initial = document.getElementById("setup-path").value.trim();
  const btn = document.getElementById("setup-browse");
  btn.disabled = true;
  try {
    const res = await api("/api/pick_folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial }),
    });
    if (res.ok && res.path) {
      document.getElementById("setup-path").value = res.path;
      validateSetup();
    }
  } catch (e) {}
  btn.disabled = false;
});

document.getElementById("setup-usmap-browse").addEventListener("click", async () => {
  const initial = document.getElementById("setup-usmap").value.trim();
  const btn = document.getElementById("setup-usmap-browse");
  btn.disabled = true;
  try {
    const res = await api("/api/pick_usmap_file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial }),
    });
    if (res.ok && res.path) {
      document.getElementById("setup-usmap").value = res.path;
      validateSetup();
    }
  } catch (e) {}
  btn.disabled = false;
});


document.getElementById("setup-save").addEventListener("click", async () => {
  const path      = document.getElementById("setup-path").value.trim();
  const usmapPath = document.getElementById("setup-usmap").value.trim();
  const rawKey    = document.getElementById("setup-aes").value.trim();
  const aes_key   = rawKey.toLowerCase().startsWith("0x") ? rawKey.slice(2) : rawKey;
  if (!path)      { toast("Please enter a path", "warning"); return; }
  if (!usmapPath) { toast("Please enter or auto-fetch a USMAP file", "warning"); return; }
  if (!aes_key)   { toast("Please enter an AES key", "warning"); return; }
  const btn = document.getElementById("setup-save");
  btn.disabled = true;
  btn.innerHTML = "Saving…";
  try {
    const res = await api("/api/save_paks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, aes_key, usmap_path: usmapPath }),
    });
    if (res.ok) {
      document.getElementById("setup-overlay").classList.remove("active");
      await checkPrereqs();
      await renderGrid();
      await loadSidebar();
    } else {
      toast(`Error: ${res.error}`, "warning");
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="check" size="14"></i> Save & Continue';
      lucide.createIcons({ nodes: [btn] });
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="check" size="14"></i> Save & Continue';
    lucide.createIcons({ nodes: [btn] });
  }
});

// ── sidebar resize ────────────────────────────────────────────────────────────
{
  const handle  = document.getElementById("sidebar-resize");
  const sidebar = document.getElementById("sidebar");
  let dragging = false, startX = 0, startW = 0;

  handle.addEventListener("mousedown", e => {
    dragging = true;
    startX   = e.clientX;
    startW   = sidebar.offsetWidth;
    handle.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor     = "col-resize";
    e.preventDefault();
  });

  document.addEventListener("mousemove", e => {
    if (!dragging) return;
    const newW = Math.min(520, Math.max(140, startW + (startX - e.clientX)));
    sidebar.style.width = newW + "px";
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor     = "";
  });
}

// ── context menu ──────────────────────────────────────────────────────────────
let _ctxFileTarget = null;
const _ctxMenu      = document.getElementById("ctx-menu");
const _ctxFileInput = document.getElementById("ctx-file-input");

function _ctxShow(e, items) {
  e.preventDefault();
  e.stopPropagation();
  if (!items.length) return;
  _ctxMenu.innerHTML = "";
  for (const item of items) {
    if (item === "sep") {
      const d = document.createElement("div");
      d.className = "ctx-sep";
      _ctxMenu.appendChild(d);
      continue;
    }
    const el = document.createElement("div");
    el.className = "ctx-item" + (item.danger ? " danger" : "");
    el.innerHTML = `<i data-lucide="${item.icon}" size="13"></i><span>${item.label}</span>`;
    el.addEventListener("click", ev => { ev.stopPropagation(); _ctxHide(); item.action(); });
    _ctxMenu.appendChild(el);
  }
  lucide.createIcons({ nodes: [_ctxMenu] });
  _ctxMenu.style.left = e.clientX + "px";
  _ctxMenu.style.top  = e.clientY + "px";
  _ctxMenu.classList.add("active");
  const r = _ctxMenu.getBoundingClientRect();
  if (r.right  > window.innerWidth)  _ctxMenu.style.left = (e.clientX - r.width)  + "px";
  if (r.bottom > window.innerHeight) _ctxMenu.style.top  = (e.clientY - r.height) + "px";
}

function _ctxHide() { _ctxMenu.classList.remove("active"); }

document.addEventListener("click", _ctxHide);
document.addEventListener("contextmenu", () => _ctxHide());

_ctxFileInput.addEventListener("change", async () => {
  const file = _ctxFileInput.files[0];
  _ctxFileInput.value = "";
  if (!file || !_ctxFileTarget) return;
  const game_rel = _ctxFileTarget;
  _ctxFileTarget = null;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("game_rel", game_rel);
  suppressedImportGameRels.add(game_rel);
  const t = toastSpinner("Replacing…");
  try {
    const res  = await fetch("/api/replace_texture", { method: "POST", body: fd });
    const data = await res.json();
    t.remove();
    if (data.ok) {
      suppressChangeToastUntil = Date.now() + 1500;
      suppressedImportGameRels.delete(game_rel);
      toast("Texture replaced", "success");
      const bust = `&_t=${Date.now()}`;
      document.querySelectorAll(`img[data-game-rel="${CSS.escape(game_rel)}"]`).forEach(img => {
        img.src = `/api/thumb?game_rel=${encodeURIComponent(game_rel)}${bust}`;
      });
      if (data.token) {
        document.querySelectorAll(`img[data-token="${data.token}"]`).forEach(img => {
          img.src = `/api/preview?token=${data.token}&game_rel=${encodeURIComponent(game_rel)}${bust}`;
        });
      }
    } else {
      suppressedImportGameRels.delete(game_rel);
      toast(`Replace failed: ${data.error}`, "warning");
    }
  } catch (err) {
    t.remove();
    suppressedImportGameRels.delete(game_rel);
    toast(`Error: ${err.message}`, "warning");
  }
});

function _ctxItemsCard(card) {
  const items = [];
  if (!card.imported) {
    items.push({ icon: "download", label: "Edit this asset", action: () => handleAssetClick({ imported: card.imported, token: card.token, file_type: card.file_type, name: card.label, rel_path: card.rel_path, game_rel: card.game_rel }) });
    return items;
  }
  if (card.game_rel)
    items.push({ icon: "folder-open", label: "Open in Explorer", action: () => fetch(`/api/open_explorer?game_rel=${encodeURIComponent(card.game_rel)}`) });
  if (card.game_rel)
    items.push({ icon: "compass", label: "Find in Atelier", action: () => { const p = card.game_rel.split("/"); pushNav({ path: p.slice(0, -1).join("/") }); } });
  if (card.imported && card.file_type === "texture" && card.game_rel) {
    if (items.length) items.push("sep");
    items.push({ icon: "image-plus", label: "Replace with Image", action: () => { _ctxFileTarget = card.game_rel; _ctxFileInput.click(); } });
  }
  if (card.imported && card.token) {
    if (items.length && items[items.length - 1] !== "sep") items.push("sep");
    items.push({ icon: "trash-2", label: "Delete edits", danger: true, action: () => clearImported(card.token) });
  }
  return items;
}

function _ctxItemsSidebar(item) {
  const items = [];
  if (item.game_rel)
    items.push({ icon: "folder-open", label: "Open in Explorer", action: () => fetch(`/api/open_explorer?game_rel=${encodeURIComponent(item.game_rel)}`) });
  if (item.game_rel)
    items.push({ icon: "compass", label: "Find in Atelier", action: () => { const p = item.game_rel.split("/"); pushNav({ path: p.slice(0, -1).join("/") }); } });
  if (item.file_type === "texture" && item.game_rel) {
    if (items.length) items.push("sep");
    items.push({ icon: "image-plus", label: "Replace with Image", action: () => { _ctxFileTarget = item.game_rel; _ctxFileInput.click(); } });
  }
  if (items.length) items.push("sep");
  items.push({ icon: "trash-2", label: "Delete edits", danger: true, action: () => clearImported(item.token) });
  return items;
}

// ── USMAP update check ────────────────────────────────────────────────────────
async function checkUsmapUpdate() {
  try { await api("/api/usmap_update_check"); } catch (_) {}
}

// ── update check ──────────────────────────────────────────────────────────────
function _fmtMB(bytes) {
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}
function _showUpdatePanel(id) {
  ["update-checking", "update-confirm", "update-downloading", "update-error"].forEach(p => {
    document.getElementById(p).style.display = p === id ? "flex" : "none";
  });
}

async function checkUpdate() {
  const overlay = document.getElementById("update-overlay");
  overlay.classList.add("active");
  _showUpdatePanel("update-checking");

  let info;
  try { info = await api("/api/update_check"); }
  catch { overlay.classList.remove("active"); return false; }

  if (!info.available) { overlay.classList.remove("active"); return false; }

  document.getElementById("update-tag").textContent = info.tag;
  _showUpdatePanel("update-confirm");
  lucide.createIcons();

  return new Promise(resolve => {
    const dismiss = () => { overlay.classList.remove("active"); resolve(false); };
    document.getElementById("update-later").onclick = dismiss;
    const skipVerBtn = document.getElementById("update-skip-ver");
    if (skipVerBtn) skipVerBtn.onclick = async () => {
      try { await api("/api/update_skip", { method: "POST" }); } catch {}
      dismiss();
    };
    document.getElementById("update-now").onclick = async () => {
      _showUpdatePanel("update-downloading");
      const fill  = document.getElementById("update-dl-fill");
      const label = document.getElementById("update-dl-label");
      try { await api("/api/update_download", { method: "POST" }); }
      catch { _showUpdatePanel("update-error"); return; }
      const poll = setInterval(async () => {
        let s, p;
        try {
          [s, p] = await Promise.all([api("/api/update_status"), api("/api/update_progress")]);
        } catch { clearInterval(poll); return; }
        if (p.total > 0) {
          fill.style.width = p.pct + "%";
          label.textContent = `${_fmtMB(p.bytes)} / ${_fmtMB(p.total)}  (${p.pct}%)`;
        }
        if (s.state === "error") {
          clearInterval(poll);
          _showUpdatePanel("update-error");
          document.getElementById("update-error-ok").onclick = () => { overlay.classList.remove("active"); resolve(false); };
        }
      }, 300);
    };
    document.getElementById("update-error-ok").onclick = () => {
      overlay.classList.remove("active");
      resolve(false);
    };
  });
}

// ── initial load ──────────────────────────────────────────────────────────────
async function init() {
  console.log("[init] starting");
  renderBreadcrumbs();
  if (await checkUpdate()) { console.log("[init] update in progress"); return; }
  if (await checkSetup()) { console.log("[init] halted for setup"); return; }
  await checkPrereqs();
  await renderGrid();
  await loadSidebar();
  checkUsmapUpdate();
}

init();
