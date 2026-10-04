/* video-stage.js — one video component for the whole dashboard.
 *
 *   const stage = VideoStage.mount(el, { kind: "video" });          // clips
 *   const live  = VideoStage.mount(el, { kind: "stream", src: "/video_feed",
 *                                         frameId: "hudFrame", mediaId: "feedImg" });
 *
 * Page-specific markup can be supplied as children of `el` tagged
 * data-vs-slot="frame" (overlay on the screen), "empty" (shown when nothing is
 * loaded) or "viewport" (overlay on the whole bay). The component builds
 * everything else, so the markup exists in exactly one place.
 *
 * Framing: the frame's aspect ratio follows the media's real dimensions
 * (Fit) or a fixed cinematic ratio (Fill). See video-stage.css.
 *
 * Events (CustomEvent on the root): vs:metadata, vs:error, vs:unload, vs:fit.
 */
(function (global) {
  "use strict";

  const ZOOM_STEPS = [1, 1.5, 2, 3, 4];
  const SPEEDS = [0.5, 1, 1.5, 2];
  const RATIO_MIN = 0.4, RATIO_MAX = 3.6;      // beyond this, letterbox inside the frame
  const FILL_RATIO = { landscape: 16 / 9, portrait: 4 / 5, square: 4 / 3 };

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function fmt(t) {
    if (!isFinite(t) || t < 0) t = 0;
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
    const ss = String(s).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
  }

  const svg = (inner) =>
    `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">${inner}</svg>`;
  const stroke = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
  const ICON = {
    play: svg('<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>'),
    pause: svg('<path d="M7 5h3.6v14H7zM13.4 5H17v14h-3.6z" fill="currentColor"/>'),
    stepBack: svg('<path d="M6 5h2v14H6zM20 5.5v13L9.5 12z" fill="currentColor"/>'),
    stepFwd: svg('<path d="M16 5h2v14h-2zM4 5.5v13L14.5 12z" fill="currentColor"/>'),
    volHigh: svg(`<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11" ${stroke}/>`),
    volLow: svg(`<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/><path d="M15.5 9a4 4 0 0 1 0 6" ${stroke}/>`),
    volMuted: svg(`<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/><path d="M16 9.5l5 5M21 9.5l-5 5" ${stroke}/>`),
    fsEnter: svg(`<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" ${stroke}/>`),
    fsExit: svg(`<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" ${stroke}/>`),
  };

  const KNOWN_RATIOS = [
    [1, "1:1"], [4 / 3, "4:3"], [3 / 2, "3:2"], [16 / 10, "16:10"], [16 / 9, "16:9"], [21 / 9, "21:9"],
    [3 / 4, "3:4"], [2 / 3, "2:3"], [10 / 16, "10:16"], [9 / 16, "9:16"], [9 / 21, "9:21"],
  ];

  class VideoStage {
    static mount(root, opts) { return new VideoStage(root, opts); }

    /** "1920×1080" -> "16:9"; unusual shapes fall back to a decimal ratio. */
    static aspectLabel(w, h) {
      if (!w || !h) return "";
      const r = w / h;
      const hit = KNOWN_RATIOS.find(([k]) => Math.abs(r - k) / k < 0.02);
      if (hit) return hit[1];
      return r >= 1 ? `${r.toFixed(2)}:1` : `1:${(1 / r).toFixed(2)}`;
    }

    constructor(root, opts = {}) {
      this.root = root;
      this.opts = opts;
      this.kind = opts.kind === "stream" ? "stream" : "video";
      this.fit = "fit";
      this.mediaRatio = this.kind === "stream" ? 4 / 3 : 16 / 9;
      this.natural = null;
      this.zoom = { z: 1, x: 0, y: 0 };
      this.markers = [];
      this.fps = 30;
      this.current = null;
      this._duration = 0;
      this._cleanups = [];
      this._scrubbing = false;
      this._busy = false;
      this._clickGuard = 0;
      this._wheelAt = 0;

      // adopt page-specific slot content before the root is rebuilt
      const slots = { frame: [], empty: [], viewport: [] };
      Array.from(root.children).forEach((el) => {
        const s = el.getAttribute("data-vs-slot");
        if (s && slots[s]) { slots[s].push(el); root.removeChild(el); }
      });

      root.classList.add("vs");
      root.dataset.kind = this.kind;
      root.dataset.fit = "fit";
      root.dataset.orient = "landscape";
      root.dataset.state = this.kind === "stream" ? "loading" : "empty";
      root.innerHTML = this._template();

      this.r = {};
      root.querySelectorAll("[data-r]").forEach((el) => { this.r[el.dataset.r] = el; });
      slots.frame.forEach((el) => this.r.frame.appendChild(el));
      slots.empty.forEach((el) => this.r.empty.appendChild(el));
      slots.viewport.forEach((el) => this.r.viewport.appendChild(el));
      if (opts.frameId) this.r.frame.id = opts.frameId;
      if (opts.mediaId) this.r.media.id = opts.mediaId;

      this._bindCommon();
      if (this.kind === "video") this._bindVideo();
      this._applyAspect();
      this._applyTransform();
      this._renderState();
      if (this.kind === "stream") this._initStream();
      else { this._syncPlay(); this._syncVolume(); }
    }

    /* ---------------------------------------------------------------- markup */
    _template() {
      const stream = this.kind === "stream";
      const media = stream
        ? `<img class="vs-media" data-r="media" alt="${esc(this.opts.alt || "Live camera feed")}" draggable="false">`
        : `<video class="vs-media" data-r="media" playsinline preload="auto"></video>`;
      const tip = 'data-tip-pos="bottom"';
      const transport = stream ? "" : `
        <div class="vs-transport" data-r="transport">
          <div class="vs-seek-row">
            <span class="vs-time" data-r="cur">0:00</span>
            <div class="vs-seek" data-r="seek">
              <div class="vs-track"><div class="vs-buffered"></div><div class="vs-played"></div></div>
              <div class="vs-markers" data-r="markers"></div>
              <input class="vs-range" data-r="range" type="range" min="0" max="1" step="1" value="0" disabled aria-label="Seek">
              <span class="vs-hover" data-r="hover" aria-hidden="true">0:00</span>
            </div>
            <span class="vs-time end" data-r="dur">0:00</span>
          </div>
          <div class="vs-controls">
            <button type="button" class="ctl ctl-play" data-r="play" aria-label="Play" data-tip="Play (Space)"></button>
            <button type="button" class="ctl ctl-flat" data-r="stepBack" aria-label="Previous frame" data-tip="Previous frame (,)">${ICON.stepBack}</button>
            <button type="button" class="ctl ctl-flat" data-r="stepFwd" aria-label="Next frame" data-tip="Next frame (.)">${ICON.stepFwd}</button>
            <span class="grow"></span>
            <button type="button" class="ctl ctl-flat ctl-speed" data-r="speed" aria-label="Playback speed" data-tip="Playback speed">1×</button>
            <div class="vs-volume">
              <button type="button" class="ctl ctl-flat" data-r="mute" aria-label="Mute" data-tip="Mute (M)"></button>
              <input class="vs-vol" data-r="vol" type="range" min="0" max="1" step="0.05" value="1" aria-label="Volume">
            </div>
          </div>
        </div>`;

      return `
        <div class="vs-viewport" data-r="viewport" tabindex="0" role="group"
             aria-label="${stream ? "Live camera feed" : "Video preview"}">
          <div class="vs-frame" data-r="frame">${media}</div>
          <div class="vs-empty" data-r="empty"></div>
          <div class="vs-state" data-r="state" role="status" aria-live="polite" hidden></div>
          <div class="vs-tools" data-r="tools" role="toolbar" aria-label="View controls">
            <div class="seg" role="group" aria-label="Framing">
              <button type="button" class="seg-btn" data-r="fitBtn" aria-pressed="true" ${tip} data-tip="Show the whole frame (V)">Fit</button>
              <button type="button" class="seg-btn" data-r="fillBtn" aria-pressed="false" ${tip} data-tip="Crop to fill the stage (V)">Fill</button>
            </div>
            <div class="tool-group" role="group" aria-label="Zoom">
              <button type="button" class="tool-btn" data-r="zoomOut" aria-label="Zoom out" ${tip} data-tip="Zoom out (−)">−</button>
              <button type="button" class="tool-btn zoom-val" data-r="zoomVal" aria-label="Reset zoom" ${tip} data-tip="Reset zoom (0)">100%</button>
              <button type="button" class="tool-btn" data-r="zoomIn" aria-label="Zoom in" ${tip} data-tip="Zoom in (+)">+</button>
            </div>
            <button type="button" class="tool-btn" data-r="fsBtn" aria-label="Fullscreen" ${tip} data-tip-align="end" data-tip="Fullscreen (F)">${ICON.fsEnter}</button>
          </div>
        </div>${transport}`;
    }

    _on(el, ev, fn, opt) {
      el.addEventListener(ev, fn, opt);
      this._cleanups.push(() => el.removeEventListener(ev, fn, opt));
    }
    _flag(name, on) {
      if (on) this.root.setAttribute(`data-${name}`, "1");
      else this.root.removeAttribute(`data-${name}`);
    }
    _emit(name, detail) {
      this.root.dispatchEvent(new CustomEvent(`vs:${name}`, { detail }));
    }
    get _ready() { return this.root.dataset.state === "ready"; }

    /* -------------------------------------------------- view: framing, zoom */
    _bindCommon() {
      const r = this.r;
      this._on(r.fitBtn, "click", () => this.setFit("fit"));
      this._on(r.fillBtn, "click", () => this.setFit("fill"));
      this._on(r.zoomIn, "click", () => this.stepZoom(1));
      this._on(r.zoomOut, "click", () => this.stepZoom(-1));
      this._on(r.zoomVal, "click", () => this.resetZoom());
      this._on(r.fsBtn, "click", () => this.toggleFullscreen());
      this._on(document, "fullscreenchange", () => this._syncFs());
      this._on(document, "webkitfullscreenchange", () => this._syncFs());
      this._on(window, "resize", () => this._applyTransform());
      this._on(this.root, "keydown", (e) => this._onKey(e));

      // ctrl/cmd + wheel (and trackpad pinch) zooms; plain wheel still scrolls the page
      this._on(r.viewport, "wheel", (e) => {
        if (!(e.ctrlKey || e.metaKey) || !this._ready) return;
        e.preventDefault();
        const now = performance.now();
        if (now - this._wheelAt < 120) return;
        this._wheelAt = now;
        this.stepZoom(e.deltaY < 0 ? 1 : -1);
      }, { passive: false });

      // drag to pan when zoomed; a click without movement toggles playback
      this._on(r.viewport, "pointerdown", (e) => this._pointerDown(e));
      this._on(r.viewport, "pointermove", (e) => this._pointerMove(e));
      this._on(r.viewport, "pointerup", (e) => this._pointerUp(e, false));
      this._on(r.viewport, "pointercancel", (e) => this._pointerUp(e, true));
      this._on(r.viewport, "click", (e) => this._onClick(e));
      this._on(r.viewport, "dblclick", (e) => { if (e.target.closest(".vs-frame")) this.toggleFullscreen(); });
    }

    setFit(mode) {
      this.fit = mode === "fill" ? "fill" : "fit";
      this.root.dataset.fit = this.fit;
      this.r.fitBtn.setAttribute("aria-pressed", String(this.fit === "fit"));
      this.r.fillBtn.setAttribute("aria-pressed", String(this.fit === "fill"));
      this._applyAspect();
      this._applyTransform();
      this._emit("fit", { mode: this.fit });
    }

    _setMediaSize(w, h) {
      this.natural = { w, h };
      this.mediaRatio = clamp(w / h, RATIO_MIN, RATIO_MAX);
      this._applyAspect();
      this._applyTransform();
    }

    _applyAspect() {
      const ratio = this.mediaRatio;
      const orient = ratio < 0.95 ? "portrait" : ratio > 1.05 ? "landscape" : "square";
      this.root.dataset.orient = orient;
      const ar = this.fit === "fit" ? ratio : FILL_RATIO[orient];
      this.root.style.setProperty("--ar", ar.toFixed(4));
    }

    stepZoom(dir) {
      let i = ZOOM_STEPS.findIndex((s) => Math.abs(s - this.zoom.z) < 0.01);
      if (i < 0) i = 0;
      this.setZoom(ZOOM_STEPS[clamp(i + dir, 0, ZOOM_STEPS.length - 1)]);
    }
    setZoom(z) {
      this.zoom.z = z;
      if (z === 1) { this.zoom.x = 0; this.zoom.y = 0; }
      this._applyTransform();
    }
    resetZoom() { this.setZoom(1); }

    _applyTransform() {
      const { frame, media, zoomVal, zoomIn, zoomOut } = this.r;
      const z = this.zoom.z;
      const maxX = ((z - 1) * frame.clientWidth) / 2;
      const maxY = ((z - 1) * frame.clientHeight) / 2;
      this.zoom.x = clamp(this.zoom.x, -maxX, maxX);
      this.zoom.y = clamp(this.zoom.y, -maxY, maxY);
      media.style.transform = z === 1 ? "" : `translate3d(${this.zoom.x}px,${this.zoom.y}px,0) scale(${z})`;
      this.root.classList.toggle("is-zoomed", z > 1);
      zoomVal.textContent = `${Math.round(z * 100)}%`;
      zoomIn.disabled = z >= ZOOM_STEPS[ZOOM_STEPS.length - 1];
      zoomOut.disabled = z <= 1;
    }

    _pointerDown(e) {
      if (e.button !== 0 || this.zoom.z <= 1 || !this._ready || !e.target.closest(".vs-frame")) return;
      this._pan = { sx: e.clientX, sy: e.clientY, ox: this.zoom.x, oy: this.zoom.y, moved: false };
      try { this.r.viewport.setPointerCapture(e.pointerId); } catch (_) { /* not critical */ }
      this.root.classList.add("is-panning");
    }
    _pointerMove(e) {
      const p = this._pan;
      if (!p) return;
      const dx = e.clientX - p.sx, dy = e.clientY - p.sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) p.moved = true;
      this.zoom.x = p.ox + dx;
      this.zoom.y = p.oy + dy;
      this._applyTransform();
    }
    _pointerUp(e, cancelled) {
      const p = this._pan;
      if (!p) return;
      this._pan = null;
      this.root.classList.remove("is-panning");
      try { this.r.viewport.releasePointerCapture(e.pointerId); } catch (_) { /* not critical */ }
      // pointer capture can retarget the click; guard so it isn't handled twice
      this._clickGuard = performance.now();
      if (!p.moved && !cancelled) this._togglePlayFromClick();
    }
    _onClick(e) {
      if (performance.now() - this._clickGuard < 60) return;
      if (!e.target.closest(".vs-frame")) return;
      this._togglePlayFromClick();
    }
    _togglePlayFromClick() {
      if (this.kind === "video" && this._ready) this.toggle();
    }

    toggleFullscreen() {
      const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
      if (fsEl) {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        return;
      }
      const el = this.root;
      const req = el.requestFullscreen || el.webkitRequestFullscreen;
      if (req) {
        const p = req.call(el);
        if (p && p.catch) p.catch(() => {});
      } else if (this.kind === "video" && this.r.media.webkitEnterFullscreen) {
        this.r.media.webkitEnterFullscreen();   // iPhone Safari only fullscreens the <video> itself
      }
    }
    _syncFs() {
      const fs = (document.fullscreenElement || document.webkitFullscreenElement) === this.root;
      this._flag("fs", fs);
      this.r.fsBtn.innerHTML = fs ? ICON.fsExit : ICON.fsEnter;
      this.r.fsBtn.setAttribute("aria-label", fs ? "Exit fullscreen" : "Fullscreen");
      this._applyTransform();
    }

    _onKey(e) {
      if (e.ctrlKey || e.metaKey || e.altKey || !this._ready) return;
      const t = e.target;
      if (t.matches && t.matches('input[type="text"],input[type="number"],textarea,select')) return;
      const onButton = t.tagName === "BUTTON" || t.tagName === "A";
      const video = this.kind === "video";
      let handled = true;
      switch (e.key) {
        case " ":
          if (!video || onButton) { handled = false; break; }
          this.toggle(); break;
        case "k": case "K": if (!video) { handled = false; break; } this.toggle(); break;
        case "ArrowLeft": if (!video || t === this.r.vol) { handled = false; break; } this.skip(-5); break;
        case "ArrowRight": if (!video || t === this.r.vol) { handled = false; break; } this.skip(5); break;
        case "j": case "J": if (!video) { handled = false; break; } this.skip(-10); break;
        case "l": case "L": if (!video) { handled = false; break; } this.skip(10); break;
        case ",": if (!video) { handled = false; break; } this.step(-1); break;
        case ".": if (!video) { handled = false; break; } this.step(1); break;
        case "m": case "M": if (!video) { handled = false; break; } this.toggleMute(); break;
        case "f": case "F": this.toggleFullscreen(); break;
        case "v": case "V": this.setFit(this.fit === "fit" ? "fill" : "fit"); break;
        case "+": case "=": this.stepZoom(1); break;
        case "-": case "_": this.stepZoom(-1); break;
        case "0": this.resetZoom(); break;
        default: handled = false;
      }
      if (handled) e.preventDefault();
    }

    /* ------------------------------------------------------------ states */
    _setState(state, detail) {
      this.root.dataset.state = state;
      this._stateDetail = detail || {};
      this._renderState();
    }
    _setBusy(b) {
      if (this._busy === b) return;
      this._busy = b;
      this._renderState();
    }
    _renderState() {
      const st = this.r.state;
      const s = this.root.dataset.state;
      const d = this._stateDetail || {};
      st.removeAttribute("data-kind");
      if (s === "loading") {
        st.hidden = false;
        st.innerHTML = `<div class="vs-spinner" aria-hidden="true"></div><p class="vs-state-text">${esc(d.text || "Loading…")}</p>`;
      } else if (s === "error") {
        st.hidden = false;
        let actions = "";
        if (d.retry) actions += '<button type="button" class="btn btn-sm" data-act="retry">Try again</button>';
        if (d.downloadUrl) actions += `<a class="btn btn-sm btn-primary" href="${esc(d.downloadUrl)}" download>Download clip</a>`;
        st.innerHTML = `<div class="vs-error"><p class="vs-state-title">${esc(d.title)}</p>
          <p class="vs-state-text">${esc(d.text)}</p>${actions ? `<div class="vs-state-actions">${actions}</div>` : ""}</div>`;
        const retry = st.querySelector('[data-act="retry"]');
        if (retry) retry.addEventListener("click", () => this._retry());
      } else if (s === "ready" && this._busy) {
        st.hidden = false;
        st.setAttribute("data-kind", "buffer");
        st.innerHTML = '<div class="vs-spinner" aria-hidden="true"></div>';
      } else {
        st.hidden = true;
        st.innerHTML = "";
      }
    }
    _retry() {
      if (this.kind === "video" && this.current) this.load(this.current);
      else if (this.kind === "stream") this.setSource(this.opts.src, true);
    }

    /* ------------------------------------------------------------ stream */
    _initStream() {
      const m = this.r.media;
      this._setState("loading", { text: this.opts.loadingText || "Connecting to camera…" });
      this._on(m, "load", () => this._streamSized());
      this._on(m, "error", () => {
        if (!m.getAttribute("src")) return;
        this._setState("error", {
          title: "Camera feed unavailable",
          text: "The dashboard server didn't send a frame. Check that it's running, then try again.",
          retry: true,
        });
      });
      // MJPEG fires `load` once; poll so a source with a new resolution reframes itself
      const timer = setInterval(() => this._streamSized(), 1000);
      this._cleanups.push(() => clearInterval(timer));
      this.setSource(this.opts.src);
    }
    setSource(src, bust) {
      if (!src) return;
      const url = bust ? `${src}${src.includes("?") ? "&" : "?"}_=${Date.now()}` : src;
      this._setState("loading", { text: this.opts.loadingText || "Connecting to camera…" });
      this.r.media.src = url;
    }
    _streamSized() {
      const m = this.r.media;
      const w = m.naturalWidth, h = m.naturalHeight;
      if (!w || !h) return;
      if (!this._ready) this._setState("ready");
      if (!this.natural || this.natural.w !== w || this.natural.h !== h) {
        this._setMediaSize(w, h);
        this._emit("metadata", this.info());
      }
    }

    /* ------------------------------------------------------------- video */
    _bindVideo() {
      const r = this.r, m = r.media;
      const on = (t, fn) => this._on(m, t, fn);
      on("loadedmetadata", () => this._onMeta());
      on("loadeddata", () => this._setBusy(false));
      on("canplay", () => this._setBusy(false));
      on("waiting", () => { if (this._ready) this._setBusy(true); });
      on("playing", () => { this._setBusy(false); this._syncPlay(); this._startTick(); this._sampleFps(); });
      on("pause", () => this._syncPlay());
      on("ended", () => this._syncPlay());
      on("timeupdate", () => this._updateProgress());
      on("seeked", () => this._updateProgress());
      on("progress", () => this._updateBuffered());
      on("durationchange", () => this._onDuration());
      on("volumechange", () => this._syncVolume());
      on("ratechange", () => { r.speed.textContent = `${m.playbackRate}×`; });
      on("error", () => this._onMediaError());

      this._on(r.play, "click", () => this.toggle());
      this._on(r.stepBack, "click", () => this.step(-1));
      this._on(r.stepFwd, "click", () => this.step(1));
      this._on(r.mute, "click", () => this.toggleMute());
      this._on(r.speed, "click", () => this.cycleSpeed());
      this._on(r.vol, "input", () => {
        m.volume = Number(r.vol.value);
        m.muted = Number(r.vol.value) === 0;
      });

      // scrubbing
      this._on(r.range, "pointerdown", () => { this._scrubbing = true; r.seek.classList.add("is-scrubbing"); });
      this._on(window, "pointerup", () => {
        if (!this._scrubbing) return;
        this._scrubbing = false;
        r.seek.classList.remove("is-scrubbing");
      });
      this._on(r.range, "input", () => {
        const t = Number(r.range.value) / 100;
        this.seek(t);
        this._showHover(t);
      });
      this._on(r.seek, "pointermove", (e) => {
        if (!this._duration) return;
        const rect = r.seek.getBoundingClientRect();
        const thumb = parseFloat(getComputedStyle(this.root).getPropertyValue("--thumb")) || 14;
        const inner = Math.max(1, rect.width - thumb);
        const frac = clamp((e.clientX - rect.left - thumb / 2) / inner, 0, 1);
        this._showHover(frac * this._duration, frac);
      });
    }

    _showHover(t, frac) {
      const { hover, seek } = this.r;
      const d = this._duration;
      if (!d) return;
      const f = frac ?? clamp(t / d, 0, 1);
      const rect = seek.getBoundingClientRect();
      const thumb = parseFloat(getComputedStyle(this.root).getPropertyValue("--thumb")) || 14;
      hover.textContent = fmt(t);
      hover.style.setProperty("--hx", `${thumb / 2 + f * (rect.width - thumb)}px`);
    }

    /** Load a video. `src` may be a blob: URL (instant local preview) or a server URL. */
    load({ src, name, size, poster, downloadUrl, unsupportedHint } = {}) {
      if (this.kind !== "video" || !src) return;
      const m = this.r.media;
      this.current = { src, name: name || "", size, poster, downloadUrl, unsupportedHint };
      this._duration = 0;
      this.natural = null;               // never report the previous clip's dimensions
      this._fpsDone = false;
      this.fps = 30;
      this._busy = false;
      this.resetZoom();
      this.r.range.value = "0";
      this.r.range.disabled = true;
      this.r.cur.textContent = "0:00";
      this.r.dur.textContent = "0:00";
      this.r.seek.style.setProperty("--p", "0%");
      this.r.seek.style.setProperty("--b", "0%");
      this.r.speed.textContent = "1×";
      this._setState("loading", { text: "Reading video…" });
      if (poster) m.setAttribute("poster", poster); else m.removeAttribute("poster");
      m.src = src;
      m.load();
      m.playbackRate = 1;
      this._syncPlay();
      this._renderMarkers();
    }

    unload() {
      if (this.kind !== "video") return;
      const m = this.r.media;
      m.pause();
      m.removeAttribute("src");
      m.removeAttribute("poster");
      m.load();
      this.current = null;
      this._duration = 0;
      this.natural = null;
      this.resetZoom();
      this._setState("empty");
      this._emit("unload", {});
    }

    _onMeta() {
      const m = this.r.media;
      this._duration = isFinite(m.duration) ? m.duration : 0;
      if (m.videoWidth && m.videoHeight) this._setMediaSize(m.videoWidth, m.videoHeight);
      this._onDuration();
      this._busy = true;                  // spinner until the first frame is decoded
      this._setState("ready");
      this._updateProgress();
      this._emit("metadata", this.info());
    }
    _onDuration() {
      const m = this.r.media, r = this.r;
      this._duration = isFinite(m.duration) ? m.duration : 0;
      r.range.max = String(Math.max(1, Math.round(this._duration * 100)));
      r.range.disabled = !this._duration;
      r.dur.textContent = this._duration ? fmt(this._duration) : "—";
      this._renderMarkers();
    }

    info() {
      const n = this.natural || {};
      return {
        duration: this._duration,
        width: n.w || 0,
        height: n.h || 0,
        aspect: VideoStage.aspectLabel(n.w, n.h),
        name: this.current ? this.current.name : "",
        size: this.current ? this.current.size : undefined,
      };
    }

    _onMediaError() {
      const m = this.r.media;
      if (!m.getAttribute("src")) return;           // ignore the event fired by unload()
      const code = m.error ? m.error.code : 0;
      const cur = this.current || {};
      const ext = (cur.name || "").includes(".") ? cur.name.split(".").pop().toLowerCase() : "";
      let detail;
      if (code === 2) {
        detail = { title: "Couldn't load the video", text: "The connection dropped while loading. Check that the server is running, then try again.", retry: true };
      } else {
        let text = `Browsers can't preview every codec or container${ext ? ` (this is .${ext})` : ""}.`;
        if (cur.unsupportedHint) text += ` ${cur.unsupportedHint}`;
        detail = { title: "Can't preview this video here", text };
      }
      detail.downloadUrl = cur.downloadUrl;
      this._busy = false;
      this._setState("error", detail);
      this._emit("error", { code });
    }

    /* playback */
    toggle() {
      const m = this.r.media;
      if (!this._ready) return;
      if (m.paused || m.ended) { const p = m.play(); if (p && p.catch) p.catch(() => {}); }
      else m.pause();
    }
    play() { const p = this.r.media.play(); if (p && p.catch) p.catch(() => {}); }
    pause() { this.r.media.pause(); }
    seek(t) {
      const m = this.r.media;
      if (!this._duration) return;
      m.currentTime = clamp(t, 0, this._duration);
      this._updateProgress();
    }
    skip(sec) { this.seek(this.r.media.currentTime + sec); }
    step(dir) {
      const m = this.r.media;
      if (!this._duration) return;
      m.pause();
      // aim at the middle of the target frame so the seek lands on it, not on its edge
      const n = Math.floor(m.currentTime * this.fps + 1e-6);
      this.seek((n + dir + 0.5) / this.fps);
    }
    toggleMute() {
      const m = this.r.media;
      m.muted = !m.muted;
      if (!m.muted && m.volume === 0) m.volume = 0.5;
    }
    cycleSpeed() {
      const m = this.r.media;
      const i = SPEEDS.indexOf(m.playbackRate);
      m.playbackRate = SPEEDS[(i + 1) % SPEEDS.length];
    }

    _syncPlay() {
      const m = this.r.media, b = this.r.play;
      const paused = m.paused || m.ended;
      b.innerHTML = paused ? ICON.play : ICON.pause;
      b.setAttribute("aria-label", paused ? "Play" : "Pause");
      b.dataset.tip = paused ? "Play (Space)" : "Pause (Space)";
      this._flag("paused", paused);
    }
    _syncVolume() {
      const m = this.r.media, r = this.r;
      const level = m.muted ? 0 : m.volume;
      r.mute.innerHTML = level === 0 ? ICON.volMuted : level < 0.5 ? ICON.volLow : ICON.volHigh;
      r.mute.setAttribute("aria-label", level === 0 ? "Unmute" : "Mute");
      r.mute.dataset.tip = level === 0 ? "Unmute (M)" : "Mute (M)";
      r.vol.value = String(level);
      r.vol.style.setProperty("--v", `${level * 100}%`);
    }
    _startTick() {
      if (this._raf) return;
      const m = this.r.media;
      const loop = () => {
        this._updateProgress();
        this._raf = !m.paused && !m.ended ? requestAnimationFrame(loop) : 0;
      };
      this._raf = requestAnimationFrame(loop);
    }
    _updateProgress() {
      const m = this.r.media, r = this.r;
      const t = m.currentTime || 0, d = this._duration;
      r.cur.textContent = fmt(t);
      if (!d) return;
      r.seek.style.setProperty("--p", `${clamp(t / d, 0, 1) * 100}%`);
      if (!this._scrubbing) r.range.value = String(Math.round(t * 100));
      r.range.setAttribute("aria-valuetext", `${fmt(t)} of ${fmt(d)}`);
    }
    _updateBuffered() {
      const m = this.r.media, d = this._duration;
      if (!d) return;
      const t = m.currentTime, b = m.buffered;
      let end = 0;
      for (let i = 0; i < b.length; i++) {
        if (b.start(i) <= t + 0.5 && b.end(i) >= t) { end = b.end(i); break; }
      }
      this.r.seek.style.setProperty("--b", `${clamp(end / d, 0, 1) * 100}%`);
    }

    /** Browsers don't expose frame rate; estimate it from presentation timestamps. */
    _sampleFps() {
      const m = this.r.media;
      if (this._fpsDone || !m.requestVideoFrameCallback) return;
      const deltas = [];
      let last = null;
      const cb = (_now, meta) => {
        if (last !== null) {
          const d = meta.mediaTime - last;
          if (d > 0.004 && d < 0.25) deltas.push(d);
        }
        last = meta.mediaTime;
        if (deltas.length < 16 && !m.paused) { m.requestVideoFrameCallback(cb); return; }
        if (deltas.length >= 8) {
          deltas.sort((a, b) => a - b);
          const fps = 1 / deltas[deltas.length >> 1];
          if (fps >= 5 && fps <= 120) { this.fps = Math.round(fps * 100) / 100; this._fpsDone = true; }
        }
      };
      m.requestVideoFrameCallback(cb);
    }

    /** markers: [{ time: seconds, level: 1 | 2 }] drawn as ticks on the seek bar */
    setMarkers(list) {
      this.markers = (list || [])
        .filter((x) => x && isFinite(Number(x.time)) && Number(x.time) >= 0)
        .map((x) => ({ time: Number(x.time), level: Number(x.level) === 2 ? 2 : 1 }));
      this._renderMarkers();
    }
    _renderMarkers() {
      if (!this.r.markers) return;
      const d = this._duration;
      this.r.markers.innerHTML = d
        ? this.markers.map((mk) =>
            `<span class="vs-marker l${mk.level}" style="--m:${(clamp(mk.time / d, 0, 1) * 100).toFixed(3)}"></span>`).join("")
        : "";
    }

    destroy() {
      this._cleanups.forEach((fn) => fn());
      this._cleanups = [];
      if (this._raf) cancelAnimationFrame(this._raf);
      if (this.kind === "video") this.unload();
    }
  }

  global.VideoStage = VideoStage;
})(window);
