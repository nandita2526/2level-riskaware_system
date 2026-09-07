// upload.js — powers the Upload Video page.
//
// BUGFIX: this file used to only ever update the progress bar + a one-line
// "done" summary (via applyJobState). It never rendered the per-alert
// timeline that module8_video_upload_analyzer.py already sends back on every
// /api/upload/<job_id> response (the `timeline` array), and it never called
// playAlertTone()/showToast()/flashCritical() the way live.js does for the
// live camera page. That's why uploading a suspicious video never showed any
// alerts and never made a sound in the browser — the data was there, nothing
// on this page was reading it. Fixed below: renderJobTimeline() shows each
// alert as it appears, and sounds/toasts fire for every NEW entry seen since
// this page started watching the job (not retroactively for old, already-
// finished jobs reloaded from disk, which would otherwise replay a wall of
// sounds on every page refresh).

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const jobList = document.getElementById("jobList");
const jobPollers = {};
// job_id -> number of timeline entries already rendered/sounded for that job.
const jobTimelineSeen = {};

dropzone.addEventListener("click", () => fileInput.click());

["dragenter", "dragover"].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
dropzone.addEventListener("drop", e => {
  Array.from(e.dataTransfer.files || []).forEach(uploadFile);
});
fileInput.addEventListener("change", () => {
  Array.from(fileInput.files || []).forEach(uploadFile);
  fileInput.value = "";
});

// Repopulate the job list from the server on every page load/refresh, so
// past uploads (and anything still processing) don't just vanish — the
// job list used to live only in this page's in-memory DOM, so refreshing
// the browser lost it even though the server was still tracking (or had
// already finished) every job.
document.addEventListener("DOMContentLoaded", loadExistingJobs);

async function loadExistingJobs() {
  try {
    const res = await fetch("/api/uploads");
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.jobs)) return;

    for (const job of data.jobs) {
      const filename = job.filename || (job.video_path || "").split("/").pop() || "video";
      jobList.insertAdjacentHTML("beforeend", jobCardTemplate(job.job_id, filename));
      const card = document.getElementById(`job-${job.job_id}`);
      // Jobs that already finished before this page loaded: show their full
      // timeline, but don't replay alert sounds/toasts for events that
      // happened in the past — only sound for genuinely new activity from
      // here on (relevant for jobs still "processing" on load).
      jobTimelineSeen[job.job_id] = (job.timeline || []).length;
      applyJobState(card, job, { silent: true });
      if (job.status === "processing" || job.status === "queued") {
        pollJob(job.job_id, card);
      }
    }
  } catch (e) {
    // server unreachable on load — the page still works for new uploads
    // once connectivity returns, so fail silently here.
  }
}

function jobCardTemplate(id, filename) {
  return `
    <div class="job-card" id="job-${id}">
      <div class="job-card-head">
        <span class="job-name">${filename}</span>
        <span class="job-status queued">Queued</span>
      </div>
      <div class="job-progress-row">
        <div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>
        <span class="progress-pct">0%</span>
      </div>
      <div class="job-summary"></div>
      <div class="job-timeline"></div>
    </div>
  `;
}

function normalizeTimelineEntry(entry) {
  const level = entry.type === "critical" ? 2 : 1;
  const rules = (entry.triggered_rules || []).join(", ") || "rule violation";
  const parts = [`Track #${entry.track_id}`];
  if (entry.behaviour_class && entry.behaviour_class !== "Normal") parts.push(entry.behaviour_class);
  parts.push(rules);
  if (typeof entry.risk_score === "number") parts.push(`risk ${entry.risk_score.toFixed(2)}`);
  return {
    level,
    text: parts.join(" · "),
    time: typeof entry.video_time_s === "number" ? `@${entry.video_time_s.toFixed(1)}s` : (entry.time || ""),
  };
}

// Renders the alerts found so far in a job's timeline (same visual style as
// the Live page's alert console), and — for anything not seen before by this
// page — plays the matching alert tone/toast/flash, exactly like live.js
// does for the live camera feed.
function renderJobTimeline(card, job, opts = {}) {
  const wrap = card.querySelector(".job-timeline");
  if (!wrap) return;
  const timeline = job.timeline || [];

  if (!timeline.length) {
    wrap.innerHTML = "";
    return;
  }

  const alreadySeen = jobTimelineSeen[job.job_id] ?? timeline.length;
  const newEntries = opts.silent ? [] : timeline.slice(alreadySeen);
  jobTimelineSeen[job.job_id] = timeline.length;

  // Most recent first, capped so one long video can't blow up the DOM.
  const items = timeline.slice().reverse().slice(0, 25).map(normalizeTimelineEntry);
  wrap.innerHTML = items.map(a => `
    <div class="alert-row">
      <span class="alert-tag ${a.level === 2 ? "l2" : "l1"}">L${a.level}</span>
      <span class="alert-text">${a.text}</span>
      ${a.time ? `<span class="alert-time">${a.time}</span>` : ""}
    </div>
  `).join("");

  newEntries.forEach(raw => {
    const a = normalizeTimelineEntry(raw);
    showToast(
      `${a.level === 2 ? "Level-2 critical" : "Level-1 warning"} alert — ${a.text}`,
      a.level === 2 ? "critical" : "warning"
    );
    if (a.level === 2 && typeof flashCritical === "function") flashCritical();
    if (typeof playAlertTone === "function") playAlertTone(a.level);
  });
}

// Renders a job's current status/progress/summary onto its card — shared by
// both the initial page-load render and the live poller so they stay in sync.
// opts.silent suppresses alert sounds/toasts for timeline entries (used when
// first painting a job that already existed before this page loaded).
function applyJobState(card, data, opts = {}) {
  const status = (data.status || "processing").toLowerCase();
  const statusEl = card.querySelector(".job-status");
  const fillEl = card.querySelector(".progress-fill");
  const pctEl = card.querySelector(".progress-pct");
  const summaryEl = card.querySelector(".job-summary");

  statusEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  statusEl.className = `job-status ${status}`;
  card.classList.toggle("indeterminate", status === "processing" && data.progress == null);

  if (typeof data.progress === "number") {
    // Backend sends progress as a 0..1 fraction — convert to a 0..100 percent.
    const p = Math.min(100, Math.max(0, data.progress * 100));
    fillEl.style.width = `${p}%`;
    if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
  } else if (status === "done") {
    fillEl.style.width = "100%";
    if (pctEl) pctEl.textContent = "100%";
  } else if (pctEl) {
    pctEl.textContent = "…";
  }

  if (status === "processing" && typeof data.eta_seconds === "number") {
    const mins = Math.floor(data.eta_seconds / 60);
    const secs = Math.round(data.eta_seconds % 60);
    summaryEl.textContent = `~${mins > 0 ? mins + "m " : ""}${secs}s remaining`;
  }

  if (status === "done") {
    card.classList.add("done");
    fillEl.style.width = "100%";
    if (pctEl) pctEl.textContent = "100%";
    const bits = [];
    if (data.total_alerts != null) bits.push(`${data.total_alerts} alerts`);
    if (data.ai_verified != null) bits.push(`${data.ai_verified} AI-verified`);
    if (data.duration != null) bits.push(`${data.duration}s analyzed`);
    summaryEl.innerHTML = (bits.length ? bits.join(" · ") : "Analysis complete") +
      (data.camera_id
        ? ` &nbsp;·&nbsp; <a href="/analytics?camera_id=${encodeURIComponent(data.camera_id)}" class="job-analytics-link">View analytics for this video →</a>`
        : "");
  } else if (status === "error") {
    summaryEl.textContent = data.error || "Analysis failed";
  }

  renderJobTimeline(card, data, opts);
}

async function uploadFile(file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (![".mp4", ".avi", ".mov", ".mkv", ".webm"].includes(ext)) {
    showToast(`Unsupported file type "${ext}"`, "warning");
    return;
  }

  const cameraId = document.getElementById("uploadCameraId").value.trim();
  const formData = new FormData();
  formData.append("file", file);
  if (cameraId) formData.append("camera_id", cameraId);

  const tempId = "pending-" + Date.now();
  jobList.insertAdjacentHTML("afterbegin", jobCardTemplate(tempId, file.name));
  const card = document.getElementById(`job-${tempId}`);

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!data.ok) {
      card.querySelector(".job-status").textContent = "Error";
      card.querySelector(".job-status").className = "job-status error";
      card.querySelector(".job-summary").textContent = data.error || "Upload failed";
      showToast(data.error || "Upload failed", "critical");
      return;
    }
    card.id = `job-${data.job_id}`;
    // Freshly started by this page in this session — sound/toast for every
    // alert this job produces, right from the first one.
    jobTimelineSeen[data.job_id] = 0;
    showToast(`Uploaded "${file.name}" — analysis started`, "info");
    pollJob(data.job_id, card);
  } catch (e) {
    card.querySelector(".job-status").textContent = "Error";
    card.querySelector(".job-status").className = "job-status error";
    card.querySelector(".job-summary").textContent = "Could not reach the server";
    showToast("Upload failed — server unreachable", "critical");
  }
}

function pollJob(jobId, card) {
  if (jobPollers[jobId]) return;
  jobPollers[jobId] = setInterval(async () => {
    try {
      const res = await fetch(`/api/upload/${jobId}`);
      const data = await res.json();
      if (!data.ok) return;

      applyJobState(card, data);
      const status = (data.status || "processing").toLowerCase();

      if (status === "done") {
        clearInterval(jobPollers[jobId]);
        delete jobPollers[jobId];
        showToast(`Analysis finished for job ${jobId}`, "info");
      } else if (status === "error") {
        clearInterval(jobPollers[jobId]);
        delete jobPollers[jobId];
        showToast(`Job ${jobId} failed`, "critical");
      }
    } catch (e) {
      // transient network hiccup — keep polling
    }
  }, 1500);
}
