// common.js — shared header status, toast, and animation helpers, loaded on every page.

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Tweens the text content of `el` from its current numeric value to `target`.
// opts: { decimals, duration, suffix }
function animateNumber(el, target, opts = {}) {
  if (!el) return;
  const decimals = opts.decimals ?? 0;
  const suffix = opts.suffix ?? "";
  const end = Number(target) || 0;
  const start = parseFloat((el.textContent || "0").replace(/[^\d.-]/g, "")) || 0;
  const duration = prefersReducedMotion() ? 0 : (opts.duration ?? 400);

  if (duration === 0 || Math.abs(start - end) < Math.pow(10, -decimals)) {
    el.textContent = end.toFixed(decimals) + suffix;
    return;
  }

  const t0 = performance.now();
  function tick(now) {
    const p = Math.min(1, (now - t0) / duration);
    const eased = 1 - Math.pow(1 - p, 3); // ease-out-cubic
    el.textContent = (start + (end - start) * eased).toFixed(decimals) + suffix;
    if (p < 1) {
      requestAnimationFrame(tick);
    } else {
      el.classList.add("flash-update");
      setTimeout(() => el.classList.remove("flash-update"), 500);
    }
  }
  requestAnimationFrame(tick);
}

// Pulses the full-screen vignette — used to punctuate a new Level-2 alert.
function flashCritical() {
  const v = document.getElementById("critVignette");
  if (!v) return;
  v.classList.remove("show");
  void v.offsetWidth; // restart the animation
  v.classList.add("show");
}

function showToast(message, level = "info") {
  const root = document.getElementById("toastRoot");
  if (!root) return;
  const el = document.createElement("div");
  el.className = `toast ${level === "critical" ? "crit" : level === "warning" ? "warn" : ""}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, 4200);
}

async function refreshHeaderStatus() {
  const dot = document.getElementById("globalStatusDot");
  const text = document.getElementById("globalStatusText");
  const cam = document.getElementById("globalCameraId");
  const pulse = document.getElementById("pulseDot");
  if (!dot || !text) return;

  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    if (data.running) {
      dot.className = "status-dot ok";
      text.textContent = "PIPELINE LIVE";
      if (pulse) pulse.classList.add("live");
    } else {
      dot.className = "status-dot";
      text.textContent = "IDLE";
      if (pulse) pulse.classList.remove("live");
    }
    if (cam) cam.textContent = data.camera_id ? `CAM ${data.camera_id}` : "—";
  } catch (e) {
    dot.className = "status-dot err";
    text.textContent = "OFFLINE";
    if (pulse) pulse.classList.remove("live");
  }
}

refreshHeaderStatus();
setInterval(refreshHeaderStatus, 4000);
