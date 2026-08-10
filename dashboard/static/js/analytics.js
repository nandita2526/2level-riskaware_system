// analytics.js — powers the Analytics page.

let timelineChart = null;

function setKpis(data) {
  const cards = document.querySelectorAll("#kpiRow .kpi-card .v");
  animateNumber(cards[0], data.total_alerts_today ?? 0, { decimals: 0 });
  animateNumber(cards[1], data.total_alerts_all_time ?? 0, { decimals: 0 });
  animateNumber(cards[2], data.total_ai_verified_alerts ?? 0, { decimals: 0 });
  animateNumber(cards[3], (data.false_positive_rate ?? 0) * 100, { decimals: 1, suffix: "%" });
  animateNumber(cards[4], data.average_risk ?? 0, { decimals: 2 });
}

function renderTimeline(timeline) {
  const ctx = document.getElementById("timelineChart");
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
  try {
    const res = await fetch("/api/analytics");
    const data = await res.json();
    setKpis(data);
    renderTimeline(data.timeline);
    renderHeatmap(data.heatmap);
  } catch (e) {
    showToast("Could not load analytics", "critical");
  }
}

loadAnalytics();
setInterval(loadAnalytics, 15000);
