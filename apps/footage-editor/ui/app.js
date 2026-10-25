// Footage Editor presentation: everything comes from the Project API of the local service (core/src/media_analysis/
// workflows/edit/api.py). Changes to the edit happen only through the chat. The host window (WebView2) helps with
// what a page can't do: picking files and giving their paths.

const $ = (id) => document.getElementById(id);
const host = window.chrome?.webview;
const token = new URLSearchParams(location.search).get("token") || "";

// ---------- API ----------------------------------------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json", ...(token ? { "X-Token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.status === 204 ? null : res.json();
}

function toast(text, ms = 4000) {
  const t = $("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}

const fmt = (t) => { t = Math.max(0, t); const m = Math.floor(t / 60), s = t - m * 60; return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`; };
const fmtSrc = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let profiles = [];

// ---------- home ---------------------------------------------------------------------------------------------------

async function showHome() {
  history.replaceState(null, "", `${location.pathname}${location.search}`);
  closeEvents();
  pause();
  $("project").hidden = true;
  $("home").hidden = false;
  const list = await api("GET", "/api/projects");
  $("noProjects").hidden = list.length > 0;
  $("projects").innerHTML = list.map((p) => {
    const profile = profiles.find((x) => x.id === p.profile_id)?.name || p.profile_id;
    const state = p.analysing ? "analysing…" : p.current_version ? `version ${p.current_version} of ${p.versions}` : "not analysed";
    return `<div class="card" data-id="${p.id}"><div class="name">${esc(p.name)}</div>
      <div class="meta">${esc(profile)}${p.max_seconds ? ` · max ${p.max_seconds} s` : ""}</div>
      <div class="meta">${p.files} file(s) · ${state}</div></div>`;
  }).join("");
  for (const card of document.querySelectorAll(".card")) card.addEventListener("click", () => openProject(card.dataset.id));
}

function fillProfiles(select, selected) {
  select.innerHTML = profiles.map((p) => `<option value="${p.id}" ${p.id === selected ? "selected" : ""}>${esc(p.name)}</option>`).join("");
}

$("settingsBtn").hidden = !host;
$("settingsBtn").addEventListener("click", () => host?.postMessage({ type: "settings" }));

$("newProjectBtn").addEventListener("click", () => {
  fillProfiles($("newProfile"), "generic");
  updateProfileHint();
  $("newProject").showModal();
});
function updateProfileHint() {
  const p = profiles.find((x) => x.id === $("newProfile").value);
  $("newProfileHint").textContent = p ? `${p.description} Target ${p.target_seconds} s${p.max_seconds ? `, at most ${p.max_seconds} s` : ""}.` : "";
}
$("newProfile").addEventListener("change", updateProfileHint);
$("newProject").addEventListener("close", async () => {
  if ($("newProject").returnValue !== "ok") return;
  const f = new FormData($("newProjectForm"));
  try {
    const p = await api("POST", "/api/projects", {
      name: f.get("name"), profile_id: f.get("profile"), max_seconds: f.get("max") ? Number(f.get("max")) : null,
    });
    $("newProjectForm").reset();
    openProject(p.id);
  } catch (e) { toast(e.message); }
});

// ---------- project ------------------------------------------------------------------------------------------------

const P = {
  id: null, info: null, footage: [], sources: [], edit: null, version: null, versions: [], chat: [],
};

async function openProject(id) {
  P.id = id;
  history.replaceState(null, "", `${location.pathname}${location.search}#p=${id}`);
  $("home").hidden = true;
  $("project").hidden = false;
  openEvents(id);
  await Promise.all([loadProject(), loadEdit(), loadChat()]);
}

async function loadProject() {
  P.info = await api("GET", `/api/projects/${P.id}`);
  P.footage = await api("GET", `/api/projects/${P.id}/footage`);
  $("projectName").textContent = P.info.name;
  fillProfiles($("profile"), P.info.profile_id);
  $("maxSeconds").value = P.info.max_seconds ?? "";
  $("maxSeconds").placeholder = profiles.find((x) => x.id === P.info.profile_id)?.max_seconds ?? "none";
  $("analyseBtn").textContent = P.info.analysing ? "Stop" : P.info.current_version ? "Analyse again" : "Analyse";
  setStatus(P.info.analysing ? "Analysing…" : "", P.info.analysing ? "busy" : "");
  renderSources();
}

async function loadEdit(version) {
  try {
    const data = await api("GET", `/api/projects/${P.id}/edit${version ? `?version=${version}` : ""}`);
    P.edit = data.edit; P.sources = data.sources; P.version = data.version;
  } catch {
    P.edit = null; P.sources = []; P.version = null;
  }
  try { P.versions = (await api("GET", `/api/projects/${P.id}/versions`)).versions; } catch { P.versions = []; }
  $("version").innerHTML = P.versions.map((v) =>
    `<option value="${v.version}" ${P.version && v.version === P.version.version ? "selected" : ""}>v${v.version} · ${esc(v.summary).slice(0, 60)}</option>`).join("");
  $("exportBtn").href = P.edit ? `/api/projects/${P.id}/export/edit.json${token ? `?token=${token}` : ""}` : "#";
  $("noEdit").hidden = !!P.edit;
  computeEdit();
  renderSources(); renderTimeline(); syncPlayer(true);
}

async function loadChat() {
  try { P.chat = await api("GET", `/api/projects/${P.id}/chat`); } catch { P.chat = []; }
  renderChat();
}

$("backBtn").addEventListener("click", showHome);

$("profile").addEventListener("change", async (e) => {
  try { await api("PATCH", `/api/projects/${P.id}`, { profile_id: e.target.value }); await loadProject(); toast("Profile changed. Analyse again to get a new suggestion."); }
  catch (err) { toast(err.message); }
});
$("maxSeconds").addEventListener("change", async (e) => {
  const v = e.target.value.trim();
  try {
    await api("PATCH", `/api/projects/${P.id}`, v ? { max_seconds: Number(v) } : { clear_max_seconds: true });
    await loadProject();
    toast("Duration limit changed. Analyse again to get a suggestion within it, or ask in the chat.");
  } catch (err) { toast(err.message); }
});
$("analyseBtn").addEventListener("click", async () => {
  try {
    if (P.info.analysing) { await api("POST", `/api/projects/${P.id}/stop`); }
    else { await api("POST", `/api/projects/${P.id}/analyse`); setStatus("Starting…", "busy"); }
    await loadProject();
  } catch (e) { toast(e.message); }
});
$("version").addEventListener("change", async (e) => {
  try { await api("POST", `/api/projects/${P.id}/versions/${e.target.value}/current`); await loadEdit(); }
  catch (err) { toast(err.message); }
});
$("undoBtn").addEventListener("click", async () => {
  try { await api("POST", `/api/projects/${P.id}/undo`); await loadEdit(); } catch (e) { toast(e.message); }
});

function setStatus(text, cls = "") {
  $("status").textContent = text;
  $("status").className = "status " + cls;
}

// ---------- live events --------------------------------------------------------------------------------------------

let ws = null;
const STAGES = { ingest: "Reading files", transcribe: "Transcribing", shots: "Finding shots", sound: "Measuring sound",
  describe: "Describing shots", prepare: "Making previews", sequence: "Planning the edit" };

function openEvents(id) {
  closeEvents();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/events/${id}${token ? `?token=${token}` : ""}`);
  ws.onmessage = (m) => onEvent(JSON.parse(m.data));
  ws.onclose = () => { if (P.id === id && !$("project").hidden) setTimeout(() => { if (P.id === id) openEvents(id); }, 2000); };
}
function closeEvents() { if (ws) { ws.onclose = null; ws.close(); ws = null; } }

function onEvent(e) {
  if (e.source === "job") {
    const stage = STAGES[e.stage] || e.stage;
    if (e.event === "progress" && e.total) setStatus(`${stage}… ${Math.round(100 * e.completed / e.total)}%`, "busy");
    else if (e.event === "activity") setStatus(`${stage}… (${e.message})`, "busy");
    else if (e.event === "stage_started") setStatus(`${stage}…`, "busy");
    else if (e.event === "error") setStatus(`Failed: ${e.message}`, "error");
    else if (e.event === "finished") setStatus(`Done: version ${e.version}, ${e.seconds} s`);
    else if (e.event === "job_exit") { loadProject(); loadEdit(); if (e.exit_code && !$("status").className.includes("error")) setStatus("Stopped"); }
  } else if (e.source === "project") {
    if (e.event === "edit_changed") loadEdit();
    if (e.event === "project_changed") loadProject();
  } else if (e.source === "chat") {
    onChatEvent(e);
  }
}

// ---------- footage panel ------------------------------------------------------------------------------------------

function renderSources() {
  const box = $("sources"); box.innerHTML = "";
  const byPath = new Map(P.sources.map((s) => [s.path, s]));
  const total = P.sources.reduce((a, s) => a + s.duration, 0);
  $("footageSummary").textContent = `${P.footage.length} clip${P.footage.length === 1 ? "" : "s"}${total ? ` · ${fmtSrc(total)} total` : ""}`;
  for (const f of P.footage) {
    const s = byPath.get(f.path);
    const uses = s && P.edit ? P.edit.tracks.filter((tr) => tr.kind === "video")
      .flatMap((tr) => tr.items.filter((it) => it.source === s.id).map((it) => ({ tr, it })))
      .sort((a, b) => a.it.at - b.it.at) : [];
    const el = document.createElement("div");
    el.className = "source" + (sourceMode && s && sourceMode.id === s.id ? " active" : "");
    const thumb = s?.strip ? `<div class="ph" style="background:url(${s.strip}) left/auto 100% no-repeat"></div>` : `<div class="ph">${s ? "" : "new"}</div>`;
    const meta = !f.exists ? `<span class="missing">file not found</span>` : s ? fmtSrc(s.duration) : "not analysed yet";
    el.innerHTML = `${thumb}<div style="min-width:0"><div style="display:flex"><div class="name">${esc(f.name)}</div>
      <button class="remove" title="Remove from the project">✕</button></div><div class="meta">${meta}</div>
      <div class="cuts">${uses.map(({ tr, it }) => `<span class="cut-id${selected === it.id ? " selected" : ""}" style="--c:${trackColor(tr)}" data-id="${it.id}" title="${tr.id} ${esc(tr.name)}, ${fmtSrc(it.in)}–${fmtSrc(it.out)}">${it.label}</span>`).join("")}</div></div>`;
    el.title = f.path;
    if (s?.proxy) el.addEventListener("click", () => playSource(s, 0));
    el.querySelector(".remove").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try { P.footage = await api("DELETE", `/api/projects/${P.id}/footage/${f.index}`); renderSources(); toast("Removed. Analyse again to update the edit."); }
      catch (e) { toast(e.message); }
    });
    for (const chip of el.querySelectorAll(".cut-id")) chip.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const it = itemById(chip.dataset.id);
      if (it) { selected = it.id; seek(it.at); renderTimeline(); renderSources(); }
    });
    box.appendChild(el);
  }
}

async function importPaths(paths) {
  if (!paths.length) return;
  try {
    P.footage = await api("POST", `/api/projects/${P.id}/footage`, { paths });
    renderSources();
    toast(`${paths.length} item(s) imported. Analyse to include them.`);
  } catch (e) { toast(e.message); }
}

$("importBtn").addEventListener("click", () => {
  if (host) host.postMessage({ type: "pickFiles" });
  else {
    const p = prompt("Paths of video/audio files or folders, one per line:");
    if (p) importPaths(p.split(/\r?\n/).map((x) => x.trim()).filter(Boolean));
  }
});
host?.addEventListener("message", (m) => {
  const msg = m.data;
  if (msg?.type === "files" && P.id) importPaths(msg.paths || []);
});
const footagePane = $("paneFootage");
footagePane.addEventListener("dragover", (e) => { e.preventDefault(); footagePane.classList.add("drop"); });
footagePane.addEventListener("dragleave", () => footagePane.classList.remove("drop"));
footagePane.addEventListener("drop", (e) => {
  e.preventDefault(); footagePane.classList.remove("drop");
  // A page can't see where dropped files are; the host window can.
  if (host?.postMessageWithAdditionalObjects) host.postMessageWithAdditionalObjects({ type: "dropped" }, e.dataTransfer.files);
});

// ---------- chat ---------------------------------------------------------------------------------------------------

let pending = null;

function renderChat() {
  const box = $("messages"); box.innerHTML = "";
  for (const m of P.chat) box.appendChild(chatBubble(m));
  if (pending) box.appendChild(pending.el);
  box.scrollTop = box.scrollHeight;
}

function chatBubble(m) {
  const el = document.createElement("div");
  el.className = "msg " + m.role;
  el.textContent = m.text;
  if (m.version) {
    const v = document.createElement("div"); v.className = "ver"; v.textContent = `→ version ${m.version}`;
    v.addEventListener("click", async () => { await api("POST", `/api/projects/${P.id}/versions/${m.version}/current`); loadEdit(); });
    el.appendChild(v);
  }
  return el;
}

async function send() {
  const text = $("input").value.trim();
  if (!text || pending) return;
  try {
    await api("POST", `/api/projects/${P.id}/chat`, { message: text });
  } catch (e) { toast(e.message); return; }
  P.chat.push({ role: "user", text });
  const el = document.createElement("div"); el.className = "msg assistant pending"; el.textContent = "…";
  const tools = document.createElement("div"); tools.className = "tools"; el.appendChild(tools);
  pending = { el, text: "", tools };
  $("input").value = "";
  renderChat();
}

function onChatEvent(e) {
  if (!pending) return;
  if (e.event === "assistant.delta") { pending.el.firstChild.textContent = e.text; }
  else if (e.event === "tool.end") { pending.tools.textContent += (e.error ? "✗ " : "✓ ") + e.result + "\n"; }
  else if (e.event === "chat.done" || e.event === "chat.error") {
    pending = null;
    if (e.event === "chat.error") toast(e.message);
    loadChat();
  }
  $("messages").scrollTop = $("messages").scrollHeight;
}

$("send").addEventListener("click", send);
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

let activePane = "paneFootage";
function showPane(id) {
  activePane = id;
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.pane === id);
  for (const p of document.querySelectorAll(".pane")) p.classList.toggle("active", p.id === id);
  if (id === "paneChat") { $("messages").scrollTop = $("messages").scrollHeight; $("input").focus(); }
}
for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => showPane(t.dataset.pane));
function addToMessage(label) {
  const box = $("input");
  box.value = (box.value && !box.value.endsWith(" ") ? box.value + " " : box.value) + label + " ";
  box.focus();
}

// ---------- timeline -----------------------------------------------------------------------------------------------

const COLORS = { video: "var(--v1)", text: "var(--text-track)", audio: "var(--audio)", music: "var(--music)", sfx: "var(--sfx)" };
const trackColor = (tr) => (tr.kind === "video" && tr.id !== "V1") ? "var(--v2)" : COLORS[tr.kind];
let pxPerSec = 12, view = "full", layout = null, selected = null, total = 0;
let t = 0, playing = false, startWall = 0, startT = 0, sourceMode = null;

const src = (id) => P.sources.find((s) => s.id === id);
const main = () => P.edit?.tracks.find((x) => x.id === "V1");
const itemsOf = (tr) => tr.linked ? (P.edit.tracks.find((x) => x.id === tr.linked)?.items || []) : tr.items;
const itemById = (id) => P.edit?.tracks.flatMap((tr) => tr.items).find((it) => it.id === id);

function computeEdit() { total = P.edit ? P.edit.duration : 0; t = Math.min(t, total); }

function buildLayout() {
  if (view !== "full" || !main()) return null;
  const v1 = [...main().items].sort((a, b) => a.at - b.at);
  const blocks = []; let x0 = 0;
  for (const it of v1) {
    const last = blocks.at(-1);
    if (last && last.source === it.source && it.in >= last.lastOut) { last.items.push(it); last.lastOut = it.out; continue; }
    const s = src(it.source);
    blocks.push({ source: it.source, x0, len: s.duration, items: [it], lastOut: it.out });
    x0 += s.duration;
  }
  return { blocks, width: x0 };
}

function posOf(tt) {
  if (!layout) return tt;
  for (const b of layout.blocks) for (const it of b.items) if (tt >= it.at && tt < it.at + it.duration) return b.x0 + it.in + (tt - it.at);
  const b = layout.blocks.at(-1); if (!b) return tt;
  return b.x0 + b.items.at(-1).out;
}

function hit(pos) {
  if (!layout) return { t: pos };
  for (const b of layout.blocks) if (pos >= b.x0 && pos < b.x0 + b.len) {
    const st = pos - b.x0, it = b.items.find((i) => st >= i.in && st < i.out);
    return it ? { t: it.at + (st - it.in) } : { source: src(b.source), st };
  }
  return { t: total };
}

function pieces(start, dur) {
  if (!layout) return [[start, start + dur]];
  const end = start + dur, out = [];
  for (const b of layout.blocks) for (const it of b.items) {
    const a = Math.max(start, it.at), z = Math.min(end, it.at + it.duration);
    if (z > a) out.push([b.x0 + it.in + (a - it.at), b.x0 + it.in + (z - it.at)]);
  }
  return out.length ? out : [[posOf(start), posOf(start) + dur]];
}

function goTo(pos) { const r = hit(pos); if (r.source) playSource(r.source, r.st); else seek(r.t); }

function filmstrip(el, s, from) {
  if (!s?.strip) return;
  el.style.backgroundImage = `url(${s.strip})`;
  el.style.backgroundSize = `${s.duration * pxPerSec}px 100%`;
  el.style.backgroundPosition = `${-from * pxPerSec}px 0`;
  el.style.backgroundRepeat = "no-repeat";
}

function clipEl(tr, it, extra, labelOnly = false, frames = true) {
  const c = document.createElement("div");
  c.className = extra + (tr.kind === "music" || tr.kind === "sfx" ? " placeholder" : "") + (selected === it.id ? " selected" : "") + (it.changed && !tr.linked ? " changed" : "");
  c.style.setProperty("--c", trackColor(tr));
  if (tr.kind === "video" && frames) filmstrip(c, src(it.source), it.in);
  if (tr.linked && !frames) c.innerHTML = `<span class="lbl">${it.label}</span>`;
  else if (tr.linked) c.innerHTML = `<div class="wave"></div><span class="lbl">${it.label}</span>`;
  else if (labelOnly) c.innerHTML = `<span class="lbl">${it.label}</span>`;
  else c.innerHTML = `<span class="lbl">${it.label}</span><span class="txt">${esc(it.source ? src(it.source)?.name : it.text)}</span>`;
  c.title = it.source ? `${it.label} ${src(it.source)?.name} ${fmtSrc(it.in)}–${fmtSrc(it.out)}${it.transcript ? `\n"${it.transcript}"` : ""}${it.reason ? `\n${it.reason}` : ""}` : `${it.label} ${it.text}`;
  c.addEventListener("click", (e) => {
    selected = it.id; renderTimeline(); renderSources();
    if (activePane === "paneChat") addToMessage(it.label);
    e.stopPropagation();
  });
  c.addEventListener("dblclick", (e) => { seek(it.at); e.stopPropagation(); });
  return c;
}

function renderTimeline() {
  const headers = $("headers"), lanes = $("lanes"), ruler = $("ruler");
  headers.innerHTML = ""; lanes.innerHTML = ""; ruler.innerHTML = "";
  if (!P.edit) return;
  layout = buildLayout();
  const span = layout ? layout.width : total;
  const width = Math.ceil((span + 5) * pxPerSec);
  lanes.style.width = ruler.style.width = width + "px";
  // The ruler counts edit time; in the full footage view its marks sit on the kept parts and skip the cuts.
  const step = pxPerSec >= 30 ? 1 : pxPerSec >= 12 ? 5 : 10;
  for (let s = 0; s <= total; s += step) {
    const tk = document.createElement("div"); tk.className = "tick"; tk.style.left = posOf(s) * pxPerSec + "px"; tk.textContent = fmtSrc(s); ruler.appendChild(tk);
  }
  for (const tr of P.edit.tracks) {
    const h = document.createElement("div");
    h.className = "header"; h.style.setProperty("--c", trackColor(tr));
    h.innerHTML = `<span class="tid">${tr.id}</span><span class="tname">${esc(tr.name)}${tr.linked ? ` (with ${tr.linked})` : ""}</span>`;
    headers.appendChild(h);
    const lane = document.createElement("div"); lane.className = "lane";
    if (layout && (tr.id === "V1" || tr.linked === "V1")) {
      for (const b of layout.blocks) {
        const s = src(b.source);
        const blk = document.createElement("div"); blk.className = "block";
        blk.style.left = b.x0 * pxPerSec + "px"; blk.style.width = Math.max(4, b.len * pxPerSec - 1) + "px";
        blk.title = `${s.name}: dimmed parts are cut. Click one to play the original there.`;
        if (tr.linked) blk.innerHTML = `<div class="wave"></div>`; else filmstrip(blk, s, 0);
        blk.addEventListener("click", (e) => { const r = blk.getBoundingClientRect(); playSource(s, (e.clientX - r.left) / pxPerSec); e.stopPropagation(); });
        let from = 0;
        for (const it of [...b.items, { in: s.duration }]) {
          if (it.in > from + 0.05) {
            const cut = document.createElement("div"); cut.className = "cutpart";
            cut.style.left = from * pxPerSec + "px"; cut.style.width = (it.in - from) * pxPerSec + "px";
            cut.title = `Cut: ${s.name} ${fmtSrc(from)}–${fmtSrc(it.in)}. Click to play it.`;
            blk.appendChild(cut);
          }
          if (!it.label) break;
          const seg = clipEl(tr, it, "seg", true, false);
          seg.style.left = it.in * pxPerSec + "px"; seg.style.width = Math.max(3, (it.out - it.in) * pxPerSec - 1) + "px";
          blk.appendChild(seg);
          from = it.out;
        }
        lane.appendChild(blk);
      }
    } else {
      for (const it of itemsOf(tr)) {
        pieces(it.at, it.duration).forEach(([x1, x2], i) => {
          const c = clipEl(tr, it, "clip", i > 0);
          c.style.left = x1 * pxPerSec + "px"; c.style.width = Math.max(4, (x2 - x1) * pxPerSec - 1) + "px";
          lane.appendChild(c);
        });
      }
    }
    lanes.appendChild(lane);
  }
  for (const hostEl of [ruler, lanes]) { const p = document.createElement("div"); p.className = "playhead"; hostEl.appendChild(p); }
  movePlayhead();
}

function movePlayhead() {
  const x = posOf(t) * pxPerSec;
  for (const p of document.querySelectorAll(".playhead")) p.style.left = x + "px";
  $("time").textContent = `${fmt(t)} / ${fmt(total)}`;
  const m = main()?.items.find((it) => t >= it.at && t < it.at + it.duration);
  $("nowClip").textContent = sourceMode || !m ? "" : `Now: ${m.label} ${src(m.source)?.name} @ ${fmtSrc(m.in + t - m.at)}`;
  if (playing && !sourceMode) {
    const w = $("lanesWrap");
    if (x > w.scrollLeft + w.clientWidth - 40 || x < w.scrollLeft) w.scrollLeft = Math.max(0, x - 80);
  }
}

// ---------- player -------------------------------------------------------------------------------------------------

function active(trackId) {
  const tr = P.edit?.tracks.find((x) => x.id === trackId);
  return tr?.items.find((it) => t >= it.at && t < it.at + it.duration);
}

function drive(el, it, force) {
  const s = it && src(it.source);
  if (!it || !s?.proxy) { el.classList.add("hidden"); if (!el.paused) el.pause(); el.dataset.item = ""; return; }
  const want = it.in + (t - it.at);
  if (!el.src.endsWith(s.proxy)) el.src = s.proxy;
  el.classList.remove("hidden");
  if (force || el.dataset.item !== it.id || Math.abs(el.currentTime - want) > 0.3) el.currentTime = want;
  el.dataset.item = it.id;
  if (playing && el.paused) el.play().catch(() => {});
  if (!playing && !el.paused) el.pause();
}

function syncPlayer(force) {
  if (sourceMode) return;
  if (!P.edit) { for (const id of ["v1", "v2"]) $(id).classList.add("hidden"); $("caption").style.display = "none"; $("badges").innerHTML = ""; return; }
  drive($("v1"), active("V1"), force);
  const overlay = P.edit.tracks.filter((tr) => tr.kind === "video" && tr.id !== "V1").map((tr) => active(tr.id)).find(Boolean);
  drive($("v2"), overlay, force);
  const textTrack = P.edit.tracks.find((tr) => tr.kind === "text");
  const cap = textTrack && active(textTrack.id);
  $("caption").style.display = cap ? "block" : "none";
  if (cap) $("caption").textContent = cap.text;
  const marks = P.edit.tracks.filter((tr) => tr.kind === "music" || tr.kind === "sfx").map((tr) => active(tr.id)).filter(Boolean);
  $("badges").innerHTML = marks.map((b) => `<span class="badge">${b.label} ${esc(b.text)}</span>`).join("");
}

function tick() {
  if (playing && !sourceMode) {
    t = startT + (performance.now() - startWall) / 1000;
    if (t >= total) { t = total; setPlaying(false); }
    syncPlayer(false);
    movePlayhead();
  }
  requestAnimationFrame(tick);
}

function setPlaying(on) {
  if (sourceMode) { const v = $("src"); on ? v.play() : v.pause(); $("play").textContent = on ? "❚❚ Pause" : "▶ Play"; return; }
  if (!P.edit) return;
  playing = on;
  if (on) { if (t >= total) t = 0; startT = t; startWall = performance.now(); }
  $("play").textContent = on ? "❚❚ Pause" : "▶ Play";
  syncPlayer(true);
}
function pause() { if (playing) setPlaying(false); if (sourceMode) exitSource(); }

function seek(to) {
  if (sourceMode) exitSource();
  t = Math.max(0, Math.min(total, to)); startT = t; startWall = performance.now();
  syncPlayer(true); movePlayhead();
}

function playSource(s, at) {
  if (!s?.proxy) return;
  if (playing) setPlaying(false);
  sourceMode = s;
  for (const id of ["v1", "v2"]) { $(id).pause(); $(id).classList.add("hidden"); }
  $("caption").style.display = "none"; $("badges").innerHTML = "";
  const v = $("src"); v.classList.remove("hidden");
  if (!v.src.endsWith(s.proxy)) v.src = s.proxy;
  v.currentTime = at; v.play().catch(() => {});
  $("srcName").textContent = `Original: ${s.name}`;
  $("srcBanner").style.display = "flex";
  $("play").textContent = "❚❚ Pause";
  renderSources(); movePlayhead();
}

function exitSource() {
  sourceMode = null; const v = $("src"); v.pause(); v.classList.add("hidden");
  $("srcBanner").style.display = "none"; $("play").textContent = "▶ Play";
  renderSources(); syncPlayer(true); movePlayhead();
}

$("play").addEventListener("click", () => setPlaying(sourceMode ? $("src").paused : !playing));
$("backToEdit").addEventListener("click", exitSource);
$("src").addEventListener("ended", () => { $("play").textContent = "▶ Play"; });
$("zoom").addEventListener("input", (e) => { pxPerSec = Number(e.target.value); renderTimeline(); });
$("view").addEventListener("change", (e) => { view = e.target.value; renderTimeline(); });
$("lanesWrap").addEventListener("scroll", (e) => { $("rulerWrap").scrollLeft = e.target.scrollLeft; $("headers").scrollTop = e.target.scrollTop; });
$("rulerWrap").addEventListener("click", (e) => { const r = $("ruler").getBoundingClientRect(); goTo((e.clientX - r.left) / pxPerSec); });
$("lanes").addEventListener("click", (e) => { const r = $("lanes").getBoundingClientRect(); goTo((e.clientX - r.left) / pxPerSec); });
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) && !$("project").hidden) { e.preventDefault(); $("play").click(); }
});

// Timeline height: drag the bar above it; track rows share the height (28-96 px each). Remembered between sessions.
function setTimelineHeight(h) {
  h = Math.max(140, Math.min(window.innerHeight - 220, h));
  $("project").style.setProperty("--tl-h", h + "px");
  const rows = P.edit ? P.edit.tracks.length : 6;
  $("timeline").style.setProperty("--row", Math.max(28, Math.min(96, Math.floor((h - 30 - 14) / rows))) + "px");
  localStorage.setItem("timelineHeight", h);
}
$("split").addEventListener("pointerdown", (e) => {
  const startY = e.clientY, startH = $("timeline").getBoundingClientRect().height;
  $("split").classList.add("dragging"); $("split").setPointerCapture(e.pointerId);
  const move = (ev) => setTimelineHeight(startH + (startY - ev.clientY));
  const up = () => { $("split").classList.remove("dragging"); $("split").removeEventListener("pointermove", move); $("split").removeEventListener("pointerup", up); };
  $("split").addEventListener("pointermove", move); $("split").addEventListener("pointerup", up);
});

// ---------- start --------------------------------------------------------------------------------------------------

(async () => {
  try {
    profiles = await api("GET", "/api/profiles");
    setTimelineHeight(Number(localStorage.getItem("timelineHeight")) || 280);
    const opened = location.hash.match(/^#p=([a-z0-9-]+)$/);
    if (opened) await openProject(opened[1]); else await showHome();
  } catch (e) {
    document.body.textContent = `Can't reach the Footage Editor service: ${e.message}`;
  }
  requestAnimationFrame(tick);
})();
