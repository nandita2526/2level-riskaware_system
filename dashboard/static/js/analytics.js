/* Analytics page: totals, timeline chart, camera x hour heatmap */

function fmtClock() {
  const now = new Date();
  const el = document.getElementById("clock");
  if (el) el.textContent = now.toLocaleTimeString("en-GB");
}
setInterval(fmtClock, 1000);
fmtClock();

async function loadAnalytics() {
  try {
    const res = await fetch("/api/analytics");
    const data = await res.json();
    renderCards(data);
    drawTimeline(data.timeline || []);
    renderHeatmap(data.heatmap || []);
  } catch (err) {
    console.error("Failed to load analytics:", err);
  }
}

function renderCards(data) {
  const cards = document.getElementById("analyticsCards");
  cards.innerHTML = `
    <div class="stat-card"><span class="stat-label">Total Alerts Today</span><span class="stat-value">${data.total_alerts_today}</span></div>
    <div class="stat-card tier-critical"><span class="stat-label">AI-Verified Alerts</span><span class="stat-value">${data.total_ai_verified_alerts}</span></div>
    <div class="stat-card tier-elevated"><span class="stat-label">False Positive Rate</span><span class="stat-value">${(data.false_positive_rate * 100).toFixed(1)}%</span></div>
    <div class="stat-card"><span class="stat-label">Average Risk</span><span class="stat-value">${data.average_risk.toFixed(2)}</span></div>
    <div class="stat-card"><span class="stat-label">Total Alerts (all time)</span><span class="stat-value">${data.total_alerts_all_time}</span></div>
  `;
}

function drawTimeline(timeline) {
  const canvas = document.getElementById("timelineChart");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);

  if (!timeline.length) return;
  const maxCount = Math.max(1, ...timeline.map(t => t.count));
  const barW = W / timeline.length;

  timeline.forEach((t, i) => {
    const barH = (t.count / maxCount) * (H - 24);
    ctx.fillStyle = t.count > 0 ? "rgba(23,227,176,0.55)" : "rgba(255,255,255,0.06)";
    ctx.fillRect(i * barW + 2, H - barH - 18, barW - 4, barH);
    ctx.fillStyle = "#6E7F8D";
    ctx.font = "9px IBM Plex Mono";
    ctx.textAlign = "center";
    ctx.fillText(t.hour, i * barW + barW / 2, H - 4);
  });
}

function renderHeatmap(heatmap) {
  const container = document.getElementById("heatmapContainer");
  if (!heatmap.length) {
    container.innerHTML = `<div class="empty-state">No incidents logged yet.</div>`;
    return;
  }

  const maxCount = Math.max(1, ...heatmap.flatMap(row => row.hours.map(h => h.count)));

  container.innerHTML = heatmap.map(row => `
    <div class="heatmap-row">
      <span class="heatmap-cam-label">${row.camera_id}</span>
      <div class="heatmap-cells">
        ${row.hours.map(h => {
          const alpha = h.count === 0 ? 0.05 : 0.15 + 0.85 * (h.count / maxCount);
          return `<div class="heatmap-cell" title="${row.camera_id} ${h.hour}:00 — ${h.count} alert(s)"
                       style="background: rgba(255,59,92,${alpha.toFixed(2)});"></div>`;
        }).join("")}
      </div>
    </div>
  `).join("");
}

loadAnalytics();
setInterval(loadAnalytics, 10000);
