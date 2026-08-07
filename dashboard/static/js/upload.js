/* Upload Video page: Choose File -> Upload -> Analyze -> Timeline */

function fmtClock() {
  const now = new Date();
  const el = document.getElementById("clock");
  if (el) el.textContent = now.toLocaleTimeString("en-GB");
}
setInterval(fmtClock, 1000);
fmtClock();

let pollTimer = null;

async function uploadVideo() {
  const fileInput = document.getElementById("videoFile");
  const cameraIdInput = document.getElementById("uploadCameraId");
  const file = fileInput.files[0];
  if (!file) {
    alert("Please choose a video file first.");
    return;
  }

  const form = new FormData();
  form.append("file", file);
  if (cameraIdInput.value.trim()) form.append("camera_id", cameraIdInput.value.trim());

  const btn = document.getElementById("uploadBtn");
  btn.disabled = true;
  btn.textContent = "Uploading...";

  document.getElementById("uploadStatus").style.display = "block";
  document.getElementById("uploadStatusLine").textContent = "Uploading file...";
  document.getElementById("progressFill").style.width = "0%";

  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!data.ok) {
      document.getElementById("uploadStatusLine").textContent = "Error: " + data.error;
      btn.disabled = false;
      btn.textContent = "Upload & Analyze";
      return;
    }
    btn.textContent = "Analyzing...";
    pollJob(data.job_id);
  } catch (err) {
    document.getElementById("uploadStatusLine").textContent = "Upload failed: " + err;
    btn.disabled = false;
    btn.textContent = "Upload & Analyze";
  }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/upload/${jobId}`);
      const job = await res.json();
      if (!job.ok) return;

      const pct = Math.round((job.progress || 0) * 100);
      document.getElementById("progressFill").style.width = pct + "%";
      document.getElementById("uploadStatusLine").textContent =
        `${job.status.toUpperCase()} — ${job.processed_frames}/${job.total_frames || "?"} frames (${pct}%)`;

      if (job.status === "done") {
        clearInterval(pollTimer);
        document.getElementById("uploadBtn").disabled = false;
        document.getElementById("uploadBtn").textContent = "Upload & Analyze";
        renderSummary(job.summary);
        renderTimeline(job.timeline);
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        document.getElementById("uploadStatusLine").textContent = "Error: " + job.error;
        document.getElementById("uploadBtn").disabled = false;
        document.getElementById("uploadBtn").textContent = "Upload & Analyze";
      }
    } catch (err) {
      console.error(err);
    }
  }, 1000);
}

function renderSummary(summary) {
  const panel = document.getElementById("summaryPanel");
  const cards = document.getElementById("summaryCards");
  panel.style.display = "block";
  cards.innerHTML = `
    <div class="stat-card"><span class="stat-label">Duration</span><span class="stat-value">${summary.duration_seconds}s</span></div>
    <div class="stat-card"><span class="stat-label">Frames Analyzed</span><span class="stat-value">${summary.frames_analyzed}</span></div>
    <div class="stat-card tier-elevated"><span class="stat-label">L1 Warnings</span><span class="stat-value">${summary.level1_warnings}</span></div>
    <div class="stat-card tier-critical"><span class="stat-label">Critical (AI-verified)</span><span class="stat-value">${summary.critical_alerts}</span></div>
  `;
}

function renderTimeline(timeline) {
  const panel = document.getElementById("timelinePanel");
  const console_ = document.getElementById("timelineConsole");
  panel.style.display = "block";

  if (!timeline || timeline.length === 0) {
    console_.innerHTML = `<div class="console-line console-empty">&gt; no suspicious events detected in this video</div>`;
    return;
  }

  console_.innerHTML = timeline.map(e => {
    const cls = e.type === "critical" ? "critical" : "warning";
    const tag = e.type === "critical" ? "L2 CRITICAL (AI)" : "L1 WARNING";
    const rules = (e.triggered_rules || []).join(", ") || "n/a";
    return `
      <div class="console-line ${cls}">
        <span class="ts">t=${e.video_time_s}s</span><span class="tag tag-${cls === "critical" ? "critical" : "warning"}">${tag}</span>
        Track ID ${e.track_id} — risk ${e.risk_score.toFixed(2)} — rules: ${rules}
        ${e.behaviour_class ? " — behaviour: " + e.behaviour_class : ""}
      </div>`;
  }).join("");
}

document.getElementById("uploadBtn").addEventListener("click", uploadVideo);
