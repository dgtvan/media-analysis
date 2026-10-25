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
// Durations are shown in minutes (decimals allowed) and kept in whole seconds by the API.
const minutes = (seconds) => seconds == null ? "" : String(+(seconds / 60).toFixed(2));
const toSeconds = (text) => { const m = Number(String(text).trim().replace(",", ".")); return text && m > 0 ? Math.max(1, Math.round(m * 60)) : null; };
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
      <div class="meta">${esc(profile)}${p.max_seconds ? ` · max ${minutes(p.max_seconds)} min` : ""}</div>
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
  $("newProfileHint").textContent = p ? `${p.description} Target ${minutes(p.target_seconds)} min${p.max_seconds ? `, at most ${minutes(p.max_seconds)} min` : ""}.` : "";
}
$("newProfile").addEventListener("change", updateProfileHint);
$("newProject").addEventListener("close", async () => {
  if ($("newProject").returnValue !== "ok") return;
  const f = new FormData($("newProjectForm"));
  try {
    const p = await api("POST", "/api/projects", {
      name: f.get("name"), profile_id: f.get("profile"), max_seconds: toSeconds(f.get("max")),
    });
    $("newProjectForm").reset();
    openProject(p.id);
  } catch (e) { toast(e.message); }
});

// ---------- project ------------------------------------------------------------------------------------------------

const P = {
  id: null, info: null, footage: [], sources: [], edit: null, version: null, versions: [], feed: [],
};

async function openProject(id) {
  P.id = id;
  history.replaceState(null, "", `${location.pathname}${location.search}#p=${id}`);
  $("home").hidden = true;
  $("project").hidden = false;
  openEvents(id);
  await Promise.all([loadProject(), loadEdit(), loadActivity()]);
}

async function loadProject() {
  P.info = await api("GET", `/api/projects/${P.id}`);
  P.footage = await api("GET", `/api/projects/${P.id}/footage`);
  $("projectName").textContent = P.info.name;
  fillProfiles($("profile"), P.info.profile_id);
  $("maxMinutes").value = minutes(P.info.max_seconds);
  $("maxMinutes").placeholder = minutes(profiles.find((x) => x.id === P.info.profile_id)?.max_seconds) || "none";
  $("analyseBtn").textContent = P.info.analysing ? "Stop" : P.info.current_version ? "Analyse again" : "Analyse";
  analysing = P.info.analysing;
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

async function loadActivity() {
  try { P.feed = await api("GET", `/api/projects/${P.id}/activity`); } catch { P.feed = []; }
  renderFeed();
}

$("backBtn").addEventListener("click", showHome);

$("profile").addEventListener("change", async (e) => {
  try { await api("PATCH", `/api/projects/${P.id}`, { profile_id: e.target.value }); await loadProject(); toast("Profile changed. Analyse again to get a new suggestion."); }
  catch (err) { toast(err.message); }
});
$("maxMinutes").addEventListener("change", async (e) => {
  const v = e.target.value.trim();
  try {
    const seconds = toSeconds(v);
    if (v && !seconds) { toast("Enter the limit in minutes, e.g. 1.5"); return; }
    await api("PATCH", `/api/projects/${P.id}`, seconds ? { max_seconds: seconds } : { clear_max_seconds: true });
    await loadProject();
    toast("Duration limit changed. Analyse again to get a suggestion within it, or ask in the chat.");
  } catch (err) { toast(err.message); }
});
$("analyseBtn").addEventListener("click", async () => {
  try {
    if (P.info.analysing) { await api("POST", `/api/projects/${P.id}/stop`); }
    else { await api("POST", `/api/projects/${P.id}/analyse`); setStatus("Starting…", "busy"); showPane("paneChat"); await loadActivity(); }
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
    else if (e.event === "finished") setStatus(`Done: version ${e.version}, ${fmtLength(e.seconds)}`);
    else if (e.event === "job_exit" && e.stopped) setStatus("Stopped");
    onJobEvent(e);
    if (e.event === "job_exit") { loadProject(); loadEdit(); }
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
    const thumb = f.thumb ? `<img src="${f.thumb}" alt="" loading="lazy">`
      : `<div class="ph">${f.has_video === false ? "audio" : ""}</div>`;
    const length = f.duration != null ? fmtSrc(f.duration) : "";
    const meta = !f.exists ? `<span class="missing">file not found</span>`
      : s ? length : [length, "not analysed yet"].filter(Boolean).join(" · ");
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

// ---------- activity: every analysis step and every message, one record each, oldest first -------------------------

// What each step does while it runs, and what it says when it's finished.
const STEP_TEXT = {
  ingest: ["Reading the footage", "Read the footage"],
  transcribe: ["Transcribing speech word by word", "Transcribed the speech"],
  shots: ["Finding shots and checking sharpness, shake and light", "Found the shots"],
  sound: ["Measuring pauses and loudness", "Measured pauses and loudness"],
  describe: ["Claude is looking at every shot", "Claude described every shot"],
  prepare: ["Making preview copies and filmstrips", "Made preview copies and filmstrips"],
  sequence: ["Claude is planning the edit", "Claude planned the edit"],
};
const KEPT = ["analysis_started", "stage_started", "stage_done", "stage_skipped", "warning", "info", "error", "finished",
  "summary", "job_exit"];
const fmtLength = (seconds) => seconds >= 60 ? `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s` : `${Math.round(seconds)} s`;
const clock = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

let pending = null;            // the Claude answer being written
let running = null;            // the step running now: { stage, detail, pct }
let analysing = false;

function record(icon, cls, title, detail, time) {
  const el = document.createElement("div");
  el.className = `rec ${cls}`;
  el.innerHTML = `<span class="rec-icon">${icon}</span><div class="rec-body"><div class="rec-title">${title}</div>
    ${detail ? `<div class="rec-detail">${detail}</div>` : ""}</div><span class="rec-time">${clock(time)}</span>`;
  return el;
}

// One analysis event as a record, or null for events that only feed other records (the summary, Claude's calls).
function analysisRecord(e, startedAt, isRunningStep) {
  const step = STEP_TEXT[e.stage];
  switch (e.event) {
    case "analysis_started": {
      const profile = profiles.find((x) => x.id === e.profile)?.name || e.profile || "";
      return record("▶", "analysis head", "Analysis started",
        esc(`${profile}${e.max_seconds ? `, max ${minutes(e.max_seconds)} min` : ""} · ${e.files ?? "?"} file(s)`), e.time);
    }
    case "stage_started": {
      if (!step) return null;
      const live = isRunningStep ? running : null;
      const detail = live?.detail || (e.message && !/^[A-Za-z]:\\/.test(e.message) ? e.message : "");
      const el = record(isRunningStep ? "" : "•", `analysis step${isRunningStep ? " live" : ""}`, esc(step[0]) + (isRunningStep ? "…" : ""),
        esc(detail) + (live?.pct != null ? ` · ${live.pct}%` : ""), e.time);
      if (live?.pct != null) el.querySelector(".rec-body").insertAdjacentHTML("beforeend", `<div class="bar"><i style="width:${live.pct}%"></i></div>`);
      return el;
    }
    case "stage_done":
      if (!step) return null;
      return record("✓", "analysis done", esc(step[1]),
        esc(e.message || "") + (startedAt[e.stage] != null && e.elapsed != null ? ` · took ${fmtLength(e.elapsed - startedAt[e.stage])}` : ""), e.time);
    case "stage_skipped":
      if (!step) return null;
      return record("–", "analysis skipped", esc(step[0]),
        ["cached", "ready"].includes(e.message) ? "already done earlier, reused" : esc(e.message || ""), e.time);
    case "info":
      return step ? record("·", "analysis info", esc(e.message), "", e.time) : null;
    case "warning":
      return record("⚠", "analysis warning", esc(e.message), "", e.time);
    case "error":
      return record("✗", "analysis failed", "Analysis failed", esc(e.message), e.time);
    case "finished": {
      const el = record("★", "analysis result", `Suggested edit ready: version ${e.version}`, `${fmtLength(e.seconds)} long`, e.time);
      el.dataset.version = e.version;
      return el;
    }
    case "job_exit":
      if (e.stopped) return record("■", "analysis stopped", "Analysis stopped", "Analysing again continues where it stopped.", e.time);
      return e.exit_code ? record("✗", "analysis failed", "Analysis ended with an error", `exit code ${e.exit_code}`, e.time) : null;
    default:
      return null;
  }
}

function chatBubble(m) {
  const el = document.createElement("div");
  el.className = "msg " + m.role;
  el.textContent = m.text;
  if (m.changes?.length) {
    const c = document.createElement("div"); c.className = "tools";
    c.textContent = m.changes.map((x) => "✓ " + x).join("\n");
    el.appendChild(c);
  }
  if (m.version) {
    const v = document.createElement("div"); v.className = "ver"; v.textContent = `→ version ${m.version}`;
    v.addEventListener("click", async () => { await api("POST", `/api/projects/${P.id}/versions/${m.version}/current`); loadEdit(); });
    el.appendChild(v);
  }
  return el;
}

function renderFeed() {
  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = "";
  const startedAt = {};
  // The last "started" of the running step shows live progress; earlier ones are history.
  const lastStart = analysing && running ? P.feed.findLastIndex((e) => e.kind === "analysis" && e.event === "stage_started" && e.stage === running.stage) : -1;
  P.feed.forEach((e, i) => {
    if (e.kind !== "analysis") { box.appendChild(chatBubble(e)); return; }
    if (e.event === "analysis_started") for (const k of Object.keys(startedAt)) delete startedAt[k];
    if (e.event === "stage_started") startedAt[e.stage] = e.elapsed;
    const el = analysisRecord(e, startedAt, i === lastStart);
    if (!el) return;
    if (e.event === "finished") {
      const summary = P.feed.slice(0, i).reverse().find((x) => x.event === "summary");
      if (summary) el.querySelector(".rec-body").insertAdjacentHTML("beforeend", `<details><summary>Details</summary><pre>${esc(summary.message)}</pre></details>`);
      el.querySelector(".rec-title").addEventListener("click", async () => {
        await api("POST", `/api/projects/${P.id}/versions/${e.version}/current`); loadEdit();
      });
    }
    box.appendChild(el);
  });
  if (pending) box.appendChild(pending.el);
  if (!P.feed.length && !pending) box.innerHTML = `<div class="dim feed-empty">Analysis steps and your conversation with Claude appear here.</div>`;
  if (atBottom || analysing || pending) box.scrollTop = box.scrollHeight;
}

// Live analysis: kept events become records right away (the server stores the same ones); progress and Claude's
// status only update the record of the running step.
function onJobEvent(e) {
  analysing = e.event !== "job_exit";
  if (KEPT.includes(e.event) && !String(e.message || "").startsWith("Claude call")) {
    P.feed.push({ kind: "analysis", ...e, time: e.time || new Date().toISOString() });
  }
  if (e.event === "stage_started") running = { stage: e.stage, detail: "", pct: null };
  else if (["progress", "activity"].includes(e.event) && STEP_TEXT[e.stage] && running?.stage !== e.stage) {
    running = { stage: e.stage, detail: "", pct: null };  // opened while this step was already running
  }
  if (running && e.stage === running.stage && e.event !== "stage_started") {
    if (e.event === "progress" && e.total) { running.pct = Math.round(100 * e.completed / e.total); if (e.message) running.detail = e.message; }
    else if (e.event === "activity") running.detail = e.message.startsWith("model ") ? `Claude (${e.message.slice(6)}) is starting` : `Claude is ${e.message}`;
    else if (e.event === "info") running.detail = e.message;
    else if (["stage_done", "stage_skipped"].includes(e.event)) running = null;
  }
  if (e.event === "job_exit") running = null;
  if ($("paneChat").classList.contains("active")) renderFeed();
}

async function send() {
  const text = $("input").value.trim();
  if (!text || pending) return;
  try {
    await api("POST", `/api/projects/${P.id}/chat`, { message: text });
  } catch (e) { toast(e.message); return; }
  P.feed.push({ kind: "chat", role: "user", text, time: new Date().toISOString() });
  const el = document.createElement("div"); el.className = "msg assistant pending";
  const body = document.createElement("div"); body.textContent = "Claude is reading the edit…";
  const tools = document.createElement("div"); tools.className = "tools";
  el.append(body, tools);
  pending = { el, body, tools };
  $("input").value = "";
  renderFeed();
}

function onChatEvent(e) {
  if (!pending) return;
  if (e.event === "assistant.delta") pending.body.textContent = e.text;
  else if (e.event === "tool.start") pending.tools.textContent += `… ${e.tool}\n`;
  else if (e.event === "tool.end") {
    const lines = pending.tools.textContent.split("\n");
    lines[lines.length - 2] = (e.error ? "✗ " : "✓ ") + e.result;
    pending.tools.textContent = lines.join("\n");
  } else if (e.event === "chat.done" || e.event === "chat.error") {
    pending = null;
    if (e.event === "chat.error") toast(e.message);
    loadActivity();
    return;
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
  if (id === "paneChat") { renderFeed(); $("messages").scrollTop = $("messages").scrollHeight; $("input").focus(); }
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
// "Play cut parts": playback walks the full footage view; fpos is the position there (seconds from its start).
let playCuts = false, fpos = 0, startPos = 0;
const fullPlay = () => playCuts && layout;

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

function goTo(pos) {
  if (fullPlay()) { seekPos(pos); return; }
  const r = hit(pos); if (r.source) playSource(r.source, r.st); else seek(r.t);
}

function filmstrip(el, s, from) {
  if (!s?.strip) return;
  el.style.backgroundImage = `url(${s.strip})`;
  el.style.backgroundSize = `${s.duration * pxPerSec}px 100%`;
  el.style.backgroundPosition = `${-from * pxPerSec}px 0`;
  el.style.backgroundRepeat = "no-repeat";
}

// Items on the video tracks above V1: their picture covers V1 while V1's sound keeps playing.
const overlays = () => (P.edit?.tracks || []).filter((tr) => tr.kind === "video" && tr.id !== "V1").flatMap((tr) => tr.items.map((it) => ({ tr, it })));

function coverNote(it) {
  const over = overlays().filter(({ it: o }) => o.at < it.at + it.duration && o.at + o.duration > it.at);
  return over.map(({ tr, it: o }) => `\nPicture covered by ${o.label} (${tr.name}) at ${fmtSrc(o.at)}–${fmtSrc(o.at + o.duration)}; this clip's sound keeps playing.`).join("");
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
  c.title = it.source ? `${it.label} ${src(it.source)?.name} ${fmtSrc(it.in)}–${fmtSrc(it.out)}${it.transcript ? `\n"${it.transcript}"` : ""}${it.reason ? `\n${it.reason}` : ""}${tr.id === "V1" ? coverNote(it) : ""}` : `${it.label} ${it.text}`;
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
    if (tr.id === "V1") for (const ov of overlays()) {
      for (const [x1, x2] of pieces(ov.it.at, ov.it.duration)) {
        const c = document.createElement("div"); c.className = "covered";
        c.style.left = x1 * pxPerSec + "px"; c.style.width = (x2 - x1) * pxPerSec + "px";
        lane.appendChild(c);
      }
    }
    lanes.appendChild(lane);
  }
  for (const hostEl of [ruler, lanes]) { const p = document.createElement("div"); p.className = "playhead"; hostEl.appendChild(p); }
  movePlayhead();
}

function movePlayhead() {
  const x = (fullPlay() ? fpos : posOf(t)) * pxPerSec;
  for (const p of document.querySelectorAll(".playhead")) p.style.left = x + "px";
  $("time").textContent = `${fmt(t)} / ${fmt(total)}`;
  const cut = fullPlay() && hit(fpos);
  const m = main()?.items.find((it) => t >= it.at && t < it.at + it.duration);
  $("nowClip").textContent = sourceMode ? "" : cut?.source ? `Cut part: ${cut.source.name} @ ${fmtSrc(cut.st)}`
    : m ? `Now: ${m.label} ${src(m.source)?.name} @ ${fmtSrc(m.in + t - m.at)}` : "";
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

function drive(el, it, force, want = it && it.in + (t - it.at)) {
  const s = it && src(it.source);
  if (!it || !s?.proxy) { el.classList.add("hidden"); if (!el.paused) el.pause(); el.dataset.item = ""; return; }
  const newSource = !el.src.endsWith(s.proxy);
  if (newSource) el.src = s.proxy;
  el.classList.remove("hidden");
  // Between edit clips the picture jumps; walking the full footage it runs on, so only a real drift moves it.
  const jumped = !fullPlay() && el.dataset.item !== it.id;
  if (force || newSource || jumped || Math.abs(el.currentTime - want) > 0.3) el.currentTime = want;
  el.dataset.item = it.id;
  if (playing && el.paused) el.play().catch(() => {});
  if (!playing && !el.paused) el.pause();
}

function syncPlayer(force) {
  if (sourceMode) return;
  if (!P.edit) { for (const id of ["v1", "v2"]) $(id).classList.add("hidden"); $("caption").style.display = "none"; $("badges").innerHTML = ""; return; }
  if (fullPlay()) {
    const r = hit(fpos);
    if (r.source) {
      drive($("v1"), { id: "cut", source: r.source.id }, force, r.st);
      drive($("v2"), null, force);
      $("caption").style.display = "none";
      $("badges").innerHTML = `<span class="badge cut">Cut part, not in the edit</span>`;
      return;
    }
    t = r.t;
  }
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
  if (playing && !sourceMode && fullPlay()) {
    fpos = startPos + (performance.now() - startWall) / 1000;
    if (fpos >= layout.width) { fpos = layout.width; setPlaying(false); }
    syncPlayer(false);
    movePlayhead();
  } else if (playing && !sourceMode) {
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
  if (on && fullPlay()) { if (fpos >= layout.width) fpos = 0; startPos = fpos; startWall = performance.now(); }
  else if (on) { if (t >= total) t = 0; startT = t; startWall = performance.now(); }
  $("play").textContent = on ? "❚❚ Pause" : "▶ Play";
  syncPlayer(true);
}
function pause() { if (playing) setPlaying(false); if (sourceMode) exitSource(); }

function seek(to) {
  if (sourceMode) exitSource();
  t = Math.max(0, Math.min(total, to)); startT = t; startWall = performance.now();
  if (fullPlay()) fpos = startPos = posOf(t);
  syncPlayer(true); movePlayhead();
}

function seekPos(pos) {
  if (sourceMode) exitSource();
  fpos = startPos = Math.max(0, Math.min(layout.width, pos)); startWall = performance.now();
  syncPlayer(true); movePlayhead();
}

function setPlayCuts(on) {
  playCuts = on;
  $("playCuts").setAttribute("aria-pressed", String(on));
  if (on && view !== "full") { view = $("view").value = "full"; renderTimeline(); }
  if (on && layout) fpos = startPos = posOf(t);
  startT = t; startWall = performance.now();
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
$("view").addEventListener("change", (e) => { view = e.target.value; renderTimeline(); if (view !== "full" && playCuts) setPlayCuts(false); });
$("playCuts").addEventListener("click", () => setPlayCuts(!playCuts));
$("lanesWrap").addEventListener("scroll", (e) => { $("rulerWrap").scrollLeft = e.target.scrollLeft; $("headers").scrollTop = e.target.scrollTop; });
$("rulerWrap").addEventListener("click", (e) => { if (scrubbed) return; const r = $("ruler").getBoundingClientRect(); goTo((e.clientX - r.left) / pxPerSec); });
$("lanes").addEventListener("click", (e) => { if (scrubbed) return; const r = $("lanes").getBoundingClientRect(); goTo((e.clientX - r.left) / pxPerSec); });

// Edit time at a timeline position; over a cut part (full footage view) the nearest kept moment.
function editTimeAt(pos) {
  if (!layout) return Math.max(0, Math.min(total, pos));
  const r = hit(pos);
  if (r.t != null) return r.t;
  let best = total, gap = Infinity;
  for (const b of layout.blocks) for (const it of b.items) {
    const x1 = b.x0 + it.in, x2 = b.x0 + it.out;
    if (Math.abs(pos - x1) < gap) { gap = Math.abs(pos - x1); best = it.at; }
    if (Math.abs(pos - x2) < gap) { gap = Math.abs(pos - x2); best = it.at + it.duration; }
  }
  return best;
}

// Drag the playhead to scrub; playback resumes on release if it was playing. The timeline scrolls near its edges.
let scrubbed = false;
for (const host of ["ruler", "lanes"]) $(host).addEventListener("pointerdown", (e) => {
  if (!e.target.classList.contains("playhead") || e.button !== 0 || !P.edit) return;
  e.preventDefault(); e.stopPropagation();
  const resume = playing && !sourceMode;
  if (playing) setPlaying(false);
  document.body.classList.add("scrubbing");
  const wrap = $("lanesWrap");
  const move = (ev) => {
    const w = wrap.getBoundingClientRect();
    if (ev.clientX > w.right - 30) wrap.scrollLeft += 20; else if (ev.clientX < w.left + 30) wrap.scrollLeft -= 20;
    const pos = (ev.clientX - $("lanes").getBoundingClientRect().left) / pxPerSec;
    if (fullPlay()) seekPos(pos); else seek(editTimeAt(pos));
  };
  const up = () => {
    window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
    document.body.classList.remove("scrubbing");
    scrubbed = true; setTimeout(() => { scrubbed = false; });
    if (resume) setPlaying(true);
  };
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
});
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) && !$("project").hidden) { e.preventDefault(); $("play").click(); }
});

// Timeline height: drag the bar above it; track rows share the height (28-96 px each). Remembered between sessions.
function setTimelineHeight(h) {
  h = Math.max(140, Math.min(window.innerHeight - 220, h));
  $("project").style.setProperty("--tl-h", h + "px");
  const rows = P.edit ? P.edit.tracks.length : 6;
  $("timeline").style.setProperty("--row", Math.max(28, Math.min(96, Math.floor((h - 30 - 32 - 14) / rows))) + "px");
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
    const opened = location.hash.match(/^#p=([a-z0-9-]+)(&tab=activity)?$/);
    if (opened) { await openProject(opened[1]); if (opened[2]) showPane("paneChat"); } else await showHome();
  } catch (e) {
    document.body.textContent = `Can't reach the Footage Editor service: ${e.message}`;
  }
  requestAnimationFrame(tick);
})();
