// history.js — powers the Alert History page.

let currentIncidents = [];
let quickFilter = "all";

function riskColor(score) {
  const v = Number(score) || 0;
  if (v >= 0.7) return "var(--tier-crit)";
  if (v >= 0.4) return "var(--tier-warn)";
  return "var(--tier-safe)";
}

function incidentCard(row, idx) {
  const risk = row.risk_score != null ? Number(row.risk_score).toFixed(2) : "—";
  const thumb = row.image_url || "";
  const preview = row.video_url
    ? `<video class="incident-preview-video" muted loop playsinline preload="none" data-src="${row.video_url}"></video>`
    : "";
  return `
    <div class="incident-card stagger-in" style="--i:${idx}" data-idx="${idx}">
      <div class="incident-thumb-wrap">
        ${thumb ? `<img class="incident-thumb" src="${thumb}" alt="Incident snapshot" loading="lazy">`
                 : `<div class="incident-thumb"></div>`}
        ${preview}
      </div>
      <div class="incident-body">
        <div class="incident-top">
          <span>${row.date || ""} ${row.time || ""}</span>
          <span class="incident-risk" style="color:${riskColor(row.risk_score)}">${risk}</span>
        </div>
        <div class="incident-cam">${row.camera_id || "Unknown camera"}</div>
        ${row.false_positive && row.false_positive !== "0" ? `<span class="fp-badge">False positive</span>` : ""}
      </div>
    </div>
  `;
}

function skeletonCards(n = 8) {
  return Array.from({ length: n }).map(() => `
    <div class="skeleton-card">
      <div class="skeleton skeleton-thumb"></div>
      <div class="skeleton skeleton-line" style="width:70%"></div>
      <div class="skeleton skeleton-line" style="width:40%"></div>
    </div>
  `).join("");
}

async function loadIncidents() {
  const grid = document.getElementById("incidentGrid");
  const date = document.getElementById("filterDate").value;
  const camera = document.getElementById("filterCamera").value.trim();

  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (camera) params.set("camera_id", camera);

  grid.innerHTML = skeletonCards();
  try {
    const res = await fetch(`/api/evidence?${params.toString()}`);
    const data = await res.json();
    currentIncidents = data.incidents || [];

    if (quickFilter === "critical") {
      currentIncidents = currentIncidents.filter(r => (Number(r.risk_score) || 0) >= 0.7);
    }

    if (!currentIncidents.length) {
      grid.innerHTML = `<div class="empty-row">No incidents match these filters.</div>`;
      return;
    }
    grid.innerHTML = currentIncidents.map((row, idx) => incidentCard(row, idx)).join("");
    grid.querySelectorAll(".incident-card").forEach(card => {
      card.addEventListener("click", () => openModal(Number(card.dataset.idx)));
      const vid = card.querySelector(".incident-preview-video");
      if (vid) {
        card.addEventListener("mouseenter", () => {
          if (!vid.getAttribute("src")) vid.src = vid.dataset.src;
          vid.play().catch(() => {});
        });
        card.addEventListener("mouseleave", () => vid.pause());
      }
    });
  } catch (e) {
    grid.innerHTML = `<div class="empty-row">Could not load incidents — check the server.</div>`;
  }
}

document.getElementById("quickFilters").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  document.querySelectorAll("#quickFilters .chip").forEach(c => c.classList.remove("active"));
  chip.classList.add("active");
  quickFilter = chip.dataset.filter;

  if (quickFilter === "today") {
    document.getElementById("filterDate").value = new Date().toISOString().slice(0, 10);
  } else if (quickFilter === "all") {
    document.getElementById("filterDate").value = "";
  }
  loadIncidents();
});

let activeIdx = null;

function openModal(idx) {
  const row = currentIncidents[idx];
  if (!row) return;
  activeIdx = idx;

  document.getElementById("modalTitle").textContent = `Incident · ${row.camera_id || "unknown camera"}`;

  const media = document.getElementById("modalMedia");
  media.innerHTML = "";
  if (row.video_url) {
    media.innerHTML = `<video src="${row.video_url}" controls preload="metadata"></video>`;
  } else if (row.image_url) {
    media.innerHTML = `<img src="${row.image_url}" alt="Incident snapshot">`;
  } else {
    media.innerHTML = `<div class="empty-row">No evidence media saved for this incident.</div>`;
  }

  const meta = document.getElementById("modalMeta");
  const fields = [
    ["Date", row.date], ["Time", row.time],
    ["Camera", row.camera_id], ["Risk Score", row.risk_score],
    ["Level", row.level], ["False Positive", row.false_positive && row.false_positive !== "0" ? "Yes" : "No"],
  ];
  meta.innerHTML = fields.map(([k, v]) => `
    <div><div class="k">${k}</div><div class="v">${v ?? "—"}</div></div>
  `).join("");

  document.getElementById("modalBackdrop").classList.add("open");
}

function closeModal() {
  document.getElementById("modalBackdrop").classList.remove("open");
  activeIdx = null;
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modalBackdrop").addEventListener("click", e => {
  if (e.target.id === "modalBackdrop") closeModal();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

document.getElementById("markFpBtn").addEventListener("click", async () => {
  if (activeIdx === null) return;
  const btn = document.getElementById("markFpBtn");
  btn.disabled = true;
  btn.textContent = "Marking…";
  try {
    const res = await fetch(`/api/evidence/${activeIdx}/false_positive`, { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      showToast("Marked as false positive", "info");
      closeModal();
      loadIncidents();
    } else {
      showToast("Could not update this incident", "critical");
    }
  } catch (e) {
    showToast("Server unreachable", "critical");
  } finally {
    btn.disabled = false;
    btn.textContent = "Mark False Positive";
  }
});

document.getElementById("filterDate").addEventListener("change", loadIncidents);
document.getElementById("filterCamera").addEventListener("input", debounce(loadIncidents, 400));
document.getElementById("clearFilters").addEventListener("click", () => {
  document.getElementById("filterDate").value = "";
  document.getElementById("filterCamera").value = "";
  quickFilter = "all";
  document.querySelectorAll("#quickFilters .chip").forEach(c => c.classList.toggle("active", c.dataset.filter === "all"));
  loadIncidents();
});
document.getElementById("refreshHistory").addEventListener("click", loadIncidents);

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

loadIncidents();
