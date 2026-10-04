// upload.js — powers the Upload Video page.
//
// Flow: drop or browse -> the clip appears in the video stage immediately
// (a local blob: URL, no waiting on the upload) -> the same file is POSTed to
// /api/upload and analysed on the server -> each job is polled until done.
// Detections come back with a video timestamp, so they are drawn as markers on
// the seek bar and each one jumps the player to that moment.
//
// Unchanged from before: POST /api/upload, GET /api/uploads, GET
// /api/upload/<job_id>, 1.5 s polling, and the rule that alert tones/toasts
// fire only for entries first seen *after* this page started watching a job
// (jobs reloaded from disk are painted silently, so a refresh never replays a
// wall of sounds).

const ALLOWED_EXT = [".mp4", ".avi", ".mov", ".mkv", ".webm"];
const POLL_MS = 1500;
// Alerts are stamped when the pipeline raised them, which is a moment after the
// event itself. Jumping a little early shows what led up to it.
const SEEK_LEAD_SECONDS = 2;

const $ = (id) => document.getElementById(id);
const dropzone = $("dropzone");
const fileInput = $("fileInput");
const stageRoot = $("uploadStage");
const jobList = $("jobList");

const stage = VideoStage.mount(stageRoot, { kind: "video" });

const jobs = new Map();      // id -> job
const order = [];            // job ids, newest first
const items = new Map();     // id -> <li>
const pollers = {};          // id -> interval handle
const timelineSeen = {};     // id -> timeline entries already announced
let activeId = null;
let previewOk = false;       // can the current clip be seeked in this browser?
let pendingSeq = 0;

/* -------------------------------------------------------------- job model */
function normStatus(s) {
  s = String(s || "processing").toLowerCase();
  return ["queued", "processing", "done", "error"].includes(s) ? s : "processing";
}

function jobFromServer(raw) {
  const filename = raw.filename || (raw.video_path || "").split("/").pop() || "video";
  return {
    id: raw.job_id, filename, camera_id: raw.camera_id || "",
    status: normStatus(raw.status), phase: "server", uploadPct: 100,
    progress: raw.progress ?? null, eta_seconds: raw.eta_seconds ?? null,
    timeline: raw.timeline || [], total_alerts: raw.total_alerts, ai_verified: raw.ai_verified,
    duration: raw.duration, error: raw.error || null,
    file: null, objectUrl: null, size: null,
    videoUrl: `/api/upload/${encodeURIComponent(raw.job_id)}/video`,
  };
}

// Merges a server snapshot into a job and announces genuinely new alerts.
function applyServerState(job, raw, opts = {}) {
  job.status = normStatus(raw.status);
  job.progress = raw.progress ?? null;
  job.eta_seconds = raw.eta_seconds ?? null;
  job.total_alerts = raw.total_alerts;
  job.ai_verified = raw.ai_verified;
  job.duration = raw.duration;
  job.error = raw.error || null;
  if (raw.camera_id) job.camera_id = raw.camera_id;

  const timeline = raw.timeline || [];
  const seen = timelineSeen[job.id] ?? timeline.length;
  const fresh = opts.silent ? [] : timeline.slice(seen);
  timelineSeen[job.id] = timeline.length;
  job.timeline = timeline;

  fresh.forEach((entry) => {
    const a = normalizeTimelineEntry(entry);
    showToast(
      `${a.level === 2 ? "Level-2 critical" : "Level-1 warning"} alert — ${a.text}`,
      a.level === 2 ? "critical" : "warning"
    );
    if (a.level === 2 && typeof flashCritical === "function") flashCritical();
    if (typeof playAlertTone === "function") playAlertTone(a.level);
  });

  refresh(job);
}

function normalizeTimelineEntry(entry) {
  const level = entry.type === "critical" ? 2 : 1;
  const rules = (entry.triggered_rules || []).join(", ") || "rule violation";
  const parts = [`Track #${entry.track_id}`];
  if (entry.behaviour_class && entry.behaviour_class !== "Normal") parts.push(entry.behaviour_class);
  parts.push(rules);
  if (typeof entry.risk_score === "number") parts.push(`risk ${entry.risk_score.toFixed(2)}`);
  const at = typeof entry.video_time_s === "number" ? entry.video_time_s : null;
  return { level, text: parts.join(" · "), at, time: at !== null ? fmtTime(at) : (entry.time || "") };
}

/* ------------------------------------------------------------ queue items */
function statusView(job) {
  switch (job.status) {
    case "uploading": return { label: "Uploading", cls: "pill-accent" };
    case "queued": return { label: "Queued", cls: "" };
    case "processing": return { label: "Analyzing", cls: "pill-accent" };
    case "done": return { label: "Done", cls: "pill-ok" };
    default: return { label: "Failed", cls: "pill-danger" };
  }
}

function etaText(seconds) {
  const mins = Math.floor(seconds / 60), secs = Math.round(seconds % 60);
  return `${mins > 0 ? mins + "m " : ""}${secs}s`;
}

function subText(job) {
  if (job.status === "uploading") return `Uploading ${Math.round(job.uploadPct)}%`;
  if (job.status === "queued") return "Waiting for the analyzer";
  if (job.status === "processing") {
    const pct = typeof job.progress === "number" ? `${Math.round(job.progress * 100)}%` : "Analyzing";
    return typeof job.eta_seconds === "number" ? `${pct}, about ${etaText(job.eta_seconds)} left` : pct;
  }
  if (job.status === "done") {
    const bits = [];
    if (job.total_alerts != null) bits.push(`${job.total_alerts} alert${job.total_alerts === 1 ? "" : "s"}`);
    if (job.ai_verified != null) bits.push(`${job.ai_verified} AI-verified`);
    if (job.duration != null) bits.push(`${job.duration}s analyzed`);
    return bits.length ? bits.join(", ") : "Analysis complete";
  }
  return job.error || "Analysis failed";
}

function createItem(job, atTop) {
  const li = document.createElement("li");
  li.className = "job";
  li.id = `job-${job.id}`;
  li.innerHTML = `
    <button type="button" class="job-select" aria-pressed="false">
      <span class="job-name"></span>
      <span class="pill"></span>
      <span class="job-bar"><span class="job-bar-fill"></span></span>
      <span class="job-sub"></span>
    </button>
    <div class="job-actions" hidden></div>`;
  li.querySelector(".job-select").addEventListener("click", () => selectJob(li.dataset.id));
  li.dataset.id = job.id;
  items.set(job.id, li);
  if (atTop) jobList.prepend(li); else jobList.appendChild(li);
  return li;
}

function updateItem(job) {
  const li = items.get(job.id);
  if (!li) return;
  const sv = statusView(job);
  li.className = `job is-${job.status}${job.status === "processing" && job.progress == null ? " is-indeterminate" : ""}`;

  const name = li.querySelector(".job-name");
  name.textContent = job.filename;
  name.title = job.filename;
  const pill = li.querySelector(".pill");
  pill.textContent = sv.label;
  pill.className = `pill ${sv.cls}`;
  li.querySelector(".job-sub").textContent = subText(job);

  let pct = 0;
  if (job.status === "uploading") pct = job.uploadPct;
  else if (job.status === "done") pct = 100;
  else if (typeof job.progress === "number") pct = job.progress * 100;
  else if (job.status === "error") pct = 0;
  li.querySelector(".job-bar-fill").style.width = `${Math.min(100, Math.max(0, pct))}%`;

  // secondary actions live outside the select button (no nested interactive elements)
  const actions = li.querySelector(".job-actions");
  actions.innerHTML = "";
  if (job.status === "done" && job.camera_id) {
    actions.innerHTML = `<a href="/analytics?camera_id=${encodeURIComponent(job.camera_id)}">View analytics for this video</a>`;
  } else if (job.status === "error" && job.phase === "upload" && job.file) {
    actions.innerHTML = `<button type="button">Retry upload</button>`;
    actions.querySelector("button").addEventListener("click", () => sendFile(job));
  }
  actions.hidden = !actions.innerHTML;
  li.querySelector(".job-select").setAttribute("aria-pressed", String(job.id === activeId));
}

function renderQueueMeta() {
  $("jobEmpty").hidden = order.length > 0;
  $("jobCount").textContent = order.length ? String(order.length) : "";
}

/* ------------------------------------------------------- active job views */
function pillFor(job) {
  const sv = statusView(job);
  let label = sv.label;
  if (job.status === "uploading") label += ` ${Math.round(job.uploadPct)}%`;
  else if (job.status === "processing" && typeof job.progress === "number") label += ` ${Math.round(job.progress * 100)}%`;
  return { label, cls: sv.cls };
}

function renderStageHeader(job) {
  const pill = $("stagePill");
  $("stageName").textContent = job ? job.filename : "Preview";
  if (!job) { pill.hidden = true; return; }
  const p = pillFor(job);
  pill.hidden = false;
  pill.textContent = p.label;
  pill.className = `pill ${p.cls}`;
}

function renderMeta(job) {
  const strip = $("metaStrip");
  strip.hidden = !job;
  if (!job) return;
  const info = stage.info();
  const dur = info.duration || Number(job.duration) || 0;
  $("metaDuration").textContent = dur ? fmtTime(dur) : "—";
  $("metaResolution").textContent = info.width
    ? `${info.width}×${info.height}${info.aspect ? ` (${info.aspect})` : ""}`
    : "—";
  $("metaSize").textContent = fmtBytes(job.size) || "—";
  $("metaCamera").textContent = job.camera_id || "—";
}

function renderDetections(job) {
  const wrap = $("detections");
  const count = $("detCount");
  if (!job) {
    wrap.innerHTML = `<div class="empty-row">Add a video to see its detections here.</div>`;
    count.textContent = "";
    stage.setMarkers([]);
    return;
  }
  const timeline = (job.timeline || []).map((raw) => ({ raw, ...normalizeTimelineEntry(raw) }));
  count.textContent = timeline.length ? String(timeline.length) : "";

  stage.setMarkers(timeline.filter((a) => a.at !== null).map((a) => ({ time: a.at, level: a.level })));

  if (!timeline.length) {
    const msg = job.status === "done" ? ["No alerts", "Nothing in this video triggered a rule."]
      : job.status === "error" ? ["No detections", "Analysis stopped before any alerts were found."]
      : ["Watching for alerts", "Detections appear here as the video is analyzed."];
    wrap.innerHTML = `<div class="empty-row"><strong>${msg[0]}</strong>${msg[1]}</div>`;
    return;
  }

  timeline.sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity));
  const seekable = previewOk;
  wrap.innerHTML = `<div class="det-list">${timeline.slice(0, 200).map((a) => `
    <button type="button" class="det" ${a.at !== null && seekable ? `data-t="${a.at}"` : "disabled"}
            title="${seekable ? "Jump to this moment" : "Preview isn't available for this file"}">
      <span class="alert-tag ${a.level === 2 ? "l2" : "l1"}"
            title="${a.level === 2 ? "Level 2 · AI-verified critical" : "Level 1 · rule-based warning"}">L${a.level}</span>
      <span class="det-text">${esc(a.text)}</span>
      ${a.time ? `<span class="det-time">${esc(a.time)}</span>` : ""}
    </button>`).join("")}</div>`;
}

$("detections").addEventListener("click", (e) => {
  const btn = e.target.closest(".det[data-t]");
  if (!btn) return;
  stage.seek(Math.max(0, Number(btn.dataset.t) - SEEK_LEAD_SECONDS));
  stage.play();
});

// Called whenever the active job's data changes (poll tick, upload progress).
function refresh(job) {
  updateItem(job);
  if (job.id !== activeId) return;
  renderStageHeader(job);
  renderMeta(job);
  // rebuilding the list on every tick would eat clicks; only redo it when it changed
  const sig = `${job.timeline.length}|${job.status}|${previewOk}`;
  if (job._detSig !== sig) { job._detSig = sig; renderDetections(job); }
}

function selectJob(id) {
  const job = jobs.get(id);
  if (!job || id === activeId) return;
  activeId = id;
  items.forEach((li, jid) => li.querySelector(".job-select").setAttribute("aria-pressed", String(jid === id)));
  previewOk = false;
  job._detSig = null;
  stage.load({
    src: job.objectUrl || job.videoUrl,
    name: job.filename,
    size: job.size || undefined,
    unsupportedHint: "Analysis runs on the server, so it isn't affected.",
  });
  refresh(job);
}

stageRoot.addEventListener("vs:metadata", () => {
  previewOk = true;
  const job = jobs.get(activeId);
  if (job) { job._detSig = null; refresh(job); }
});
stageRoot.addEventListener("vs:error", () => {
  previewOk = false;
  const job = jobs.get(activeId);
  if (job) { job._detSig = null; renderMeta(job); renderDetections(job); }
});

/* ------------------------------------------------------------ adding jobs */
function addJob(job, atTop) {
  jobs.set(job.id, job);
  if (atTop) order.unshift(job.id); else order.push(job.id);
  createItem(job, atTop);
  updateItem(job);
  renderQueueMeta();
}

function rekey(job, newId) {
  const oldId = job.id;
  jobs.delete(oldId);
  job.id = newId;
  jobs.set(newId, job);
  order[order.indexOf(oldId)] = newId;
  const li = items.get(oldId);
  items.delete(oldId);
  items.set(newId, li);
  li.id = `job-${newId}`;
  li.dataset.id = newId;
  if (activeId === oldId) activeId = newId;
}

function showDropzoneError(message) {
  const el = $("dzError");
  el.textContent = message;
  el.hidden = !message;
}

function uploadFile(file) {
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!ALLOWED_EXT.includes(ext)) {
    const msg = `"${file.name}" isn't a supported video type. Use MP4, MOV, AVI, MKV or WebM.`;
    showDropzoneError(msg);
    showToast(`Unsupported file type "${ext}"`, "warning");
    return;
  }
  if (file.size === 0) {
    showDropzoneError(`"${file.name}" is empty.`);
    showToast(`"${file.name}" is empty`, "warning");
    return;
  }
  showDropzoneError("");

  const job = {
    id: `pending-${Date.now()}-${++pendingSeq}`,
    filename: file.name, camera_id: $("uploadCameraId").value.trim(),
    status: "uploading", phase: "upload", uploadPct: 0,
    progress: null, eta_seconds: null, timeline: [], error: null,
    file, size: file.size, objectUrl: URL.createObjectURL(file), videoUrl: null,
  };
  addJob(job, true);
  selectJob(job.id);        // the clip is on screen before the upload finishes
  sendFile(job);
}

// XMLHttpRequest rather than fetch: same endpoint and payload, but it reports
// upload progress, which matters for large clips.
function sendFile(job) {
  const formData = new FormData();
  formData.append("file", job.file);
  if (job.camera_id) formData.append("camera_id", job.camera_id);

  job.status = "uploading"; job.phase = "upload"; job.uploadPct = 0; job.error = null;
  refresh(job);

  const fail = (message) => {
    job.status = "error"; job.error = message;
    refresh(job);
    showToast(message, "critical");
  };

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    job.uploadPct = (e.loaded / e.total) * 100;
    refresh(job);
  };
  xhr.onload = () => {
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch (_) { /* handled below */ }
    if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
      rekey(job, data.job_id);
      job.phase = "server"; job.status = "queued"; job.uploadPct = 100;
      job.videoUrl = `/api/upload/${encodeURIComponent(data.job_id)}/video`;
      if (data.camera_id) job.camera_id = data.camera_id;
      // freshly started by this page: sound/toast for every alert from the first one
      timelineSeen[data.job_id] = 0;
      showToast(`Uploaded "${job.filename}" — analysis started`, "info");
      refresh(job);
      pollJob(data.job_id);
    } else {
      fail((data && data.error) || `Upload failed (HTTP ${xhr.status})`);
    }
  };
  xhr.onerror = () => fail("Upload failed — server unreachable");
  xhr.send(formData);
}

function pollJob(jobId) {
  if (pollers[jobId]) return;
  pollers[jobId] = setInterval(async () => {
    try {
      const res = await fetch(`/api/upload/${jobId}`);
      const data = await res.json();
      if (!data.ok) return;
      const job = jobs.get(jobId);
      if (!job) return;
      applyServerState(job, data);

      if (job.status === "done" || job.status === "error") {
        clearInterval(pollers[jobId]);
        delete pollers[jobId];
        showToast(job.status === "done"
          ? `Analysis finished for "${job.filename}"`
          : `Analysis failed for "${job.filename}"`, job.status === "done" ? "info" : "critical");
      }
    } catch (e) {
      // transient network hiccup — keep polling
    }
  }, POLL_MS);
}

// Repopulate the queue from the server on every load, so past uploads (and
// anything still processing) don't vanish when the page is refreshed.
async function loadExistingJobs() {
  try {
    const res = await fetch("/api/uploads");
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.jobs)) return;
    for (const raw of data.jobs) {
      if (jobs.has(raw.job_id)) continue;
      const job = jobFromServer(raw);
      addJob(job, false);
      timelineSeen[job.id] = (raw.timeline || []).length;
      applyServerState(job, raw, { silent: true });
      if (job.status === "processing" || job.status === "queued") pollJob(job.id);
    }
    // open the most recent clip, unless the person has already added one
    if (!activeId && order.length) selectJob(order[0]);
  } catch (e) {
    // server unreachable on load — new uploads still work once it returns
  }
}

/* ----------------------------------------------------- picking and dropping */
function handleFiles(fileList) {
  Array.from(fileList || []).forEach(uploadFile);
}

dropzone.addEventListener("click", () => fileInput.click());
$("addVideoBtn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { handleFiles(fileInput.files); fileInput.value = ""; });

const hasFiles = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
let dragDepth = 0;
stageRoot.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  stageRoot.classList.add("is-dragging");
});
stageRoot.addEventListener("dragover", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
stageRoot.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) stageRoot.classList.remove("is-dragging");
});
stageRoot.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  stageRoot.classList.remove("is-dragging");
  handleFiles(e.dataTransfer.files);
});
// a file dropped outside the stage would otherwise navigate the tab to the video
["dragover", "drop"].forEach((ev) =>
  window.addEventListener(ev, (e) => { if (hasFiles(e)) e.preventDefault(); })
);

renderQueueMeta();
renderDetections(null);
renderStageHeader(null);
document.addEventListener("DOMContentLoaded", loadExistingJobs);
if (document.readyState !== "loading") loadExistingJobs();
