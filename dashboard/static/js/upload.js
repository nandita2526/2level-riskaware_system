// upload.js — powers the Upload Video page.

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const jobList = document.getElementById("jobList");
const jobPollers = {};

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
    </div>
  `;
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

      const status = (data.status || "processing").toLowerCase();
      const statusEl = card.querySelector(".job-status");
      const fillEl = card.querySelector(".progress-fill");
      const pctEl = card.querySelector(".progress-pct");
      const summaryEl = card.querySelector(".job-summary");

      statusEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
      statusEl.className = `job-status ${status}`;
      card.classList.toggle("indeterminate", status === "processing" && data.progress == null);

      if (typeof data.progress === "number") {
        const p = Math.min(100, Math.max(0, data.progress));
        fillEl.style.width = `${p}%`;
        if (pctEl) pctEl.textContent = `${Math.round(p)}%`;
      } else if (status === "done") {
        fillEl.style.width = "100%";
        if (pctEl) pctEl.textContent = "100%";
      }

      if (status === "done") {
        card.classList.add("done");
        const bits = [];
        if (data.total_alerts != null) bits.push(`${data.total_alerts} alerts`);
        if (data.ai_verified != null) bits.push(`${data.ai_verified} AI-verified`);
        if (data.duration != null) bits.push(`${data.duration}s analyzed`);
        summaryEl.textContent = bits.length ? bits.join(" · ") : "Analysis complete";
        clearInterval(jobPollers[jobId]);
        delete jobPollers[jobId];
        showToast(`Analysis finished for job ${jobId}`, "info");
      } else if (status === "error") {
        summaryEl.textContent = data.error || "Analysis failed";
        clearInterval(jobPollers[jobId]);
        delete jobPollers[jobId];
        showToast(`Job ${jobId} failed`, "critical");
      }
    } catch (e) {
      // transient network hiccup — keep polling
    }
  }, 1500);
}
