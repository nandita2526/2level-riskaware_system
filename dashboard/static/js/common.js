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

// ==========================================================================
// Client-side alert audio
// ==========================================================================
// Alerts are heard in whichever browser tab the operator is actually
// watching, generated purely with the Web Audio API (no external sound
// files to host, and it works identically on any OS/browser/device — unlike
// relying on the server machine's own speakers via a server-side TTS
// engine, which either isn't available at all in many hosting environments,
// or plays somewhere the operator isn't standing).
//
// Browsers block audio from starting on its own (autoplay policy) until the
// user has interacted with the page at least once, so this starts muted and
// the header button both (a) toggles on/off and (b) is itself the required
// user gesture that unlocks the AudioContext.
const AlertAudio = (() => {
  const STORAGE_KEY = "riskwatch_sound_enabled";
  let ctx = null;
  let enabled = false;
  try {
    enabled = localStorage.getItem(STORAGE_KEY) === "1";
  } catch (e) { /* localStorage unavailable (e.g. privacy mode) — default off */ }

  function ensureContext() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function tone(freq, startAt, duration, gainPeak = 0.18, type = "sine") {
    const audioCtx = ensureContext();
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, startAt);
    gain.gain.linearRampToValueAtTime(gainPeak, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(startAt);
    osc.stop(startAt + duration + 0.02);
  }

  function playWarning() {
    if (!enabled) return;
    const audioCtx = ensureContext();
    if (!audioCtx) return;
    // single short, soft beep — matches Level-1's "quiet nudge" role
    tone(740, audioCtx.currentTime, 0.16, 0.15, "sine");
  }

  function playCritical() {
    if (!enabled) return;
    const audioCtx = ensureContext();
    if (!audioCtx) return;
    // urgent alternating two-tone siren, three pulses — matches Level-2's
    // "confirmed, needs a human now" severity
    const now = audioCtx.currentTime;
    for (let i = 0; i < 3; i++) {
      tone(880, now + i * 0.34, 0.16, 0.22, "square");
      tone(660, now + i * 0.34 + 0.17, 0.16, 0.22, "square");
    }
  }

  function setEnabled(next) {
    enabled = next;
    try { localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0"); } catch (e) { /* ignore */ }
    ensureContext(); // the toggle click is a user gesture — unlock audio now
    updateButton();
  }

  function updateButton() {
    const btn = document.getElementById("soundToggleBtn");
    if (!btn) return;
    btn.classList.toggle("on", enabled);
    const icon = document.getElementById("soundToggleIcon");
    const text = document.getElementById("soundToggleText");
    if (icon) icon.textContent = enabled ? "🔊" : "🔈";
    if (text) text.textContent = enabled ? "Sound: On" : "Sound: Off";
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateButton();
    const btn = document.getElementById("soundToggleBtn");
    btn?.addEventListener("click", () => setEnabled(!enabled));
  });

  return { playWarning, playCritical, isEnabled: () => enabled };
})();

// Called by live.js when it detects a new Level-1 (1) or Level-2 (2) alert.
function playAlertTone(level) {
  if (level === 2) AlertAudio.playCritical();
  else AlertAudio.playWarning();
}
