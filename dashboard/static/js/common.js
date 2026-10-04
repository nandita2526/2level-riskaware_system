// common.js — shared header status, toast, and animation helpers, loaded on every page.

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Escapes text before it is interpolated into innerHTML (file names, alert
// text and CSV fields all originate outside this page).
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 83.4 -> "1:23", 3725 -> "1:02:05"
function fmtTime(seconds) {
  let t = Number(seconds);
  if (!isFinite(t) || t < 0) t = 0;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function fmtBytes(n) {
  n = Number(n);
  if (!isFinite(n) || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / Math.pow(1024, i);
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

// Reads a design token (e.g. "--accent") so canvas/Chart.js drawing stays in
// sync with style.css instead of hard-coding colours in JS.
function cssVar(name, fallback = "") {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// "#6FA3EF", 0.4 -> "rgba(111,163,239,0.4)"
function withAlpha(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
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
  el.setAttribute("role", level === "critical" ? "alert" : "status");
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
      text.textContent = "Pipeline live";
      if (pulse) pulse.classList.add("live");
    } else {
      dot.className = "status-dot";
      text.textContent = "Idle";
      if (pulse) pulse.classList.remove("live");
    }
    if (cam) cam.textContent = data.camera_id || "";
  } catch (e) {
    dot.className = "status-dot err";
    text.textContent = "Offline";
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
    btn.setAttribute("aria-pressed", String(enabled));
    const text = document.getElementById("soundToggleText");
    if (text) text.textContent = enabled ? "Sound on" : "Sound off";
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
