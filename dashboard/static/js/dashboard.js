/* ==========================================================
   SENTINEL dashboard front-end.
   Polls /api/stats every second and updates the DOM + a
   lightweight canvas sparkline for the risk trend.
   ========================================================== */

const TIER_COLORS = {
  normal:   "#17E3B0",
  warning:  "#FFB238",
  elevated: "#FFB238",
  high:     "#FF8A3D",
  critical: "#FF3B5C",
};

let riskHistory = [];
const seenAlertKeys = new Set();
const seenWarningKeys = new Set();

function fmtClock() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString("en-GB");
}

function fmtUptime(seconds) {
  seconds = Math.floor(seconds || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `Uptime ${h}h ${m}m`;
  if (m > 0) return `Uptime ${m}m ${s}s`;
  return `Uptime ${s}s`;
}

function renderCards(tierCounts, trackedCount) {
  document.getElementById("trackedCount").textContent = trackedCount;
  document.getElementById("normalCount").textContent = tierCounts.normal || 0;
  document.getElementById("elevatedCount").textContent = (tierCounts.warning || 0) + (tierCounts.elevated || 0) + (tierCounts.high || 0);
  document.getElementById("criticalCount").textContent = tierCounts.critical || 0;
}

function renderTracks(activeTracks) {
  const container = document.getElementById("tracksList");
  const ids = Object.keys(activeTracks);

  if (ids.length === 0) {
    container.innerHTML = `<div class="empty-state">No persons currently tracked.</div>`;
    return;
  }

  container.innerHTML = ids.map((id) => {
    const t = activeTracks[id];
    const pct = Math.round(t.risk_score * 100);
    const tier = t.tier || "normal";
    return `
      <div class="track-row tier-${tier}">
        <div class="track-row-top">
          <span><span class="track-id">ID ${id}</span> &nbsp;<span class="track-class">${t.class}</span></span>
          <span class="track-score">${t.risk_score.toFixed(2)} · ${tier.toUpperCase()}</span>
        </div>
        <div class="risk-bar-track">
          <div class="risk-bar-fill ${tier}" style="width:${pct}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderAlerts(recentAlerts) {
  const console_ = document.getElementById("alertConsole");
  if (!recentAlerts || recentAlerts.length === 0) return;

  // Only prepend genuinely new alerts (dedupe on track_id + time)
  const newOnes = recentAlerts.filter(a => {
    const key = `${a.track_id}-${a.time}-${a.risk_score}`;
    if (seenAlertKeys.has(key)) return false;
    seenAlertKeys.add(key);
    return true;
  });

  if (newOnes.length === 0) return;

  const emptyLine = console_.querySelector(".console-empty");
  if (emptyLine) emptyLine.remove();

  const html = newOnes.map(a => `
    <div class="console-line critical">
      <span class="ts">${a.time}</span><span class="tag">CRITICAL</span>
      Track ID ${a.track_id} on ${a.camera_id} — risk score ${a.risk_score.toFixed(2)} — siren + push notification dispatched
    </div>
  `).join("");

  console_.insertAdjacentHTML("afterbegin", html);

  // cap the console at 40 lines
  const lines = console_.querySelectorAll(".console-line:not(.console-empty)");
  if (lines.length > 40) {
    for (let i = 40; i < lines.length; i++) lines[i].remove();
  }
}

function renderWarnings(recentWarnings) {
  const console_ = document.getElementById("warningConsole");
  if (!console_ || !recentWarnings || recentWarnings.length === 0) return;

  const newOnes = recentWarnings.filter(a => {
    const key = `${a.track_id}-${a.time}-${a.risk_score}`;
    if (seenWarningKeys.has(key)) return false;
    seenWarningKeys.add(key);
    return true;
  });
  if (newOnes.length === 0) return;

  const emptyLine = console_.querySelector(".console-empty");
  if (emptyLine) emptyLine.remove();

  const html = newOnes.map(a => `
    <div class="console-line warning">
      <span class="ts">${a.time}</span><span class="tag tag-warning">L1 WARNING</span>
      Track ID ${a.track_id} on ${a.camera_id} — rule score ${a.risk_score.toFixed(2)} —
      rules: ${(a.triggered_rules || []).join(", ") || "n/a"} — beep only, no AI used
    </div>
  `).join("");

  console_.insertAdjacentHTML("afterbegin", html);
  const lines = console_.querySelectorAll(".console-line:not(.console-empty)");
  if (lines.length > 40) {
    for (let i = 40; i < lines.length; i++) lines[i].remove();
  }
}

function drawRiskChart(history) {
  const canvas = document.getElementById("riskChart");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);

  // grid lines at tier boundaries
  const tiers = [
    { level: 0.40, color: "rgba(255,178,56,0.25)" },
    { level: 0.60, color: "rgba(255,138,61,0.25)" },
    { level: 0.85, color: "rgba(255,59,92,0.35)" },
  ];
  tiers.forEach(t => {
    const y = H - t.level * H;
    ctx.strokeStyle = t.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
    ctx.stroke();
  });
  ctx.setLineDash([]);

  if (!history || history.length < 2) return;

  const n = history.length;
  const stepX = W / Math.max(n - 1, 1);

  // area fill
  ctx.beginPath();
  ctx.moveTo(0, H - history[0].risk * H);
  history.forEach((pt, i) => ctx.lineTo(i * stepX, H - pt.risk * H));
  ctx.lineTo(W, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  const gradient = ctx.createLinearGradient(0, 0, 0, H);
  gradient.addColorStop(0, "rgba(23,227,176,0.18)");
  gradient.addColorStop(1, "rgba(23,227,176,0.0)");
  ctx.fillStyle = gradient;
  ctx.fill();

  // line, colored per-segment by tier of the ending point
  for (let i = 1; i < n; i++) {
    const prev = history[i - 1], curr = history[i];
    ctx.beginPath();
    ctx.moveTo((i - 1) * stepX, H - prev.risk * H);
    ctx.lineTo(i * stepX, H - curr.risk * H);
    ctx.strokeStyle = colorForRisk(curr.risk);
    ctx.lineWidth = 2;
    ctx.stroke();
  }
}

function colorForRisk(r) {
  if (r >= 0.85) return TIER_COLORS.critical;
  if (r >= 0.60) return TIER_COLORS.high;
  if (r >= 0.40) return TIER_COLORS.warning;
  return TIER_COLORS.normal;
}

async function pollStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();

    const dot = document.getElementById("statusDot");
    if (data.running) {
      dot.classList.remove("offline");
    } else {
      dot.classList.add("offline");
    }

    document.getElementById("modelChip").textContent =
      "MODEL: " + (data.model_loaded ? "TRAINED" : "UNTRAINED (demo)");

    const camChip = document.getElementById("cameraId");
    if (camChip && data.camera_id) camChip.textContent = data.camera_id;

    if (!data.running && data.frame_count === undefined) return;

    document.getElementById("fpsValue").textContent = (data.fps || 0).toFixed(1);
    document.getElementById("uptime").textContent = fmtUptime(data.uptime_seconds);

    const activeTracks = data.active_tracks || {};
    renderCards(data.tier_counts || {}, Object.keys(activeTracks).length);
    renderTracks(activeTracks);
    renderAlerts(data.recent_alerts || []);
    renderWarnings(data.recent_warnings || []);
    drawRiskChart(data.risk_history || []);
  } catch (err) {
    console.error("Failed to poll /api/stats:", err);
  }
}

async function switchSource() {
  const source = document.getElementById("sourceInput").value.trim();
  const cameraId = document.getElementById("cameraIdInput").value.trim();
  if (!source) return;
  const btn = document.getElementById("sourceSwitchBtn");
  btn.disabled = true;
  btn.textContent = "Switching...";
  try {
    await fetch("/api/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, camera_id: cameraId || undefined }),
    });
  } catch (err) {
    console.error("Failed to switch source:", err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Switch Source";
  }
}

const sourceBtn = document.getElementById("sourceSwitchBtn");
if (sourceBtn) sourceBtn.addEventListener("click", switchSource);

setInterval(fmtClock, 1000);
setInterval(pollStats, 1000);
fmtClock();
pollStats();
