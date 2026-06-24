"use strict";

// ── icons ─────────────────────────────────────────────────────────────────────
lucide.createIcons();

// ── state ─────────────────────────────────────────────────────────────────────
const NAV_ROOT    = { level: 0, char_id: null, skin_id: null, path: "" };
let   nav         = { ...NAV_ROOT };
let   history     = [{ ...NAV_ROOT }];
let   histIdx     = 0;
let   allItems    = [];      // current grid items
let   sidebarData = {};      // token -> {name, game_rel, skin_id, char_name, skin_name, selected}
let   importing   = false;
let   pendingImport = null;  // {skin_id, rel_path, game_rel, name, card_el}
let   pendingClear  = null;  // sidebar item to delete
let   pendingImportAll = null;       // captured texture set for "Import All" (respects search filter)
let   suppressChangeToastUntil = 0;  // swallow watcher "updated on disk" toasts during our own imports

// ── handler registry ──────────────────────────────────────────────────────────
// Add a new entry here when a new asset type handler is implemented.
const ASSET_HANDLERS = {
  texture:  { endpoint: "/api/import_texture",  preview: true,  icon: "image" },
  material: { endpoint: "/api/import_material", preview: false, icon: "circle-star" },
  vfx:      { endpoint: "/api/import_vfx",      preview: false,  icon: "sparkles"   },
};
function handlerFor(ft) { return ASSET_HANDLERS[ft] || { endpoint: "/api/import", preview: false, icon: "file-question" }; }

const ASSET_ICON_CLS = {
  texture:  "texture-icon",
  vfx:      "vfx-icon",
  material: "material-icon",
};
function assetIconCls(ft) { return ASSET_ICON_CLS[ft] || "unhandled-icon"; }

const FOLDER_ICON_PATTERNS = [
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
let _charNameCache = {};
let _skinLabelCache = {};

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

  bc.appendChild(crumb("All Characters", nav.level > 0 ? { level: 0, char_id: null, skin_id: null, path: "" } : null));

  if (nav.level >= 1) {
    const cname = _charNameCache[nav.char_id] || nav.char_id;
    bc.appendChild(sep());
    bc.appendChild(crumb(`${nav.char_id} — ${cname}`,
      nav.level > 1 ? { level: 1, char_id: nav.char_id, skin_id: null, path: "" } : null));
  }
  if (nav.level >= 2) {
    const slabel = _skinLabelCache[nav.skin_id] || nav.skin_id;
    bc.appendChild(sep());
    bc.appendChild(crumb(slabel,
      nav.level > 2 || nav.path ? { level: 2, char_id: nav.char_id, skin_id: nav.skin_id, path: "" } : null));
  }
  if (nav.level >= 2 && nav.path) {
    const parts = nav.path.split("/");
    parts.forEach((part, i) => {
      bc.appendChild(sep());
      const isLast = i === parts.length - 1;
      const partPath = parts.slice(0, i + 1).join("/");
      bc.appendChild(crumb(part,
        isLast ? null : { level: 2, char_id: nav.char_id, skin_id: nav.skin_id, path: partPath }));
    });
  }
  lucide.createIcons({ nodes: [bc] });
}

// ── grid rendering ────────────────────────────────────────────────────────────
async function renderGrid() {
  const area = document.getElementById("grid-area");
  area.innerHTML = '<div id="empty-state"><div class="spinner" style="margin:0 auto 12px"></div><div>Loading…</div></div>';
  document.getElementById("import-all-btn").disabled = true;

  try {
    if (nav.level === 0) {
      await renderCharacters();
    } else if (nav.level === 1) {
      await renderSkins();
    } else {
      await renderBrowse();
    }
  } catch (e) {
    area.innerHTML = `<div id="empty-state" style="color:var(--acc)">${e.message}</div>`;
  }
}

async function renderCharacters() {
  const data = await api("/api/characters");
  if (data.error) throw new Error(data.error);
  allItems = data;
  data.forEach(c => { _charNameCache[c.char_id] = c.name; });
  buildGrid(data.map(c => ({
    type:   "char",
    id:     c.char_id,
    label:  `${c.char_id} — ${c.name}`,
    sub:    `${c.skin_count} skin${c.skin_count !== 1 ? "s" : ""}`,
    icon:   "square-user-round",
    iconCls:"char-icon",
    onClick: () => pushNav({ level: 1, char_id: c.char_id, skin_id: null, path: "" }),
  })));
}

async function renderSkins() {
  const data = await api(`/api/skins?char_id=${encodeURIComponent(nav.char_id)}`);
  if (data.error) throw new Error(data.error);
  allItems = data;
  data.forEach(s => { _skinLabelCache[s.skin_id] = s.label; });
  buildGrid(data.map(s => ({
    type:   "skin",
    id:     s.skin_id,
    label:  s.label,
    icon:   "square-user-round",
    iconCls:"char-icon",
    onClick: () => pushNav({ level: 2, char_id: nav.char_id, skin_id: s.skin_id, path: "" }),
  })));
}

async function renderBrowse() {
  const url = `/api/browse?skin_id=${encodeURIComponent(nav.skin_id)}&path=${encodeURIComponent(nav.path || "")}`;
  const data = await api(url);
  if (data.error) throw new Error(data.error);
  allItems = data;

  const importable = data.filter(d => d.type === "asset" && d.file_type === "texture");
  document.getElementById("import-all-btn").disabled = importable.length === 0;

  buildGrid(data.map(item => {
    if (item.type === "folder") {
      return {
        type:    "folder",
        label:   item.name,
        iconCls: folderIconCls(item.name),
        onClick: () => pushNav({ level: 2, char_id: nav.char_id, skin_id: nav.skin_id,
                                 path: item.rel_path }),
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
  }));
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

    if (card.type === "asset" && card.imported && card.token && handlerFor(card.file_type).preview) {
      const img = document.createElement("img");
      img.src = `/api/preview?token=${card.token}&game_rel=${encodeURIComponent(card.game_rel)}`;
      img.alt = card.label;
      img.onerror = () => { img.replaceWith(makeIcon(card)); };
      thumb.appendChild(img);
      img.dataset.token = card.token;
    } else {
      thumb.appendChild(makeIcon(card));
    }

    const name = document.createElement("div");
    name.className = "card-name";
    name.textContent = card.label;

    el.appendChild(thumb);
    el.appendChild(name);
    el.addEventListener("click", card.onClick);
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
  if (nav.level === 0 && allItems.length) {
    buildGrid(allItems.map(c => ({
      type: "char", id: c.char_id,
      label: `${c.char_id} — ${c.name}`,
      sub: `${c.skin_count} skin${c.skin_count !== 1 ? "s" : ""}`,
      icon: "square-user-round", iconCls: "char-icon",
      onClick: () => pushNav({ level: 1, char_id: c.char_id, skin_id: null, path: "" }),
    })));
  } else if (nav.level === 1 && allItems.length) {
    buildGrid(allItems.map(s => ({
      type: "skin", id: s.skin_id, label: s.label,
      icon: "square-user-round", iconCls: "char-icon",
      onClick: () => pushNav({ level: 2, char_id: nav.char_id, skin_id: s.skin_id, path: "" }),
    })));
  } else if (nav.level >= 2 && allItems.length) {
    buildGrid(allItems.map(item => {
      if (item.type === "folder") {
        return { type: "folder", label: item.name,
          iconCls: folderIconCls(item.name),
          onClick: () => pushNav({ level: 2, char_id: nav.char_id, skin_id: nav.skin_id, path: item.rel_path }) };
      }
      const ft = item.file_type || "other";
      return { type: "asset", file_type: ft, label: item.name,
        iconCls: assetIconCls(ft),
        imported: item.imported, token: item.token,
        game_rel: item.game_rel, rel_path: item.rel_path,
        onClick: () => handleAssetClick(item) };
    }));
  }
  renderSidebar();
});

// ── asset click / single import ───────────────────────────────────────────────
function handleImportedFileAction(item) {
  const ft = item.file_type || "texture";
  switch (ft) {
    case "material":
      return; // material parameter menu — to be implemented
    default:
      fetch(`/api/open_explorer?game_rel=${encodeURIComponent(item.game_rel)}`);
  }
}

function handleAssetClick(item) {
  if (item.imported && item.token) {
    handleImportedFileAction(item);
    return;
  }
  const ft   = item.file_type || "other";
  const kind = ft.charAt(0).toUpperCase() + ft.slice(1);
  document.getElementById("confirm-title").textContent = `Import ${kind}?`;
  document.getElementById("confirm-msg").textContent =
    `Import ${ft} "${item.name}" from skin ${nav.skin_id}?`;
  pendingImport = { skin_id: nav.skin_id, rel_path: item.rel_path, game_rel: item.game_rel, name: item.name, file_type: ft };
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
  suppressChangeToastUntil = Date.now() + 2500;
  setStatus(`Importing ${item.name}…`);
  try {
    const res = await api(handlerFor(item.file_type).endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skin_id: item.skin_id, rel_path: item.rel_path }),
    });
    if (res.ok) {
      toast(`Imported: ${item.name}`, "success");
      setStatus("");
      refreshSidebarEntry(item.game_rel, item.name, item.skin_id);
      renderBrowse().catch(() => {});
    } else {
      toast(`Import failed: ${res.error}`, "warning");
      setStatus("");
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
    setStatus("");
  }
});

// ── import all ────────────────────────────────────────────────────────────────
function _shownTextures() {
  const q = document.getElementById("search-input").value.trim().toLowerCase();
  return allItems.filter(i => i.type === "asset" && i.file_type === "texture"
    && (!q || (i.name || i.label || "").toLowerCase().includes(q)));
}

document.getElementById("import-all-btn").addEventListener("click", () => {
  if (nav.level < 2) return;
  const shown   = _shownTextures();
  const pending = shown.filter(i => !i.imported);
  const q       = document.getElementById("search-input").value.trim();
  if (!pending.length) { toast(q ? "All shown textures already imported" : "All textures already imported", "success"); return; }
  pendingImportAll = pending;
  document.getElementById("confirm-all-msg").textContent =
    `Extract and decode ${pending.length} texture${pending.length !== 1 ? "s" : ""}`
    + (pending.length < shown.length ? ` (${shown.length - pending.length} already imported)` : "")
    + (q ? ` matching "${q}"` : "")
    + ` from "${nav.skin_id}"?`;
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

  const items = textures.map(t => ({
    skin_id:  nav.skin_id,
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
    toast(`Import failed: ${res.error}`, "warning");
  }
});

function showProgress(current, total) {
  document.getElementById("prog-counter").textContent = `Downloading ${current} / ${total} assets…`;
}

// ── prevent accidental close during import ────────────────────────────────────
window.addEventListener("beforeunload", e => {
  if (importing) {
    e.preventDefault();
    e.returnValue = "An import is in progress. Closing may leave assets incomplete.";
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
  if (d.file_changed) {
    const bust = `?token=${d.token}&gr=${encodeURIComponent(d.game_rel)}&t=${Date.now()}`;
    document.querySelectorAll(`img[data-token="${d.token}"]`).forEach(img => {
      img.src = `/api/preview${bust}`;
    });
    document.querySelectorAll(`#sidebar-list .sb-item[data-token="${d.token}"] .sb-thumb img`).forEach(img => {
      img.src = `/api/preview${bust}`;
    });
    if (!importing && Date.now() >= suppressChangeToastUntil) {
      toast("Asset updated on disk — preview refreshed", "warning", 4000);
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
      toast(`Import failed: ${d.error}`, "warning", 8000);
    } else {
      toast(`Imported ${d.current} texture${d.current !== 1 ? "s" : ""}`, "success");
    }
    setStatus("");
    loadSidebar();
    if (nav.level >= 2) renderBrowse().catch(() => {});
  }
}

connectSSE();

// ── sidebar / export ──────────────────────────────────────────────────────────
async function loadSidebar() {
  const data = await api("/api/imported");
  data.forEach(item => {
    if (!sidebarData[item.token]) {
      sidebarData[item.token] = { ...item, selected: false };
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
      if (!sidebarData[item.token]) sidebarData[item.token] = { ...item, selected: false };
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
    list.innerHTML = '<div style="padding:20px 14px;font-size:12px;color:var(--muted)">No assets imported yet.</div>';
    updateExportBtn();
    return;
  }
  const q     = document.getElementById("search-input").value.trim().toLowerCase();
  const items = q ? all.filter(i =>
        (i.name || "").toLowerCase().includes(q) ||
        (i.skin_name || "").toLowerCase().includes(q) ||
        (i.char_name || "").toLowerCase().includes(q)) : all;
  if (!items.length) {
    list.innerHTML = '<div style="padding:20px 14px;font-size:12px;color:var(--muted)">No staged assets match the search.</div>';
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
        <div class="sb-sub">${item.char_name} / ${item.skin_name}</div>
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
    list.appendChild(el);
  });
  lucide.createIcons({ nodes: [list] });
  updateExportBtn();
}

function updateExportBtn() {
  const sel = Object.values(sidebarData).filter(i => i.selected).length;
  document.getElementById("sel-count").textContent = `${sel} selected`;
  document.getElementById("export-btn").disabled = sel === 0;
}

document.getElementById("export-btn").addEventListener("click", async () => {
  const selected = Object.values(sidebarData).filter(i => i.selected);
  if (!selected.length) return;
  const modName = document.getElementById("mod-name-input").value.trim() || "ModFilename";
  const items   = selected.map(i => i.game_rel);
  setStatus(`Exporting ${items.length} asset${items.length !== 1 ? "s" : ""}…`);
  document.getElementById("export-btn").disabled = true;
  try {
    const res = await api("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mod_name: modName, items }),
    });
    if (res.ok && res.pak_path) {
      toast(`Exported: ${modName}_9999999_P.pak`, "success", 5000);
      setStatus(`Exported → ${res.pak_path}`);
      fetch(`/api/open_explorer?path=${encodeURIComponent(res.pak_path.replace(/\//g, "\\"))}`);
    } else {
      toast(`Export failed: ${res.error || "unknown error"}`, "warning");
      setStatus("");
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
    setStatus("");
  } finally {
    updateExportBtn();
  }
});

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
      if (nav.level >= 2) renderBrowse().catch(() => {});
    } else {
      toast(`Delete failed: ${res.error}`, "warning");
    }
  } catch (e) {
    toast(`Error: ${e.message}`, "warning");
  }
});

document.getElementById("clear-all-btn").addEventListener("click", () => {
  const count = Object.keys(sidebarData).length;
  if (!count) { toast("No imported assets to clear", "info"); return; }
  document.getElementById("confirm-clear-all-msg").textContent =
    `All ${count} imported asset${count !== 1 ? "s" : ""} will be permanently deleted from local assets.`;
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
      toast(`Deleted ${res.deleted} imported asset${res.deleted !== 1 ? "s" : ""}`, "warning", 4000);
      if (nav.level >= 2) renderBrowse().catch(() => {});
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

// ── initial load ──────────────────────────────────────────────────────────────
async function init() {
  renderBreadcrumbs();
  await checkPrereqs();
  await renderGrid();
  await loadSidebar();
}

init();
