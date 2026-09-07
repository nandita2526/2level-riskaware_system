// analytics.js — powers the Analytics page.

let timelineChart = null;
let selectedCameraId = new URLSearchParams(window.location.search).get("camera_id") || "";

async function populateCameraFilter() {
  const select = document.getElementById("cameraFilter");
  if (!select) return;
  try {
    const res = await fetch("/api/cameras");
    const data = await res.json();
    if (!data.ok) return;
    (data.camera_ids || []).forEach(id => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      select.appendChild(opt);
    });
    if (selectedCameraId) {
      select.value = selectedCameraId;
      // if the URL asked for a camera_id that no longer exists, fall back to "All"
      if (select.value !== selectedCameraId) selectedCameraId = "";
    }
  } catch (e) {
    // dropdown just stays as "All cameras" — /api/analytics still works unfiltered
  }
}

document.getElementById("cameraFilter")?.addEventListener("change", (e) => {
  selectedCameraId = e.target.value;
  const url = new URL(window.location);
  if (selectedCameraId) url.searchParams.set("camera_id", selectedCameraId);
  else url.searchParams.delete("camera_id");
  window.history.replaceState({}, "", url);
  loadAnalytics();
});

function setKpis(data) {
  const cards = document.querySelectorAll("#kpiRow .kpi-card .v");
  animateNumber(cards[0], data.total_alerts_today ?? 0, { decimals: 0 });
  animateNumber(cards[1], data.total_alerts_all_time ?? 0, { decimals: 0 });
  animateNumber(cards[2], data.total_ai_verified_alerts ?? 0, { decimals: 0 });
  animateNumber(cards[3], (data.false_positive_rate ?? 0) * 100, { decimals: 1, suffix: "%" });
  animateNumber(cards[4], data.average_risk ?? 0, { decimals: 2 });

  const hint = document.getElementById("cameraFilterHint");
  if (hint) {
    hint.textContent = data.camera_id
      ? `Showing only "${data.camera_id}"`
      : "Showing all cameras & uploaded videos combined";
  }
}

function renderTimeline(timeline) {
  const ctx = document.getElementById("timelineChart");
  if (!ctx) return;

  if (typeof Chart === "undefined") {
    // Chart.js failed to load — show a plain message instead of throwing
    // and taking down the rest of the page with it.
    const body = ctx.closest(".chart-body");
    if (body) body.innerHTML = `<div class="empty-row">Chart library unavailable.</div>`;
    return;
  }

  const labels = (timeline || []).map(p => p.hour);
  const counts = (timeline || []).map(p => p.count);

  if (timelineChart) { timelineChart.destroy(); }
  timelineChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Alerts",
        data: counts,
        backgroundColor: "rgba(56,189,248,.55)",
        hoverBackgroundColor: "#38BDF8",
        borderRadius: 3,
        maxBarThickness: 18,
      }],
    },
    options: {
      responsive: true,
      animation: { duration: 500, easing: "easeOutQuart" },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: "#7C8896", font: { family: "JetBrains Mono", size: 10 } },
        },
        y: {
          beginAtZero: true,
          grid: { color: "#232B34" },
          ticks: { color: "#7C8896", font: { family: "JetBrains Mono", size: 10 }, precision: 0 },
        },
      },
    },
  });
}

function heatColor(count, max) {
  if (!count) return "var(--panel-inset)";
  const t = Math.min(1, count / Math.max(1, max));
  // interpolate from dim cyan to hot red as intensity rises
  const r = Math.round(56 + t * (248 - 56));
  const g = Math.round(189 - t * (189 - 113));
  const b = Math.round(248 - t * (248 - 113));
  return `rgb(${r},${g},${b})`;
}

function renderHeatmap(heatmap) {
  const wrap = document.getElementById("heatmapWrap");
  if (!heatmap || !heatmap.length) {
    wrap.innerHTML = `<div class="empty-row">No incident data yet.</div>`;
    return;
  }

  const maxCount = Math.max(1, ...heatmap.flatMap(row => row.hours.map(h => h.count)));
  const hourLabels = heatmap[0].hours.map(h => h.hour);

  let html = `<table class="heatmap-table"><thead><tr><td></td>`;
  hourLabels.forEach((h, i) => {
    html += `<td class="heatmap-row-label" style="text-align:center;">${i % 3 === 0 ? h : ""}</td>`;
  });
  html += `</tr></thead><tbody>`;

  let cellIndex = 0;
  heatmap.forEach(row => {
    html += `<tr><td class="heatmap-row-label">${row.camera_id}</td>`;
    row.hours.forEach(h => {
      const delay = Math.min(cellIndex * 3, 600);
      html += `<td>
        <div class="heatmap-cell" title="${row.camera_id} · ${h.hour}:00 · ${h.count} alerts"
             style="background:${heatColor(h.count, maxCount)};animation-delay:${delay}ms"></div>
      </td>`;
      cellIndex++;
    });
    html += `</tr>`;
  });
  html += `</tbody></table>`;
  wrap.innerHTML = html;
}

async function loadAnalytics() {
  let data;
  try {
    const url = selectedCameraId
      ? `/api/analytics?camera_id=${encodeURIComponent(selectedCameraId)}`
      : "/api/analytics";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    if (!data.ok) throw new Error(data.error || "analytics API returned ok:false");
  } catch (e) {
    // Only the actual data fetch failing should surface this toast.
    showToast("Could not load analytics", "critical");
    return;
  }

  // Data loaded fine — render each piece independently so a rendering bug
  // in one (e.g. the chart library) can't blank out the others.
  try { setKpis(data); } catch (e) { console.error("setKpis failed:", e); }
  try { renderTimeline(data.timeline); } catch (e) { console.error("renderTimeline failed:", e); }
  try { renderHeatmap(data.heatmap); } catch (e) { console.error("renderHeatmap failed:", e); }
}

populateCameraFilter().then(loadAnalytics);
setInterval(loadAnalytics, 15000);
