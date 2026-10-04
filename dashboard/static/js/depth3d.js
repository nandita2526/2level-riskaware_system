// depth3d.js — animated 3D scene backdrop + tilt/glare on cards.
// Pure presentation: touches no app state, no API calls, no IDs the app uses.
(function () {
  "use strict";
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var canTilt = window.matchMedia && matchMedia("(hover:hover) and (pointer:fine)").matches && !reduce;
  var root = document.documentElement;
  var mx = 0, my = 0;               // normalised pointer, -1..1

  /* ---------- 1. Scene: perspective floor grid + floating depth dust ---------- */
  var cv = document.getElementById("scene");
  if (cv && cv.getContext && !reduce) {
    var ctx = cv.getContext("2d"), W, H, dpr, tx = 0, ty = 0, t = 0, running = true;
    var rgb = "111,163,239";
    var dust = [];
    for (var i = 0; i < 48; i++) dust.push({ x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random(), v: 0.00008 + Math.random() * 0.00022 });

    var size = function () {
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      W = innerWidth; H = innerHeight;
      cv.width = W * dpr; cv.height = H * dpr;
      cv.style.width = W + "px"; cv.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    var frame = function () {
      if (!running) return;
      tx += (mx - tx) * 0.05; ty += (my - ty) * 0.05;
      t += 0.0035;
      ctx.clearRect(0, 0, W, H);

      var hy = H * (0.60 + ty * 0.025), vx = W * (0.5 + tx * 0.07), floor = H - hy;
      // receding lines
      ctx.lineWidth = 1;
      for (var i = -18; i <= 18; i++) {
        var a = 0.16 - Math.abs(i) * 0.006;
        if (a <= 0) continue;
        var g = ctx.createLinearGradient(0, hy, 0, H);
        g.addColorStop(0, "rgba(" + rgb + ",0)"); g.addColorStop(1, "rgba(" + rgb + "," + a + ")");
        ctx.strokeStyle = g;
        ctx.beginPath(); ctx.moveTo(vx + i * 8, hy); ctx.lineTo(vx + i * W * 0.12, H); ctx.stroke();
      }
      // horizontal lines rushing toward the viewer
      var N = 14;
      for (var k = 0; k < N; k++) {
        var z = ((k + t * 6) % N) / N, y = hy + floor * z * z;
        ctx.strokeStyle = "rgba(" + rgb + "," + (0.02 + z * 0.15) + ")";
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }
      // horizon glow
      var hg = ctx.createLinearGradient(0, hy - 60, 0, hy + 30);
      hg.addColorStop(0, "rgba(" + rgb + ",0)"); hg.addColorStop(0.7, "rgba(" + rgb + ",.10)"); hg.addColorStop(1, "rgba(" + rgb + ",0)");
      ctx.fillStyle = hg; ctx.fillRect(0, hy - 60, W, 90);

      // dust with parallax by depth
      for (var d = 0; d < dust.length; d++) {
        var p = dust[d];
        p.y -= p.v * (0.4 + p.z); if (p.y < -1.1) { p.y = 1.1; p.x = Math.random() * 2 - 1; }
        var px = W * (0.5 + p.x * 0.55) + tx * 60 * p.z, py = H * (0.5 + p.y * 0.5) + ty * 40 * p.z;
        ctx.fillStyle = "rgba(" + rgb + "," + (0.10 + p.z * 0.35) + ")";
        ctx.beginPath(); ctx.arc(px, py, 0.6 + p.z * 1.8, 0, 6.2832); ctx.fill();
      }
      requestAnimationFrame(frame);
    };
    size(); addEventListener("resize", size);
    document.addEventListener("visibilitychange", function () {
      running = !document.hidden; if (running) requestAnimationFrame(frame);
    });
    requestAnimationFrame(frame);
  }

  /* ---------- 2. Pointer: parallax vars + card tilt (event-delegated) ---------- */
  var SEL = ".kpi-card,.tier-card,.incident-card,.panel,.job-select";
  var cur = null, lastEv = null, queued = false;

  function release(el) {
    if (!el) return;
    el.classList.remove("is-tilting");
    el.style.transform = "";
  }
  function apply() {
    queued = false;
    if (!cur || !lastEv) return;
    var r = cur.getBoundingClientRect();
    var px = (lastEv.clientX - r.left) / r.width, py = (lastEv.clientY - r.top) / r.height;
    var max = cur.classList.contains("panel") ? 2.2 : cur.classList.contains("tier-card") ? 10 : 8;
    var rx = (0.5 - py) * max * 2, ry = (px - 0.5) * max * 2;
    var s = cur.classList.contains("panel") ? 1.005 : 1.03;
    cur.style.transform = "perspective(900px) rotateX(" + rx.toFixed(2) + "deg) rotateY(" + ry.toFixed(2) + "deg) scale(" + s + ")";
    cur.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
    cur.style.setProperty("--my", (py * 100).toFixed(1) + "%");
  }

  document.addEventListener("pointermove", function (e) {
    mx = (e.clientX / innerWidth) * 2 - 1;
    my = (e.clientY / innerHeight) * 2 - 1;
    root.style.setProperty("--px", mx.toFixed(3));
    root.style.setProperty("--py", my.toFixed(3));
    if (!canTilt || e.pointerType === "touch") return;

    var el = e.target.closest ? e.target.closest(SEL) : null;
    if (cur && cur !== el) { release(cur); cur = null; }
    if (!el) return;
    if (el !== cur) {
      cur = el;
      if (!el.querySelector(":scope > .glare")) {
        var g = document.createElement("i");
        g.className = "glare"; g.setAttribute("aria-hidden", "true");
        el.appendChild(g);
      }
      el.classList.add("is-tilting");
    }
    lastEv = e;
    if (!queued) { queued = true; requestAnimationFrame(apply); }
  }, { passive: true });

  document.addEventListener("pointerleave", function () { release(cur); cur = null; });
  window.addEventListener("blur", function () { release(cur); cur = null; });
})();
