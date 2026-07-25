/* Rom Sync - interface */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
  return data;
};

function bytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 2 : n < 100 ? 1 : 0)) + " " + u[i];
}
const num = (n) => (n || 0).toLocaleString();
const ago = (t) => {
  if (!t) return "never";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
};
function duration(sec) {
  if (sec < 60) return "under a minute";
  const m = Math.round(sec / 60);
  if (m < 60) return m + " min";
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/* ---------------------------------------------------------------- tree */
const tree = $("#tree");
const tpl = $("#row-tpl");
const nodes = new Map();         // path -> element
const open = new Set([""]);      // expanded folders
let searchMode = false;

/* column sorting: click a header for ascending, again for descending, a third time for name order */
let sort = { key: null, dir: 1 };
try { sort = JSON.parse(localStorage.getItem("sort")) || sort; } catch (e) {}
const coll = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const sortValue = {
  name: (n) => n.name,
  files: (n) => (n.files ? n.files : null),
  bytes: (n) => (n.files ? n.bytes : null),
  newest: (n) => n.newest || null,
  synced: (n) => (n.files ? n.synced_bytes / (n.bytes || 1) + n.synced / n.files / 1e6 : null),
};
function compareNodes(a, b) {
  const n1 = a._n, n2 = b._n;
  if (!sort.key) return a._i - b._i;                     // the server's order (by name)
  const v1 = sortValue[sort.key](n1), v2 = sortValue[sort.key](n2);
  if (v1 === null || v2 === null) {                       // empty folders always go last
    if (v1 === v2) return a._i - b._i;
    return v1 === null ? 1 : -1;
  }
  const c = typeof v1 === "string" ? coll.compare(v1, v2) : v1 - v2;
  return c ? c * sort.dir : a._i - b._i;
}
function reorder(container) {
  const els = [...container.children].filter((c) => c._n);
  const sorted = els.slice().sort(compareNodes);
  if (sorted.some((el, i) => el !== els[i])) for (const el of sorted) container.append(el);
}
function reorderAll() {
  reorder(tree);
  for (const el of nodes.values()) if (open.has(el.dataset.path)) reorder($(".kids", el));
}
function showSort() {
  for (const b of document.querySelectorAll(".cols .sort")) {
    const on = b.dataset.sort === sort.key;
    b.classList.toggle("asc", on && sort.dir === 1);
    b.classList.toggle("desc", on && sort.dir === -1);
    b.setAttribute("aria-sort", on ? (sort.dir === 1 ? "ascending" : "descending") : "none");
  }
}
for (const b of document.querySelectorAll(".cols .sort")) {
  b.addEventListener("click", () => {
    const k = b.dataset.sort;
    if (sort.key !== k) sort = { key: k, dir: 1 };
    else if (sort.dir === 1) sort = { key: k, dir: -1 };
    else sort = { key: null, dir: 1 };
    try { localStorage.setItem("sort", JSON.stringify(sort)); } catch (e) {}
    showSort();
    reorderAll();
  });
}
showSort();

function fillRow(el, n, depth, showPath) {
  el._n = n;
  el.dataset.path = n.path;
  el.style.setProperty("--depth", depth);
  const row = el.firstElementChild;
  row.style.setProperty("--depth", depth);
  const name = $(".name", row);
  name.textContent = n.name;
  if (showPath && n.path.includes("/")) {
    const p = document.createElement("span");
    p.className = "path";
    p.textContent = n.path.slice(0, n.path.lastIndexOf("/"));
    name.append(p);
  }
  name.title = n.error ? `${n.path}\nCould not be read: ${n.error}` : n.path;
  $(".files", row).textContent = n.files ? num(n.files) : "";
  $(".size", row).textContent = n.files ? bytes(n.bytes) : "";
  $(".date", row).textContent = n.newest || "";
  const tick = $(".tick", row);
  tick.checked = n.state === "on";
  tick.indeterminate = n.state === "partial";
  tick.dataset.state = n.state;
  tick.setAttribute("aria-label", `Sync ${n.path || "everything"}`);
  el.classList.toggle("sel", n.state !== "off");
  el.classList.toggle("err", !!n.error);
  const meter = $(".c-meter", row);
  const pct = n.files ? n.synced / n.files : 0;
  meter.style.visibility = n.files ? "visible" : "hidden";
  $(".fill", meter).style.width = (pct * 100).toFixed(1) + "%";
  $(".pct", meter).textContent = n.files ? (n.synced === n.files ? "all" : n.synced ? Math.floor(pct * 100) + "%" : "") : "";
  meter.classList.toggle("has-failed", n.failed > 0);
  meter.title = n.files ? `${num(n.synced)} of ${num(n.files)} files synced (${bytes(n.synced_bytes)} of ${bytes(n.bytes)})`
    + (n.failed ? `\n${num(n.failed)} failed` : "") : "";
  const tw = $(".twisty", row);
  tw.classList.toggle("leaf", !n.has_children || searchMode);
  tw.setAttribute("aria-label", open.has(n.path) ? "Collapse" : "Expand");
  el.setAttribute("aria-expanded", n.has_children ? String(open.has(n.path)) : "false");
  $(".info", row).hidden = !n.own_files;
}

function makeNode(n, depth, showPath = false) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  fillRow(el, n, depth, showPath);
  const row = el.firstElementChild;
  $(".twisty", row).addEventListener("click", () => toggle(el));
  $(".name", row).addEventListener("click", () => toggle(el));
  // clicking a "-" box ticks the whole folder (browsers would otherwise flip it to unticked)
  $(".tick", row).addEventListener("change", (e) =>
    select(el.dataset.path, e.target.dataset.state === "partial" ? true : e.target.checked));
  $(".info", row).addEventListener("click", () => showFiles(el.dataset.path));
  return el;
}

/* a folder with files of its own *and* subfolders gets a first row for just its own files, so they
   can be ticked without ticking every subfolder */
function ownRow(node, depth) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.classList.add("own-files");
  el.dataset.path = node.path + "/.";
  $(".twisty", el.firstElementChild).classList.add("leaf");
  $(".tick", el).addEventListener("change", (e) => select(node.path + "/.", e.target.checked));
  $(".name", el).addEventListener("click", () => showFiles(node.path));
  $(".info", el).addEventListener("click", () => showFiles(node.path));
  fillOwnRow(el, node, depth);
  return el;
}
function fillOwnRow(el, n, depth) {
  const row = el.firstElementChild;
  row.style.setProperty("--depth", depth);
  const name = $(".name", row);
  name.textContent = "Files in this folder";
  name.title = `The ${num(n.own_files)} files directly in ${n.path}, without its subfolders`;
  $(".files", row).textContent = num(n.own_files);
  $(".size", row).textContent = bytes(n.own_bytes);
  $(".date", row).textContent = n.own_newest || "";
  const tick = $(".tick", row);
  tick.checked = n.own_state === "on";
  tick.indeterminate = false;
  tick.setAttribute("aria-label", `Sync the files directly in ${n.path}`);
  el.classList.toggle("sel", n.own_state === "on");
  const meter = $(".c-meter", row);
  const pct = n.own_files ? n.own_synced / n.own_files : 0;
  $(".fill", meter).style.width = (pct * 100).toFixed(1) + "%";
  $(".pct", meter).textContent = n.own_synced === n.own_files ? "all" : n.own_synced ? Math.floor(pct * 100) + "%" : "";
  meter.classList.toggle("has-failed", n.own_failed > 0);
  meter.title = `${num(n.own_synced)} of ${num(n.own_files)} files synced`;
}

async function loadChildren(path, container, depth) {
  container.innerHTML = `<div class="loading" style="--depth:${depth}">Loading…</div>`;
  const data = await api(`/api/tree?path=${encodeURIComponent(path)}`);
  container.innerHTML = "";
  if (path && data.node.own_files && data.children.length) container.append(ownRow(data.node, depth));
  for (const [i, c] of data.children.entries()) {
    const el = makeNode(c, depth);
    el._i = i;
    nodes.set(c.path, el);
    container.append(el);
    if (open.has(c.path) && c.has_children) {
      el.classList.add("open");
      const kids = $(".kids", el);
      kids.hidden = false;
      loadChildren(c.path, kids, depth + 1);
    }
  }
  reorder(container);
  return data;
}

async function toggle(el) {
  if (searchMode) return;
  const path = el.dataset.path;
  const kids = $(".kids", el);
  if ($(".twisty", el.firstElementChild).classList.contains("leaf")) return;
  if (open.has(path)) {
    open.delete(path);
    el.classList.remove("open");
    kids.hidden = true;
  } else {
    open.add(path);
    el.classList.add("open");
    kids.hidden = false;
    await loadChildren(path, kids, Number(el.style.getPropertyValue("--depth")) + 1);
  }
  el.setAttribute("aria-expanded", String(open.has(path)));
}

async function renderRoot() {
  nodes.clear();
  const data = await loadChildren("", tree, 0);
  $("#empty").hidden = data.children.length > 0;
  tree.hidden = data.children.length === 0;
}

/* refresh the numbers of every visible row without rebuilding the tree */
async function refreshVisible() {
  if (searchMode) return runSearch();
  const parents = new Set([""]);
  for (const p of open) parents.add(p);
  for (const p of parents) {
    if (p !== "" && !nodes.has(p)) continue;
    let data;
    try { data = await api(`/api/tree?path=${encodeURIComponent(p)}`); } catch { continue; }
    const container = p === "" ? tree : $(".kids", nodes.get(p));
    const own = container.querySelector(":scope > .own-files");
    if (own) fillOwnRow(own, data.node, Number(own.firstElementChild.style.getPropertyValue("--depth")));
    const known = new Set([...container.children].filter((c) => c._n).map((c) => c.dataset.path));
    const fresh = new Set(data.children.map((c) => c.path));
    const same = known.size === fresh.size && [...fresh].every((x) => known.has(x));
    if (!same) {                         // folders appeared / disappeared (after a scan)
      if (p === "") { await renderRoot(); return; }
      await loadChildren(p, container, Number(nodes.get(p).style.getPropertyValue("--depth")) + 1);
      continue;
    }
    for (const [i, c] of data.children.entries()) {
      const el = nodes.get(c.path);
      if (el) { fillRow(el, c, Number(el.style.getPropertyValue("--depth"))); el._i = i; }
    }
    reorder(container);
  }
}

async function select(path, include) {
  try {
    await api("/api/select", { method: "POST", body: { path, include } });
  } catch (e) { alert("Could not change the selection: " + e.message); }
  await refreshVisible();
  poll(true);
}

/* ---------------------------------------------------------------- search */
let searchTimer;
const search = $("#search");
search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 200);
});
async function runSearch() {
  const q = search.value.trim();
  if (!q) {
    if (searchMode) { searchMode = false; await renderRoot(); }
    return;
  }
  searchMode = true;
  const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
  tree.innerHTML = "";
  nodes.clear();
  if (!data.results.length) {
    tree.innerHTML = `<div class="loading">No folder matches “${q.replace(/[<&]/g, "")}”.</div>`;
    return;
  }
  for (const [i, r] of data.results.entries()) {
    const el = makeNode(r, 0, true);
    el._i = i;
    nodes.set(r.path, el);
    tree.append(el);
  }
  reorder(tree);
}

/* ---------------------------------------------------------------- files dialog */
async function showFiles(path) {
  const dlg = $("#files-dlg");
  $("#files-title").textContent = path || "lolroms.com";
  $("#files-body").innerHTML = "";
  $("#files-sub").textContent = "Loading…";
  dlg.showModal();
  const data = await api(`/api/files?path=${encodeURIComponent(path)}`);
  $("#files-sub").textContent = `${num(data.total)} files directly in this folder` +
    (data.total > data.files.length ? ` (first ${num(data.files.length)} shown)` : "");
  const body = $("#files-body");
  for (const f of data.files) {
    const tr = document.createElement("tr");
    const status = f.local ? (f.local === "already in folder" ? "in folder" : f.local === "up to date" ? "synced" : f.local)
      : f.error ? "failed" : "not synced";
    const cls = f.local ? "st-ok" : f.error ? "st-bad" : "st-none";
    for (const v of [f.name, f.date || "", f.size || ""]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    const td = document.createElement("td");
    td.textContent = status; td.className = cls;
    if (f.error) td.title = f.error;
    tr.append(td);
    body.append(tr);
  }
}

/* ---------------------------------------------------------------- status / activity */
let lastLog = 0;
let pollTimer;
let wasBusy = false;
let lastTreeRefresh = 0;

function setStats(el, pairs) {
  el.hidden = false;
  el.innerHTML = "";
  for (const [label, value, cls] of pairs) {
    const d = document.createElement("div");
    d.innerHTML = `<span class="muted"></span> <b></b>`;
    d.firstChild.textContent = label;
    d.lastChild.textContent = value;
    if (cls) d.lastChild.className = cls;
    el.append(d);
  }
}

function renderStatus(st) {
  const ver = $("#version");
  if (st.version && ver.dataset.v !== st.version) {
    ver.dataset.v = st.version;
    ver.textContent = "v" + st.version;
    ver.href = `https://github.com/sireteanu/rom-sync/releases/tag/v${st.version}`;
  }
  const busy = st.busy;
  const job = st.job;
  const pill = $("#jobpill");
  const syncing = st.sync && job && job.phase === "downloading" && busy;
  pill.className = "pill " + (busy ? "busy" : st.sync && st.sync.paused ? "warn" : "idle");
  pill.textContent = busy ? (job.kind === "scan" ? "Scanning" : "Syncing") :
    (st.sync && st.sync.paused ? "Paused" : "Idle");
  $("#btn-scan").disabled = busy;
  $("#btn-sync").disabled = busy || !st.selection.dirs;
  $("#btn-sync").title = st.selection.dirs ? "Download new, incomplete and changed files in the ticked folders"
    : "Tick the folders you want first";
  $("#btn-stop").disabled = !busy;

  // storage strip
  const sel = st.selection;
  const todo = Math.max(0, sel.bytes - sel.synced_bytes);
  $("#sel-summary").textContent = sel.files
    ? `${bytes(sel.bytes)} selected in ${num(sel.files)} files`
    : sel.dirs ? `${num(sel.dirs)} folders selected` : "Nothing selected yet";
  const cat = st.catalog;
  $("#cat-summary").textContent = cat.files
    ? `Whole site: ${num(cat.dirs)} folders, ${num(cat.files)} files, ${bytes(cat.bytes)}. Last full scan ${ago(cat.last_full_scan && cat.last_full_scan.finished)}.`
    : "";
  const total = sel.bytes || 1;
  $("#bar-synced").style.width = (100 * sel.synced_bytes / total) + "%";
  $("#bar-todo").style.width = (100 * todo / total) + "%";
  $("#lg-synced").textContent = `Synced ${bytes(sel.synced_bytes)} (${num(sel.synced)} files)`;
  $("#lg-todo").textContent = `To download ${bytes(todo)} (${num(Math.max(0, sel.files - sel.synced))} files)`;
  $("#lg-dest").textContent = `Saving to ${st.data_dir}`;

  // job
  const line = $("#job-line");
  if (job) {
    const what = { scan: "Scan", sync: "Sync", "scan+sync": "Scan and sync" }[job.kind] || job.kind;
    line.textContent = busy
      ? `${what}: ${job.phase}${job.root ? " (" + job.root + ")" : ""} - started ${clock(job.started)}`
      : `Last ${what.toLowerCase()} ${job.phase === "done" ? "finished" : job.phase} at ${clock(job.finished || job.started)}`
        + (job.message ? `: ${job.message}` : "");
  } else line.textContent = "No job running.";

  const sc = $("#scan-stats");
  if (st.scan && busy) {
    setStats(sc, [["Pages read", num(st.scan.pages)], ["Waiting", num(st.scan.queued)],
      ["Files found", num(st.scan.files)], ["Failed pages", num(st.scan.errors), st.scan.errors ? "bad" : ""],
      ["Speed", `${st.scan.rate} pages/s`], ["Parallel", `${st.scan.parallel}, ${st.scan.delay}s apart`]]);
  } else sc.hidden = true;

  const sy = $("#sync-stats");
  renderDownloads(st.sync, syncing);
  if (st.sync) {
    const s = st.sync;
    const uptodate = (s["up to date"] || 0) + (s["already in folder"] || 0);
    setStats(sy, [["New", num(s.new)], ["Updated", num(s.updated), s.updated ? "warn" : ""],
      ["Resumed", num(s.resumed)], ["Up to date", num(uptodate)],
      ["Failed", num(s.failed), s.failed ? "bad" : ""], ["Downloaded", bytes(s.bytes)],
      ["Speed", syncing ? bytes(s.speed) + "/s" : "-"],
      ["Parallel", syncing ? `${s.running} (${String(s.gate).replace("at --dl-max", "at your upper limit")})` : "-"]]);
  } else sy.hidden = true;
  const paused = $("#sync-paused");
  paused.hidden = !(st.sync && st.sync.paused);
  if (!paused.hidden) paused.textContent = "Paused: " + st.sync.paused;
  renderEta(st, busy);
  $("#next-run").textContent = st.next_run ? `Next automatic sync at ${new Date(st.next_run * 1000).toLocaleString()}` : "";

  // log
  const log = $("#log");
  for (const l of st.log) {
    const li = document.createElement("li");
    li.className = l.level;
    li.innerHTML = "<time></time><span></span>";
    li.firstChild.textContent = clock(l.t);
    li.lastChild.textContent = l.msg;
    log.append(li);
    lastLog = l.id;
  }
  while (log.children.length > 300) log.firstChild.remove();
  if (st.log.length) log.scrollTop = log.scrollHeight;

  // keep tree numbers fresh while something is happening, and once when it ends
  const now = Date.now();
  if ((busy && now - lastTreeRefresh > 4000) || (wasBusy && !busy)) {
    lastTreeRefresh = now;
    refreshVisible();
  }
  wasBusy = busy;
}

/* files being downloaded right now: one row each, kept between polls so bars move smoothly */
const dlRows = new Map();      // rel -> {el, have, t, speed}
function renderDownloads(s, syncing) {
  const card = $("#dl-card"), box = $("#downloads");
  const active = (s && s.active) || [];
  card.hidden = !syncing && !active.length;
  if (card.hidden) { box.innerHTML = ""; dlRows.clear(); return; }
  $("#dl-count").textContent = active.length ? `${active.length} ${active.length === 1 ? "file" : "files"}` : "";
  const now = Date.now() / 1000, seen = new Set();
  for (const a of active) {
    seen.add(a.rel);
    let r = dlRows.get(a.rel);
    if (!r) {
      const el = document.createElement("div");
      el.className = "dl";
      el.innerHTML = `<div class="dl-top"><span class="dl-name"></span><span class="dl-pct"></span></div>
        <div class="dl-dir"></div><span class="meter"><span class="fill"></span></span><div class="dl-size"></div>`;
      const slash = a.rel.lastIndexOf("/");
      $(".dl-name", el).textContent = a.rel.slice(slash + 1);
      $(".dl-dir", el).textContent = slash > 0 ? a.rel.slice(0, slash) : "";
      el.title = a.rel;
      r = { el, have: a.have, t: now, speed: 0 };
      dlRows.set(a.rel, r);
    } else if (now > r.t) {
      const inst = Math.max(0, (a.have - r.have) / (now - r.t));
      r.speed = r.speed ? r.speed * 0.6 + inst * 0.4 : inst;       // smoothed per-file speed
      r.have = a.have; r.t = now;
    }
    box.append(r.el);                                               // keeps the server's order
    const pct = a.total ? Math.min(100, 100 * a.have / a.total) : 0;
    $(".fill", r.el).style.width = pct.toFixed(1) + "%";
    $(".dl-pct", r.el).textContent = a.total ? Math.floor(pct) + "%" : "";
    let size = a.total ? `${bytes(a.have)} of ${bytes(a.total)}` : bytes(a.have);
    if (r.speed > 0) {
      size += ` - ${bytes(r.speed)}/s`;
      if (a.total && a.total > a.have) size += `, ${duration((a.total - a.have) / r.speed)} left`;
    }
    $(".dl-size", r.el).textContent = size;
  }
  for (const [rel, r] of dlRows) if (!seen.has(rel)) { r.el.remove(); dlRows.delete(rel); }
  if (!active.length) {
    if (!box.querySelector(".dl-wait")) box.innerHTML = `<p class="muted small dl-wait">Waiting for the next file…</p>`;
  } else { const w = box.querySelector(".dl-wait"); if (w) w.remove(); }
}

function renderEta(st, busy) {
  const box = $("#eta-box");
  const s = st.sync;
  if (!s || !s.to_fetch) { box.hidden = true; return; }
  box.hidden = false;
  const total = s.bytes_planned || 0;
  const left = Math.min(s.bytes_left || 0, total);
  $("#eta-total").textContent = `${bytes(total)} in ${num(s.to_fetch)} ${s.to_fetch === 1 ? "file" : "files"}`;
  const downloading = busy && st.job && st.job.phase === "downloading";
  let time;
  if (downloading && s.paused) time = "Paused";
  else if (downloading) time = s.eta != null ? duration(s.eta) : "Estimating…";
  else time = s.files_left ? "Stopped" : "Finished";
  $("#eta-time").textContent = time;
  $("#eta-bar").style.width = (total ? 100 * (total - left) / total : 100) + "%";
  let sub = `${bytes(total - left)} of ${bytes(total)} done, ${num(s.files_left)} ${s.files_left === 1 ? "file" : "files"} left`;
  if (downloading && s.eta != null && !s.paused) sub += ` - done around ${clock(Date.now() / 1000 + s.eta)}`;
  $("#eta-sub").textContent = sub;
}

async function poll(once = false) {
  clearTimeout(pollTimer);
  let busy = false;
  try {
    const st = await api(`/api/status?log_since=${lastLog}`);
    busy = st.busy;
    renderStatus(st);
  } catch (e) {
    $("#job-line").textContent = "Lost contact with Rom Sync - is it still running?";
  }
  if (!once || true) pollTimer = setTimeout(poll, busy ? 1000 : 4000);
}

/* ---------------------------------------------------------------- buttons */
async function startJob(path, body) {
  try { await api(path, { method: "POST", body }); }
  catch (e) { alert(e.message === "already busy" ? "Something is already running - stop it first." : e.message); }
  poll(true);
}
$("#btn-scan").addEventListener("click", () => startJob("/api/scan", { root: "" }));
$("#btn-scan-empty").addEventListener("click", () => startJob("/api/scan", { root: "" }));
$("#btn-sync").addEventListener("click", () => startJob("/api/sync"));
$("#btn-stop").addEventListener("click", () => startJob("/api/stop"));
$("#btn-clear-sel").addEventListener("click", async () => {
  if (!confirm("Untick every folder? Files already downloaded stay where they are.")) return;
  await api("/api/selection/clear", { method: "POST" });
  await refreshVisible();
  poll(true);
});

/* ---------------------------------------------------------------- settings */
const form = $("#settings-form");
function syncTypeFields() {
  form.types_custom.hidden = form.types_mode.value !== "custom";
  $("#par-label").textContent = form.parallel_mode.value === "auto" ? "Upper limit" : "Number of downloads";
}
form.types_mode.addEventListener("change", syncTypeFields);
form.parallel_mode.addEventListener("change", syncTypeFields);

$("#btn-settings").addEventListener("click", async () => {
  const { settings: s, data_locked } = await api("/api/settings");
  form.data_dir.value = s.data_dir;
  form.data_dir.readOnly = data_locked;
  $("#data-hint").textContent = data_locked
    ? "Set by the container's /data mapping - change it in the Docker template."
    : "Synced files are saved here, keeping the site's folder structure.";
  const t = String(s.types || "archives");
  form.types_mode.value = t === "archives" || t === "all" ? t : "custom";
  form.types_custom.value = form.types_mode.value === "custom" ? t : "";
  const auto = String(s.parallel) === "auto";
  form.parallel_mode.value = auto ? "auto" : "fixed";
  form.parallel_n.value = auto ? s.parallel_max : s.parallel;
  form.max_rate.value = s.max_rate || "";
  form.min_free.value = s.min_free || "";
  form.file_mode.value = s.file_mode || "";
  form.sync_every_hours.value = s.sync_every_hours;
  form.full_scan_every_days.value = s.full_scan_every_days;
  form.refresh_before_sync.checked = !!s.refresh_before_sync;
  form.check_remote.checked = !!s.check_remote;
  form.resume_interrupted.checked = s.resume_interrupted !== false;
  $("#settings-error").hidden = true;
  syncTypeFields();
  $("#settings-dlg").showModal();
});

$("#settings-save").addEventListener("click", async (e) => {
  e.preventDefault();
  const auto = form.parallel_mode.value === "auto";
  const body = {
    types: form.types_mode.value === "custom" ? form.types_custom.value : form.types_mode.value,
    parallel: auto ? "auto" : form.parallel_n.value,
    max_rate: form.max_rate.value.trim(),
    min_free: form.min_free.value.trim() || "0",
    file_mode: form.file_mode.value.trim(),
    sync_every_hours: form.sync_every_hours.value || 0,
    full_scan_every_days: form.full_scan_every_days.value || 0,
    refresh_before_sync: form.refresh_before_sync.checked,
    check_remote: form.check_remote.checked,
    resume_interrupted: form.resume_interrupted.checked,
  };
  if (auto) body.parallel_max = form.parallel_n.value || 16;
  if (!form.data_dir.readOnly) body.data_dir = form.data_dir.value.trim();
  try {
    await api("/api/settings", { method: "POST", body });
    $("#settings-dlg").close();
    await refreshVisible();
    poll(true);
  } catch (err) {
    const p = $("#settings-error");
    p.textContent = "Not saved: " + err.message;
    p.hidden = false;
  }
});

/* ---------------------------------------------------------------- restart */
/* power menu: Restart / Shut down */
const powerBtn = $("#btn-power"), powerMenu = $("#power-menu");
function powerOpen(open) {
  powerMenu.hidden = !open;
  powerBtn.setAttribute("aria-expanded", String(open));
  if (open) powerMenu.querySelector("button").focus();
}
powerBtn.addEventListener("click", (e) => { e.stopPropagation(); powerOpen(powerMenu.hidden); });
document.addEventListener("click", (e) => { if (!powerMenu.hidden && !powerMenu.contains(e.target)) powerOpen(false); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !powerMenu.hidden) { powerOpen(false); powerBtn.focus(); } });

$("#btn-shutdown").addEventListener("click", async () => {
  powerOpen(false);
  if (!confirm("Shut down Rom Sync? A sync that is running continues the next time you start it.")) return;
  let docker = false;
  try { docker = !!(await api("/api/shutdown", { method: "POST" })).docker; }
  catch (e) { alert("Could not shut down: " + e.message); return; }
  clearTimeout(pollTimer);
  $("#shutdown-how").textContent = docker
    ? "Start it again in Unraid: Docker tab, rom-sync, Start (or Compose Up on its stack)."
    : "Start it again with start.bat.";
  $("#shutdown").hidden = false;
});

$("#btn-restart").addEventListener("click", async () => {
  powerOpen(false);
  if (!confirm("Restart Rom Sync? A sync that is running carries on by itself afterwards.")) return;
  try { await api("/api/restart", { method: "POST" }); }
  catch (e) { alert("Could not restart: " + e.message); return; }
  $("#restarting").hidden = false;
  clearTimeout(pollTimer);
  const t0 = Date.now();
  await new Promise((r) => setTimeout(r, 1500));
  while (Date.now() - t0 < 120000) {
    try { const r = await fetch("/api/health", { cache: "no-store" }); if (r.ok) { location.reload(); return; } } catch (e) {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  $("#restarting").querySelector("p").textContent = "It hasn't come back after 2 minutes - check the window or container it runs in.";
});

/* ---------------------------------------------------------------- RomM */
let romm = null;
let rommTab = "compose";
const ROMM_HELP_LINKS = {
  compose: "For RomM's container: map the links folder as RomM's roms folder (replace its .../romm/library/roms line).",
};
const ROMM_HELP = {
  compose: "For RomM's container: add these under the RomM service in its docker-compose.yml (next to its other volumes).",
  config: "Merge into RomM's config.yml (in its /romm/config folder), keeping your other settings.",
  list: "Every platform RomM knows. Type one of these names into a folder's RomM platform box; click a name to copy it.",
};
function rommResult(r) {
  if (r.kind === "platform") return [r.used ? `roms/${r.slug}` : `roms/${r.slug} (nothing synced)`, r.custom ? "warn" : "ok"];
  if (r.kind === "merge") return [r.used ? `in ${r.slug}` : `in ${r.slug} (nothing synced)`, "ok"];
  if (r.kind === "bios") return [`firmware: bios/${r.slug}`, "ok"];
  if (r.kind === "skip") return ["skipped", "none"];
  return ["no platform", "bad"];
}
function renderRomm() {
  const d = romm;
  const f = $("#romm-filter").value, q = $("#romm-find").value.trim().toLowerCase();
  const body = $("#romm-body");
  body.innerHTML = "";
  const rows = d.rows.filter((r) => (f === "all" || (f === "used" ? r.used : (r.kind === "unmapped" && r.files)))
    && (!q || r.path.toLowerCase().includes(q)));
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="p"></td><td class="n"></td><td><input class="act" list="romm-slugs" spellcheck="false"></td><td class="res"></td>`;
    tr.children[0].textContent = r.path;
    tr.children[0].title = r.path;
    tr.children[1].textContent = r.synced && r.synced !== r.files ? `${num(r.synced)} / ${num(r.files)}` : num(r.files);
    const inp = $(".act", tr);
    inp.value = r.action;
    inp.placeholder = r.rule || (r.kind === "merge" ? "merge" : r.kind === "skip" ? "skip" : r.kind === "bios" ? "bios:" + r.slug : "");
    inp.setAttribute("aria-label", `RomM platform for ${r.path}`);
    inp.addEventListener("change", () => rommMap(r.path, inp.value));
    const [text, cls] = rommResult(r);
    const res = tr.children[3];
    res.textContent = text;
    res.className = "res st-" + cls;
    if (r.kind === "unmapped" && r.suggest) {
      const b = document.createElement("button");
      b.className = "btn small ghost"; b.textContent = "Use";
      b.addEventListener("click", () => rommMap(r.path, r.suggest));
      res.append(" ", b);
    }
    body.append(tr);
  }
  if (!rows.length) body.innerHTML = `<tr><td colspan="4" class="muted">${f === "todo" ? "Every folder with files has a platform or is skipped." : "No folders."}</td></tr>`;
  const s = d.summary;
  $("#romm-count").textContent = `${num(s.platforms)} platforms, ${num(s.firmware)} firmware, ${num(s.files)} files`;
  const w = $("#romm-warn");
  w.innerHTML = "";
  for (const t of d.warnings) { const li = document.createElement("li"); li.textContent = t; w.append(li); }
  const isList = rommTab === "list";
  $("#romm-out").hidden = isList;
  $("#romm-list").hidden = !isList;
  $("#romm-copy").hidden = isList;
  if (isList) renderRommList(); else $("#romm-out").textContent = d.outputs[rommTab];
  $("#romm-help").textContent = (d.options.method === "links" && ROMM_HELP_LINKS[rommTab]) || ROMM_HELP[rommTab];
  const links = d.options.method === "links";
  $("#romm-links").hidden = !links;
  if (links) {
    $("#romm-linkdir").placeholder = d.links.dir || "";
    $("#romm-linkdir-hint").textContent = d.links.inside
      ? `Links go in ${d.links.dir}, next to "${d.links.inside}" (the download folder ${d.links.download_dir}). Map ${d.links.dir}'s server folder as RomM's roms folder.`
      : `The download folder (${d.links.download_dir}) must be inside the links folder.`;
  }
  for (const t of document.querySelectorAll("#romm-dlg .tab")) t.classList.toggle("on", t.dataset.out === rommTab);
}
function renderRommList() {
  const q = $("#romm-list-find").value.trim().toLowerCase();
  const used = {};
  for (const p of romm.platforms) used[p.slug] = p.folders;
  const body = $("#romm-list-body");
  body.innerHTML = "";
  for (const p of romm.slugs) {
    if (q && !`${p.slug} ${p.name} ${p.family}`.toLowerCase().includes(q)) continue;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><button class="slug" title="Copy"></button></td><td></td><td></td><td class="p"></td>`;
    const b = tr.querySelector(".slug");
    b.textContent = p.slug;
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(p.slug); b.classList.add("copied"); setTimeout(() => b.classList.remove("copied"), 1000); } catch (e) {}
    });
    tr.children[1].textContent = p.name;
    tr.children[2].textContent = p.family;
    const f = used[p.slug] || [];
    tr.children[3].textContent = f.join(", ");
    tr.children[3].title = f.join("\n");
    if (f.length) tr.classList.add("in-use");
    body.append(tr);
  }
}
$("#romm-list-find").addEventListener("input", renderRommList);

function rommLoad(d) {
  romm = d;
  $("#romm-host").value = d.options.host_path || "";
  $("#romm-lib").value = d.options.library || "";
  $("#romm-only").checked = !!d.options.only_synced;
  $("#romm-var").checked = !!d.options.variants;
  $("#romm-layout").value = d.options.layout || "category";
  $("#romm-method").value = d.options.method || "mounts";
  $("#romm-linkdir").value = d.options.links_dir || "";
  const dl = $("#romm-slugs");
  if (!dl.children.length) {
    for (const v of ["skip", "merge"]) { const o = document.createElement("option"); o.value = v; dl.append(o); }
    for (const p of d.slugs) { const o = document.createElement("option"); o.value = p.slug; o.label = p.name; dl.append(o); }
  }
  renderRomm();
}
async function rommMap(path, action) {
  try { rommLoad(await api("/api/romm/map", { method: "POST", body: { path, action } })); }
  catch (e) { alert(e.message); renderRomm(); }
}
async function rommOptions() {
  rommLoad(await api("/api/romm/options", { method: "POST", body: {
    host_path: $("#romm-host").value, library: $("#romm-lib").value,
    only_synced: $("#romm-only").checked, variants: $("#romm-var").checked,
    layout: $("#romm-layout").value, method: $("#romm-method").value,
    links_dir: $("#romm-linkdir").value } }));
}
$("#btn-romm").addEventListener("click", async () => {
  $("#romm-dlg").showModal();
  $("#romm-out").textContent = "Loading…";
  rommLoad(await api("/api/romm"));
});
$("#romm-apply").addEventListener("click", async () => {
  const b = $("#romm-apply"), out = $("#romm-applied");
  b.disabled = true;
  try {
    const d = await api("/api/romm/links", { method: "POST" });
    rommLoad(d);
    out.textContent = d.applied.text;
    out.className = "small " + (d.applied.errors.length ? "error" : "muted");
    out.hidden = false;
  } catch (e) { out.textContent = "Could not update the links: " + e.message; out.className = "small error"; out.hidden = false; }
  b.disabled = false;
});
for (const id of ["#romm-host", "#romm-lib", "#romm-only", "#romm-var", "#romm-layout", "#romm-method", "#romm-linkdir"]) $(id).addEventListener("change", rommOptions);
$("#romm-filter").addEventListener("change", renderRomm);
$("#romm-find").addEventListener("input", renderRomm);
for (const t of document.querySelectorAll("#romm-dlg .tab")) t.addEventListener("click", () => { rommTab = t.dataset.out; renderRomm(); });
$("#romm-copy").addEventListener("click", async () => {
  const text = $("#romm-out").textContent;
  try { await navigator.clipboard.writeText(text); }
  catch (e) {                                   // plain http on a LAN address: no clipboard API
    const r = document.createRange(); r.selectNodeContents($("#romm-out"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand("copy"); sel.removeAllRanges();
  }
  $("#romm-copy").textContent = "Copied";
  setTimeout(() => ($("#romm-copy").textContent = "Copy"), 1500);
});

/* ---------------------------------------------------------------- theme */
$("#btn-theme").addEventListener("click", () => {
  const light = document.documentElement.dataset.theme !== "light";
  if (light) document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("theme", light ? "light" : "dark"); } catch (e) {}
});

/* ---------------------------------------------------------------- start */
renderRoot().then(() => poll());
