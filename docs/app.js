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

const MIN_WEEK = "0000-00-00";
const MAX_WEEK = "9999-99-99";

let DATA = null;
let currentMetric = "global_score";
let currentRepo = ""; // "" = all
let chart = null;
let userChart = null;

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
  initRepoFilter();
  renderMeta();
  render();
}

// ---------- week helpers ----------

function mondayOf(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00Z");
  if (isNaN(d)) return null;
  const dow = d.getUTCDay() || 7; // Sun=0 -> 7
  d.setUTCDate(d.getUTCDate() - (dow - 1));
  return d.toISOString().slice(0, 10);
}

function addWeeks(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n * 7);
  return d.toISOString().slice(0, 10);
}

// ---------- filters ----------

function initFilters() {
  const weeks = DATA.weeks || [];
  const from = document.getElementById("fromWeek");
  const to = document.getElementById("toWeek");
  if (weeks.length) {
    from.min = to.min = weeks[0];
    from.max = to.max = weeks[weeks.length - 1];
    from.value = weeks[0];
    to.value = weeks[weeks.length - 1];
  }
  from.addEventListener("change", () => {
    from.value = mondayOf(from.value) || from.value;
    clearPreset();
    render();
  });
  to.addEventListener("change", () => {
    to.value = mondayOf(to.value) || to.value;
    clearPreset();
    render();
  });

  document.querySelectorAll("#presets button").forEach((b) => {
    b.addEventListener("click", () => applyPreset(b.dataset.preset, b));
  });
}

function initRepoFilter() {
  const sel = document.getElementById("repoFilter");
  (DATA.repos || []).forEach((r) => {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => {
    currentRepo = sel.value;
    render();
  });
}

function applyPreset(preset, btn) {
  const weeks = DATA.weeks || [];
  if (!weeks.length) return;
  const last = weeks[weeks.length - 1];
  const first = weeks[0];
  let from = first;
  if (preset !== "all") {
    const n = parseInt(preset, 10);
    from = addWeeks(last, -(n - 1));
    if (from < first) from = first;
  }
  document.getElementById("fromWeek").value = from;
  document.getElementById("toWeek").value = last;
  document.querySelectorAll("#presets button").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  render();
}

function clearPreset() {
  document.querySelectorAll("#presets button").forEach((b) => b.classList.remove("active"));
}

function selectedRange() {
  return {
    from: document.getElementById("fromWeek").value || MIN_WEEK,
    to: document.getElementById("toWeek").value || MAX_WEEK,
  };
}

// ---------- aggregation ----------

function aggregate(user, from, to, repo) {
  const totals = Object.fromEntries(METRICS.map((m) => [m, 0]));
  if (repo) {
    // Per-repo totals are all-time (we don't store per-repo per-week).
    const pr = (user.per_repo || {})[repo];
    if (pr) for (const m of METRICS) totals[m] = pr[m] || 0;
    return totals;
  }
  const tl = user.timeline || {};
  for (const [week, vals] of Object.entries(tl)) {
    if (week < from || week > to) continue;
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
    per_repo: u.per_repo || {},
    ...aggregate(u, from, to, currentRepo),
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
  const wikis = (config.wiki_repos || []).length
    ? ` + ${config.wiki_repos.length} wiki`
    : "";
  document.getElementById("meta").textContent =
    `${scope}${wikis} · last built ${when}`;
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
      <span class="val">${totals.prs_merged.toLocaleString()}</span>
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
        <div class="podium-card p${i + 1}" data-login="${escapeAttr(u.login)}">
          <div class="podium-medal">${medal}</div>
          ${u.avatar_url ? `<img src="${u.avatar_url}" alt="" />` : ""}
          <div class="podium-name">@${escapeHtml(u.login)}</div>
          <div class="podium-value">${v}</div>
          <span class="podium-metric">${LABELS[currentMetric]}</span>
        </div>`;
    })
    .join("");
  el.querySelectorAll(".podium-card").forEach((c) => {
    c.addEventListener("click", () => openProfile(c.dataset.login));
  });
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
    tr.dataset.login = u.login;
    const raw = u[currentMetric] || 0;
    const value = formatValue(currentMetric, raw) + valueSuffix(currentMetric);
    const barPct = max > 0 ? (raw / max) * 100 : 0;
    tr.innerHTML = `
      <td class="rank">#${i + 1}</td>
      <td class="user">
        ${u.avatar_url ? `<img src="${u.avatar_url}" alt="" />` : ""}
        <span class="user-login">@${escapeHtml(u.login)}</span>
      </td>
      <td class="col-bar">
        <div class="bar-track"><div class="bar-fill" style="width:${barPct}%"></div></div>
      </td>
      <td class="col-val">${value}</td>
    `;
    tr.addEventListener("click", () => openProfile(u.login));
    tbody.appendChild(tr);
  });

  document.getElementById("valueHeader").textContent = LABELS[currentMetric];
  document.getElementById("boardSub").textContent =
    `${rows.length} contributor${rows.length === 1 ? "" : "s"} · click a row`;
  document.getElementById("chartSub").textContent =
    currentMetric === "global_score"
      ? "activity proxy · cumulative"
      : `${LABELS[currentMetric]} · cumulative`;

  renderChart(rows.slice(0, 5));
}

// ---------- main chart ----------

function weeksInRange() {
  const { from, to } = selectedRange();
  return (DATA.weeks || []).filter((w) => w >= from && w <= to);
}

function cumulativeSeries(user, metric, weeks) {
  const tl = user.timeline || {};
  let acc = 0;
  return weeks.map((w) => {
    if (metric === "global_score") {
      const vals = tl[w] || {};
      acc += METRICS.reduce((s, k) => s + (vals[k] || 0), 0);
    } else {
      acc += (tl[w] || {})[metric] || 0;
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

function sparseTicks(weeks) {
  const max = 12;
  if (weeks.length <= max) return {};
  const every = Math.ceil(weeks.length / max);
  return {
    callback: function (_val, idx) {
      return idx % every === 0 ? weeks[idx] : "";
    },
  };
}

function renderChart(topUsers) {
  const weeks = weeksInRange();
  const datasets = topUsers.map((u, i) => {
    const c = CHART_COLORS[i % CHART_COLORS.length];
    return {
      label: u.login,
      data: cumulativeSeries(u, currentMetric, weeks),
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
    data: { labels: weeks, datasets },
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
        },
      },
      scales: {
        x: {
          ticks: {
            color: "#8a93ab",
            maxRotation: 0,
            autoSkip: false,
            font: { family: "Inter" },
            ...sparseTicks(weeks),
          },
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

// ---------- profile modal ----------

function openProfile(login) {
  const user = DATA.users.find((u) => u.login === login);
  if (!user) return;
  const { from, to } = selectedRange();
  const filtered = aggregate(user, from, to, "");
  const allTime = METRICS.reduce((acc, m) => ({ ...acc, [m]: user[m] || 0 }), {});

  const body = document.getElementById("modal-body");
  body.innerHTML = `
    <div class="profile-head">
      ${user.avatar_url ? `<img src="${user.avatar_url}" alt="" />` : ""}
      <div>
        <h2>@${escapeHtml(user.login)}</h2>
        <a href="${user.html_url || "https://github.com/" + encodeURIComponent(user.login)}"
           target="_blank" rel="noopener">View on GitHub ↗</a>
      </div>
    </div>

    <h3 class="profile-h3">Metric breakdown</h3>
    <div class="metric-grid">
      ${METRICS.map((m) => `
        <div class="metric-card">
          <span class="metric-label">${LABELS[m]}</span>
          <span class="metric-value">${(filtered[m] || 0).toLocaleString()}</span>
          <span class="metric-total">${(allTime[m] || 0).toLocaleString()} all-time</span>
        </div>
      `).join("")}
    </div>

    <h3 class="profile-h3">Per repository (all time)</h3>
    <div class="repo-table-wrap">
      ${renderPerRepoTable(user)}
    </div>

    <h3 class="profile-h3">Evolution — cumulative</h3>
    <div class="user-chart-wrap">
      <canvas id="userChart"></canvas>
    </div>
  `;

  document.getElementById("modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  renderUserChart(user);
}

function renderPerRepoTable(user) {
  const perRepo = user.per_repo || {};
  const entries = Object.entries(perRepo)
    .map(([repo, vals]) => ({
      repo, ...vals,
      total: METRICS.reduce((s, m) => s + (vals[m] || 0), 0),
    }))
    .sort((a, b) => b.total - a.total);
  if (!entries.length) return `<p class="muted">No per-repo data.</p>`;

  return `
    <table class="repo-table">
      <thead>
        <tr>
          <th>Repository</th>
          <th>Commits</th>
          <th>Java</th>
          <th>GAML</th>
          <th>Wiki</th>
          <th>Issues</th>
          <th>PRs</th>
        </tr>
      </thead>
      <tbody>
        ${entries.map((e) => `
          <tr>
            <td class="repo-name">${escapeHtml(e.repo)}</td>
            <td>${(e.commits || 0).toLocaleString()}</td>
            <td>${(e.java_lines || 0).toLocaleString()}</td>
            <td>${(e.gaml_lines || 0).toLocaleString()}</td>
            <td>${(e.wiki_lines || 0).toLocaleString()}</td>
            <td>${((e.issues_opened || 0) + (e.issues_closed || 0)).toLocaleString()}</td>
            <td>${((e.prs_opened || 0) + (e.prs_merged || 0)).toLocaleString()}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderUserChart(user) {
  const weeks = weeksInRange();
  const datasets = METRICS.slice(0, 4).map((m, i) => {
    const c = CHART_COLORS[i % CHART_COLORS.length];
    return {
      label: LABELS[m],
      data: cumulativeSeries(user, m, weeks),
      borderColor: c.border,
      backgroundColor: c.bg,
      fill: true,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 2,
    };
  });
  const cfg = {
    type: "line",
    data: { labels: weeks, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#f1f3f9", font: { family: "Inter" }, usePointStyle: true, padding: 12 },
        },
      },
      scales: {
        x: {
          ticks: { color: "#8a93ab", font: { family: "Inter" }, autoSkip: false, ...sparseTicks(weeks) },
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
  if (userChart) userChart.destroy();
  const ctx = document.getElementById("userChart").getContext("2d");
  userChart = new Chart(ctx, cfg);
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
  document.body.style.overflow = "";
  if (userChart) { userChart.destroy(); userChart = null; }
}

document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.dataset.close !== undefined || e.target.closest("[data-close]")) {
    closeModal();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// ---------- tabs ----------

document.querySelectorAll("#tabs .chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs .chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentMetric = btn.dataset.metric;
    if (DATA) render();
  });
});

// ---------- misc ----------

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

load();
