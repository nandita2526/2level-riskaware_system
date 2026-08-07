/* Alert History page: fetches /api/evidence and renders a table + evidence-viewer modal */

function fmtClock() {
  const now = new Date();
  const el = document.getElementById("clock");
  if (el) el.textContent = now.toLocaleTimeString("en-GB");
}
setInterval(fmtClock, 1000);
fmtClock();

let incidentsCache = [];

async function loadHistory() {
  try {
    const res = await fetch("/api/evidence");
    const data = await res.json();
    incidentsCache = data.incidents || [];
    renderTable(incidentsCache);
    document.getElementById("historyCount").textContent = `${data.count} AI-verified incident(s) logged`;
  } catch (err) {
    console.error("Failed to load evidence:", err);
  }
}

function renderTable(incidents) {
  const body = document.getElementById("historyBody");
  if (!incidents || incidents.length === 0) {
    body.innerHTML = `<tr><td colspan="9" class="empty-state">No incidents logged yet.</td></tr>`;
    return;
  }

  body.innerHTML = incidents.map((r, idx) => `
    <tr class="${r.false_positive ? 'row-false-positive' : ''}">
      <td>${r.date}</td>
      <td>${r.time}</td>
      <td>${r.camera_id}</td>
      <td>${r.behaviour_class}</td>
      <td><span class="risk-pill">${parseFloat(r.risk_score).toFixed(2)}</span></td>
      <td>${parseFloat(r.ai_confidence).toFixed(2)}</td>
      <td class="rules-cell">${(r.triggered_rules || "").replace(/\|/g, ", ") || "—"}</td>
      <td>
        ${r.image_url ? `<a href="#" class="ev-link" data-idx="${idx}">View</a>` : "—"}
      </td>
      <td>
        ${r.false_positive ? '<span class="fp-tag">FALSE POSITIVE</span>' :
          `<button class="btn-secondary btn-fp" data-idx="${idx}">Mark False Positive</button>`}
      </td>
    </tr>
  `).join("");

  body.querySelectorAll(".ev-link").forEach(el =>
    el.addEventListener("click", (e) => { e.preventDefault(); openEvidence(incidents[el.dataset.idx]); }));
  body.querySelectorAll(".btn-fp").forEach(el =>
    el.addEventListener("click", () => markFalsePositive(parseInt(el.dataset.idx, 10))));
}

function openEvidence(row) {
  document.getElementById("evidenceModalTitle").textContent =
    `${row.camera_id} — ${row.date} ${row.time} — ${row.behaviour_class}`;
  const body = document.getElementById("evidenceModalBody");
  let html = "";
  if (row.image_url) html += `<img src="${row.image_url}" class="evidence-image" alt="Snapshot">`;
  if (row.video_url) html += `<video src="${row.video_url}" controls class="evidence-video"></video>`;
  html += `<div class="evidence-meta">Risk ${parseFloat(row.risk_score).toFixed(2)} · AI confidence ${parseFloat(row.ai_confidence).toFixed(2)} · Rules: ${(row.triggered_rules || "").replace(/\|/g, ", ") || "—"}</div>`;
  body.innerHTML = html;
  document.getElementById("evidenceModal").classList.add("open");
}

async function markFalsePositive(idxFromTop) {
  // incidents are returned most-recent-first, so index-from-end = idxFromTop
  try {
    await fetch(`/api/evidence/${idxFromTop}/false_positive`, { method: "POST" });
    loadHistory();
  } catch (err) {
    console.error(err);
  }
}

document.getElementById("evidenceModalClose").addEventListener("click", () =>
  document.getElementById("evidenceModal").classList.remove("open"));
document.getElementById("evidenceModal").addEventListener("click", (e) => {
  if (e.target.id === "evidenceModal") e.currentTarget.classList.remove("open");
});

loadHistory();
setInterval(loadHistory, 5000);
