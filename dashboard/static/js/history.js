// history.js — powers the Alert History page.

let currentIncidents = [];
let quickFilter = "all";

function riskColor(score) {
  const v = Number(score) || 0;
  if (v >= 0.7) return "var(--danger)";
  if (v >= 0.4) return "var(--warn)";
  return "var(--ok)";
}

function incidentCard(row, idx) {
  const risk = row.risk_score != null ? Number(row.risk_score).toFixed(2) : "—";
  const thumb = row.image_url || "";
  const isCritical = row.type === "critical";
  const preview = row.video_url
    ? `<video class="incident-preview-video" muted loop playsinline preload="none" data-src="${esc(row.video_url)}"></video>`
    : "";
  return `
    <div class="incident-card stagger-in" style="--i:${Math.min(idx, 12)}" data-idx="${idx}" tabindex="0" role="button"
         aria-label="Open incident: ${esc(row.behaviour_class || "Suspicious activity")}, ${esc(row.camera_id || "unknown camera")}">
      <div class="incident-thumb-wrap">
        ${thumb ? `<img class="incident-thumb" src="${esc(thumb)}" alt="" loading="lazy">`
                 : `<div class="incident-thumb-placeholder">No snapshot</div>`}
        ${preview}
        <span class="alert-tag incident-level-tag ${isCritical ? "l2" : "l1"}">${isCritical ? "L2 critical" : "L1 warning"}</span>
      </div>
      <div class="incident-body">
        <div class="incident-behaviour">${esc((row.behaviour_class || "Suspicious activity").replace(/_/g, " "))}</div>
        <div class="incident-top">
          <span>${esc(row.date || "")} ${esc(row.time || "")}</span>
          <span class="incident-risk" style="color:${riskColor(row.risk_score)}">${risk}</span>
        </div>
        <div class="incident-cam">${esc(row.camera_id || "Unknown camera")}</div>
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

function renderHistoryStats(data) {
  const wrap = document.getElementById("historyStats");
  if (!wrap) return;
  const today = new Date().toISOString().slice(0, 10);
  const todayCount = (data.incidents || []).filter(r => r.date === today).length;
  const stats = [
    { v: data.count ?? 0, l: "Total incidents", cls: "" },
    { v: data.critical_count ?? 0, l: "Critical (L2, AI-verified)", cls: "crit" },
    { v: data.warning_count ?? 0, l: "Warnings (L1, rule-based)", cls: "warn" },
    { v: todayCount, l: "Today", cls: "accent" },
  ];
  wrap.innerHTML = stats.map(s => `
    <div class="kpi-card ${s.cls}"><div class="v">${s.v}</div><div class="l">${s.l}</div></div>
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
    renderHistoryStats(data);

    if (quickFilter === "critical") {
      currentIncidents = currentIncidents.filter(r => r.type === "critical");
    }

    if (!currentIncidents.length) {
      grid.innerHTML = `<div class="empty-row">No incidents match these filters.</div>`;
      return;
    }
    grid.innerHTML = currentIncidents.map((row, idx) => incidentCard(row, idx)).join("");
    grid.querySelectorAll(".incident-card").forEach(card => {
      card.addEventListener("click", () => openModal(Number(card.dataset.idx), card));
      card.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModal(Number(card.dataset.idx), card); }
      });
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
    grid.innerHTML = `<div class="empty-row">Could not load incidents. Check that the server is running.</div>`;
  }
}

document.getElementById("quickFilters").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  document.querySelectorAll("#quickFilters .chip").forEach(c => {
    c.classList.remove("active"); c.setAttribute("aria-pressed", "false");
  });
  chip.classList.add("active");
  chip.setAttribute("aria-pressed", "true");
  quickFilter = chip.dataset.filter;

  if (quickFilter === "today") {
    document.getElementById("filterDate").value = new Date().toISOString().slice(0, 10);
  } else if (quickFilter === "all") {
    document.getElementById("filterDate").value = "";
  }
  loadIncidents();
});

let activeIdx = null;
let lastFocus = null;
let modalStage = null;   // created on first use; shared by every incident clip

function openModal(idx, opener) {
  const row = currentIncidents[idx];
  if (!row) return;
  activeIdx = idx;
  lastFocus = opener || null;

  document.getElementById("modalTitle").textContent = `Incident · ${row.camera_id || "unknown camera"}`;

  const media = document.getElementById("modalMedia");
  if (modalStage) modalStage.unload();
  media.innerHTML = "";
  modalStage = null;
  if (row.video_url) {
    // a fresh holder per incident, so no stage state leaks from one clip to the next
    const holder = document.createElement("div");
    media.appendChild(holder);
    modalStage = VideoStage.mount(holder, { kind: "video" });
    modalStage.load({
      src: row.video_url,
      name: row.video_path || "incident clip",
      poster: row.image_url || undefined,
      downloadUrl: row.video_url,
      unsupportedHint: "Clips are saved as MPEG-4 Part 2, which some browsers can't play. Download it to open in a desktop player.",
    });
  } else if (row.image_url) {
    media.innerHTML = `<img src="${esc(row.image_url)}" alt="Incident snapshot">`;
  } else {
    media.innerHTML = `<div class="empty-row">No evidence media saved for this incident.</div>`;
  }

  const meta = document.getElementById("modalMeta");
  const fields = [
    ["Type", row.type === "critical" ? "Level-2 · AI-verified critical" : "Level-1 · rule-based warning"],
    ["Behaviour", String(row.behaviour_class || "—").replace(/_/g, " ")],
    ["Date", row.date], ["Time", row.time],
    ["Camera", row.camera_id], ["Risk Score", row.risk_score],
    ["Triggered rules", (row.triggered_rules || "").split("|").join(", ") || "—"],
    ["False Positive", row.false_positive && row.false_positive !== "0" ? "Yes" : "No"],
  ];
  meta.innerHTML = fields.map(([k, v]) => `
    <div class="${k === "Triggered rules" || k === "Type" ? "wide" : ""}"><div class="k">${esc(k)}</div><div class="v">${esc(v ?? "—")}</div></div>
  `).join("");

  // Only Level-2 incidents have a saved evidence_log.csv row to flag.
  const fpBtn = document.getElementById("markFpBtn");
  fpBtn.style.display = row.type === "critical" ? "" : "none";

  document.getElementById("modalBackdrop").classList.add("open");
  document.getElementById("modalClose").focus({ preventScroll: true });
}

function closeModal() {
  const backdrop = document.getElementById("modalBackdrop");
  if (!backdrop.classList.contains("open")) return;
  backdrop.classList.remove("open");
  if (modalStage) { modalStage.unload(); modalStage = null; }   // stop playback and release the file
  document.getElementById("modalMedia").innerHTML = "";
  activeIdx = null;
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modalBackdrop").addEventListener("click", e => {
  if (e.target.id === "modalBackdrop") closeModal();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

document.getElementById("markFpBtn").addEventListener("click", async () => {
  if (activeIdx === null) return;
  const row = currentIncidents[activeIdx];
  if (!row || row.evidence_index == null) return;
  const btn = document.getElementById("markFpBtn");
  btn.disabled = true;
  btn.textContent = "Marking…";
  try {
    const res = await fetch(`/api/evidence/${row.evidence_index}/false_positive`, { method: "POST" });
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
    btn.textContent = "Mark false positive";
  }
});

document.getElementById("filterDate").addEventListener("change", loadIncidents);
document.getElementById("filterCamera").addEventListener("input", debounce(loadIncidents, 400));
document.getElementById("clearFilters").addEventListener("click", () => {
  document.getElementById("filterDate").value = "";
  document.getElementById("filterCamera").value = "";
  quickFilter = "all";
  document.querySelectorAll("#quickFilters .chip").forEach(c => {
    const on = c.dataset.filter === "all";
    c.classList.toggle("active", on);
    c.setAttribute("aria-pressed", String(on));
  });
  loadIncidents();
});
document.getElementById("refreshHistory").addEventListener("click", loadIncidents);

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

loadIncidents();
