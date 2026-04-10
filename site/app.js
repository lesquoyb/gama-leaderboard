const LABELS = {
  global_score: "Global score",
  commits: "Commits",
  java_lines: "Java lines",
  gaml_lines: "GAML lines",
  wiki_lines: "Wiki lines",
  issues_opened: "Issues opened",
  issues_closed: "Issues closed",
  prs_opened: "PRs opened",
  prs_merged: "PRs merged",
};

const METRICS = [
  "commits",
  "java_lines",
  "gaml_lines",
  "wiki_lines",
  "issues_opened",
  "issues_closed",
  "prs_opened",
  "prs_merged",
];

let DATA = null;
let currentMetric = "global_score";
let chart = null;

// ---------- loading ----------

async function load() {
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    DATA = await res.json();
  } catch (e) {
    document.getElementById("meta").textContent =
      "⚠ Could not load data.json — run the builder (see README).";
    console.error(e);
    return;
  }
  initFilters();
  renderMeta();
  render();
}

// ---------- filters ----------

function initFilters() {
  const months = DATA.months || [];
  const from = document.getElementById("fromMonth");
  const to = document.getElementById("toMonth");
  if (months.length) {
    from.min = to.min = months[0];
    from.max = to.max = months[months.length - 1];
    from.value = months[0];
    to.value = months[months.length - 1];
  }
  from.addEventListener("change", render);
  to.addEventListener("change", render);
  document.getElementById("resetRange").addEventListener("click", () => {
    if (months.length) {
      from.value = months[0];
      to.value = months[months.length - 1];
    }
    render();
  });
}

function selectedRange() {
  return {
    from: document.getElementById("fromMonth").value || "0000-00",
    to: document.getElementById("toMonth").value || "9999-99",
  };
}

// ---------- aggregation ----------

function aggregate(user, from, to) {
  const totals = Object.fromEntries(METRICS.map((m) => [m, 0]));
  const tl = user.timeline || {};
  for (const [month, vals] of Object.entries(tl)) {
    if (month < from || month > to) continue;
    for (const m of METRICS) totals[m] += vals[m] || 0;
  }
  return totals;
}

function computedUsers() {
  const { from, to } = selectedRange();
  const rows = DATA.users.map((u) => ({
    login: u.login,
    avatar_url: u.avatar_url,
    html_url: u.html_url,
    timeline: u.timeline || {},
    ...aggregate(u, from, to),
  }));
  const maxes = Object.fromEntries(
    METRICS.map((m) => [m, Math.max(0, ...rows.map((r) => r[m]))])
  );
  for (const r of rows) {
    const parts = METRICS.map((m) => (maxes[m] > 0 ? r[m] / maxes[m] : 0));
    r.global_score = parts.reduce((a, b) => a + b, 0) / parts.length;
  }
  return rows;
}

// ---------- rendering ----------

function renderMeta() {
  const { generated_at, config } = DATA;
  const when = new Date(generated_at).toLocaleString();
  const scope = config.org
    ? `org ${config.org} · ${config.repos.length} repos`
    : `${config.repos.length} repos`;
  document.getElementById("meta").textContent =
    `${scope} · last built ${when}`;
}

function formatValue(metric, v) {
  if (metric === "global_score") return (v * 100).toFixed(1);
  return (v || 0).toLocaleString();
}
function valueSuffix(metric) {
  return metric === "global_score" ? " / 100" : "";
}

function renderHeroStats(rows) {
  const totals = METRICS.reduce((acc, m) => {
    acc[m] = rows.reduce((s, r) => s + (r[m] || 0), 0);
    return acc;
  }, {});
  const el = document.getElementById("hero-stats");
  el.innerHTML = `
    <div class="hero-stat">
      <span class="val">${rows.length.toLocaleString()}</span>
      <span class="lbl">Contributors</span>
    </div>
    <div class="hero-stat">
      <span class="val">${totals.commits.toLocaleString()}</span>
      <span class="lbl">Commits</span>
    </div>
    <div class="hero-stat">
      <span class="val">${(totals.prs_merged).toLocaleString()}</span>
      <span class="lbl">PRs merged</span>
    </div>
  `;
}

function renderPodium(rows) {
  const top = rows.slice(0, 3);
  const el = document.getElementById("podium");
  el.innerHTML = top
    .map((u, i) => {
      const medal = ["🥇 1st", "🥈 2nd", "🥉 3rd"][i];
      const v = formatValue(currentMetric, u[currentMetric]) + valueSuffix(currentMetric);
      return `
        <div class="podium-card p${i + 1}">
          <div class="podium-medal">${medal}</div>
          ${u.avatar_url ? `<img src="${u.avatar_url}" alt="" />` : ""}
          <div class="podium-name">
            <a href="${u.html_url || "#"}" target="_blank" rel="noopener">@${u.login}</a>
          </div>
          <div class="podium-value">${v}</div>
          <span class="podium-metric">${LABELS[currentMetric]}</span>
        </div>`;
    })
    .join("");
}

function render() {
  const rows = computedUsers()
    .filter((u) => (currentMetric === "global_score" ? u.global_score > 0 : u[currentMetric] > 0))
    .sort((a, b) => (b[currentMetric] || 0) - (a[currentMetric] || 0));

  renderHeroStats(rows);
  renderPodium(rows);

  const tbody = document.querySelector("#board tbody");
  tbody.innerHTML = "";
  const max = Math.max(0, ...rows.map((r) => r[currentMetric] || 0));
  rows.forEach((u, i) => {
    const tr = document.createElement("tr");
    if (i < 3) tr.classList.add(`top-${i + 1}`);
    const raw = u[currentMetric] || 0;
    const value = formatValue(currentMetric, raw) + valueSuffix(currentMetric);
    const barPct = max > 0 ? (raw / max) * 100 : 0;
    tr.innerHTML = `
      <td class="rank">#${i + 1}</td>
      <td class="user">
        ${u.avatar_url ? `<img src="${u.avatar_url}" alt="" />` : ""}
        <a href="${u.html_url || "#"}" target="_blank" rel="noopener">@${u.login}</a>
      </td>
      <td class="col-bar">
        <div class="bar-track"><div class="bar-fill" style="width:${barPct}%"></div></div>
      </td>
      <td class="col-val">${value}</td>
    `;
    tbody.appendChild(tr);
  });

  document.getElementById("valueHeader").textContent = LABELS[currentMetric];
  document.getElementById("boardSub").textContent =
    `${rows.length} contributor${rows.length === 1 ? "" : "s"}`;
  document.getElementById("chartSub").textContent =
    currentMetric === "global_score" ? "activity proxy" : `${LABELS[currentMetric]} · cumulative`;

  renderChart(rows.slice(0, 5));
}

// ---------- chart ----------

function monthsInRange() {
  const { from, to } = selectedRange();
  return (DATA.months || []).filter((m) => m >= from && m <= to);
}

function cumulativeSeries(user, metric, months) {
  const tl = user.timeline || {};
  let acc = 0;
  return months.map((m) => {
    if (metric === "global_score") {
      const vals = tl[m] || {};
      acc += METRICS.reduce((s, k) => s + (vals[k] || 0), 0);
    } else {
      acc += (tl[m] || {})[metric] || 0;
    }
    return acc;
  });
}

const CHART_COLORS = [
  { border: "#7c5cff", bg: "rgba(124, 92, 255, 0.12)" },
  { border: "#22d3ee", bg: "rgba(34, 211, 238, 0.12)" },
  { border: "#f472b6", bg: "rgba(244, 114, 182, 0.12)" },
  { border: "#34d399", bg: "rgba(52, 211, 153, 0.12)" },
  { border: "#fbbf24", bg: "rgba(251, 191, 36, 0.12)" },
];

function renderChart(topUsers) {
  const months = monthsInRange();
  const datasets = topUsers.map((u, i) => {
    const c = CHART_COLORS[i % CHART_COLORS.length];
    return {
      label: u.login,
      data: cumulativeSeries(u, currentMetric, months),
      borderColor: c.border,
      backgroundColor: c.bg,
      fill: true,
      tension: 0.35,
      pointRadius: 0,
      pointHoverRadius: 5,
      borderWidth: 2.5,
    };
  });
  const cfg = {
    type: "line",
    data: { labels: months, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#f1f3f9",
            font: { family: "Inter", size: 12, weight: "500" },
            usePointStyle: true,
            pointStyle: "circle",
            padding: 16,
          },
        },
        tooltip: {
          backgroundColor: "rgba(13, 16, 32, 0.95)",
          titleColor: "#f1f3f9",
          bodyColor: "#f1f3f9",
          borderColor: "rgba(255,255,255,0.1)",
          borderWidth: 1,
          padding: 12,
          cornerRadius: 10,
          titleFont: { family: "Inter", weight: "600" },
          bodyFont: { family: "Inter" },
        },
      },
      scales: {
        x: {
          ticks: { color: "#8a93ab", maxRotation: 0, autoSkip: true, font: { family: "Inter" } },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
        y: {
          ticks: { color: "#8a93ab", font: { family: "Inter" } },
          grid: { color: "rgba(255,255,255,0.04)" },
          beginAtZero: true,
        },
      },
    },
  };
  if (chart) chart.destroy();
  const ctx = document.getElementById("chart").getContext("2d");
  chart = new Chart(ctx, cfg);
}

// ---------- tabs ----------

document.querySelectorAll("#tabs .chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs .chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentMetric = btn.dataset.metric;
    if (DATA) render();
  });
});

load();
