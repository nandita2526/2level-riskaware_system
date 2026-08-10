// live.js — powers the Live Camera page.

const POLL_MS = 1000;
let lastAlertKey = null;
let riskHistory = [];

function classifyTierKey(key) {
  const k = key.toLowerCase();
  if (/safe|low|normal|green/.test(k)) return "safe";
  if (/warn|watch|medium|amber|yellow/.test(k)) return "warn";
  if (/crit|high|danger|red|alert/.test(k)) return "crit";
  return "warn"; // unknown tiers default to the middle bucket rather than being dropped
}

function renderTierCards(tierCounts) {
  const buckets = { safe: 0, warn: 0, crit: 0 };
  Object.entries(tierCounts || {}).forEach(([key, val]) => {
    buckets[classifyTierKey(key)] += Number(val) || 0;
  });

  const wrap = document.getElementById("tierCards");
  const labels = { safe: "Safe", warn: "Watch", crit: "Critical" };
  const prevValues = {};
  wrap.querySelectorAll(".tier-card").forEach(card => {
    const cls = ["safe", "warn", "crit"].find(c => card.classList.contains(c));
    prevValues[cls] = Number(card.querySelector(".n").textContent) || 0;
  });

  wrap.innerHTML = ["safe", "warn", "crit"].map(cls => {
    const changed = buckets[cls] > (prevValues[cls] || 0);
    return `<div class="tier-card ${cls} ${changed ? "pop" : ""}">
      <div class="n">${buckets[cls]}</div>
      <div class="lbl">${labels[cls]}</div>
    </div>`;
  }).join("");

  const hudFrame = document.getElementById("hudFrame");
  hudFrame.classList.toggle("alert-critical", buckets.crit > 0);

  setTimeout(() => {
    wrap.querySelectorAll(".pop").forEach(el => el.classList.remove("pop"));
  }, 350);
}

const RISK_GAUGE_CIRC = 138.2;

function updateRiskGauge(maxRisk) {
  const fill = document.getElementById("riskGaugeFill");
  const val = document.getElementById("riskGaugeVal");
  if (!fill || !val) return;
  const pct = Math.max(0, Math.min(1, Number(maxRisk) || 0));
  fill.style.strokeDashoffset = String(RISK_GAUGE_CIRC * (1 - pct));
  fill.style.stroke = pct >= 0.7 ? "var(--tier-crit)" : pct >= 0.4 ? "var(--tier-warn)" : "var(--accent-live)";
  animateNumber(val, pct, { decimals: 2 });
}

function renderTrackList(activeTracks) {
  const wrap = document.getElementById("trackList");
  if (!wrap) return;
  const entries = Object.entries(activeTracks || {});
  if (!entries.length) {
    wrap.innerHTML = `<div class="empty-row">No active tracks</div>`;
    return;
  }
  entries.sort((a, b) => (Number(b[1]?.risk_score) || 0) - (Number(a[1]?.risk_score) || 0));
  wrap.innerHTML = entries.slice(0, 12).map(([id, t]) => {
    const score = Number(t?.risk_score) || 0;
    const tier = score >= 0.7 ? "crit" : score >= 0.4 ? "warn" : "safe";
    return `
      <div class="track-row">
        <span class="track-id">#${id}</span>
        <span class="track-bar-track"><span class="track-bar-fill ${tier}" style="width:${Math.round(score * 100)}%"></span></span>
        <span class="track-score">${score.toFixed(2)}</span>
      </div>`;
  }).join("");
}

function drawSparkline(history) {
  const canvas = document.getElementById("riskSpark");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || 70;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  if (!history.length) return;
  const vals = history.map(p => p.risk ?? 0);
  const max = Math.max(1, ...vals);
  const step = w / Math.max(1, vals.length - 1);

  // filled area
  ctx.beginPath();
  ctx.moveTo(0, h);
  vals.forEach((v, i) => ctx.lineTo(i * step, h - (v / max) * (h - 8) - 2));
  ctx.lineTo(w, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(56,189,248,.35)");
  grad.addColorStop(1, "rgba(56,189,248,0)");
  ctx.fillStyle = grad;
  ctx.fill();

  // line
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = i * step, y = h - (v / max) * (h - 8) - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#38BDF8";
  ctx.lineWidth = 1.6;
  ctx.stroke();

  // latest point
  const lastVal = vals[vals.length - 1];
  const lx = w, ly = h - (lastVal / max) * (h - 8) - 2;
  ctx.beginPath();
  ctx.arc(lx - 2, ly, 3, 0, Math.PI * 2);
  ctx.fillStyle = "#38BDF8";
  ctx.fill();
}

function normalizeAlert(raw, level) {
  if (typeof raw === "string") return { text: raw, level, time: "" };
  return {
    text: raw.message || raw.text || raw.description || JSON.stringify(raw),
    level,
    time: raw.time || raw.timestamp || "",
  };
}

function renderAlertConsole(recentAlerts, recentWarnings) {
  const items = [
    ...(recentAlerts || []).map(a => normalizeAlert(a, 2)),
    ...(recentWarnings || []).map(a => normalizeAlert(a, 1)),
  ];
  const console_ = document.getElementById("alertConsole");

  if (!items.length) {
    console_.innerHTML = `<div class="empty-row">No alerts yet — monitoring…</div>`;
    return;
  }

  const newestKey = JSON.stringify(items[0]);
  if (lastAlertKey && newestKey !== lastAlertKey) {
    const newest = items[0];
    showToast(
      `${newest.level === 2 ? "Level-2" : "Level-1"} alert — ${newest.text}`,
      newest.level === 2 ? "critical" : "warning"
    );
    if (newest.level === 2) flashCritical();
  }
  lastAlertKey = newestKey;

  console_.innerHTML = items.slice(0, 25).map(a => `
    <div class="alert-row">
      <span class="alert-tag ${a.level === 2 ? "l2" : "l1"}">L${a.level}</span>
      <span class="alert-text">${a.text}</span>
      ${a.time ? `<span class="alert-time">${a.time}</span>` : ""}
    </div>
  `).join("");
}

async function pollStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();

    const fpsIsInt = Number.isInteger(Number(data.fps));
    animateNumber(document.getElementById("hudFps"), data.fps ?? 0, { decimals: fpsIsInt ? 0 : 1, duration: 300 });
    animateNumber(document.getElementById("hudUptime"), data.uptime_seconds ?? 0, { decimals: 0, duration: 300 });
    animateNumber(document.getElementById("hudFrames"), data.frame_count ?? 0, { decimals: 0, duration: 300 });

    renderTierCards(data.tier_counts);
    renderAlertConsole(data.recent_alerts, data.recent_warnings);
    renderTrackList(data.active_tracks);

    const scores = Object.values(data.active_tracks || {}).map(t => Number(t?.risk_score) || 0);
    updateRiskGauge(scores.length ? Math.max(...scores) : 0);

    if (Array.isArray(data.risk_history)) {
      riskHistory = data.risk_history;
      drawSparkline(riskHistory);
    }
  } catch (e) {
    // header status chip already communicates offline state; stay quiet here
  }
}

document.getElementById("switchSourceBtn").addEventListener("click", async () => {
  const source = document.getElementById("sourceInput").value.trim();
  const cameraId = document.getElementById("cameraIdInput").value.trim();
  if (!source) { showToast("Enter a source first", "warning"); return; }

  const btn = document.getElementById("switchSourceBtn");
  const hudFrame = document.getElementById("hudFrame");
  btn.textContent = "Switching…";
  btn.disabled = true;
  hudFrame.classList.add("switching");
  try {
    const res = await fetch("/api/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, camera_id: cameraId || undefined }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Switched to source "${data.source}"`, "info");
      // bust the MJPEG stream's connection so it reconnects to the new capture loop
      const img = document.getElementById("feedImg");
      img.addEventListener("load", () => hudFrame.classList.remove("switching"), { once: true });
      img.src = "/video_feed?_=" + Date.now();
    } else {
      showToast(data.error || "Failed to switch source", "critical");
      hudFrame.classList.remove("switching");
    }
  } catch (e) {
    showToast("Could not reach the server", "critical");
    hudFrame.classList.remove("switching");
  } finally {
    btn.textContent = "Switch Source";
    btn.disabled = false;
  }
});

window.addEventListener("resize", () => drawSparkline(riskHistory));

pollStats();
setInterval(pollStats, POLL_MS);
