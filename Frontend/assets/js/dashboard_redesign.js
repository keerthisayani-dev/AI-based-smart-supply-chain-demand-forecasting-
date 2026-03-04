
const categoryEl = document.getElementById("category");
const fromDateEl = document.getElementById("fromDate");
const toDateEl = document.getElementById("toDate");
const categoryErrorEl = document.getElementById("categoryError");
const fromDateErrorEl = document.getElementById("fromDateError");
const toDateErrorEl = document.getElementById("toDateError");
const userMetaChipEl = document.getElementById("userMetaChip");
const roleMetaChipEl = document.getElementById("roleMetaChip");
const roleHintEl = document.getElementById("roleHint");

const ALLOWED_MIN_DATE = "2025-01-01";
const ALLOWED_MAX_DATE = "2031-12-31";
const DASHBOARD_STATE_KEY = "dashboard_form_state_v1";
const DASHBOARD_FORCE_CLEAR_KEY = "dashboard_force_clear_v1";
const LOGIN_CLEAR_AFTER_LOGOUT_KEY = "demandiq_clear_login_after_logout_v1";

let fromPicker = null;
let toPicker = null;
let actualForecastChart = null;
let categoryComparisonChart = null;
let stockRiskGaugeChart = null;
const vizDataCache = new Map();

function apiBase() {
  return window.location.origin;
}

function setFieldError(inputEl, errorEl, message) {
  errorEl.textContent = String(message || "");
  errorEl.classList.add("is-visible");
  inputEl.classList.add("input-error");
}

function clearFieldError(inputEl, errorEl) {
  errorEl.textContent = "";
  errorEl.classList.remove("is-visible");
  inputEl.classList.remove("input-error");
}

function clearAllErrors() {
  clearFieldError(categoryEl, categoryErrorEl);
  clearFieldError(fromDateEl, fromDateErrorEl);
  clearFieldError(toDateEl, toDateErrorEl);
}

function setChartEmptyState(id, show, message) {
  const el = document.getElementById(id);
  if (!el) return;
  if (message) el.textContent = message;
  el.classList.toggle("is-hidden", !show);
}

function formatShortDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatRole(role) {
  const mapping = {
    admin: "Admin",
    inventory_manager: "Inventory Manager",
    viewer: "Viewer",
  };
  const key = String(role || "").toLowerCase();
  return mapping[key] || "Unknown";
}

function getResolvedDateRange() {
  const from = String(fromDateEl.value || "").trim();
  const to = String(toDateEl.value || "").trim();
  if (from && to) return { from, to };
  const now = new Date();
  const start = new Date(now);
  start.setDate(start.getDate() - 13);
  return {
    from: start.toISOString().slice(0, 10),
    to: now.toISOString().slice(0, 10),
  };
}

function getSelectedOrFirstCategory() {
  if (categoryEl.value) return categoryEl.value;
  const first = Array.from(categoryEl.options || []).find((opt) => opt.value);
  return first ? first.value : "";
}

async function fetchForecastSnapshot(category, fromDate, toDate) {
  const key = `${category}|${fromDate}|${toDate}`;
  if (vizDataCache.has(key)) return vizDataCache.get(key);
  const promise = (async () => {
    const fromDateObj = new Date(fromDate);
    const toDateObj = new Date(toDate);
    const periodDays = Math.max(1, Math.floor((toDateObj - fromDateObj) / 86400000) + 1);
    const lookback = Math.max(60, periodDays);
    const anchor = new Date(fromDateObj);
    anchor.setDate(anchor.getDate() - 1);
    const anchorDate = anchor.toISOString().slice(0, 10);
    const url = `${apiBase()}/forecast/${encodeURIComponent(category)}?horizon=${periodDays}&history_lookback_days=${lookback}&anchor_date=${anchorDate}`;
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) throw new Error(`Forecast load failed (${resp.status}).`);
    const data = await resp.json();
    const history = Array.isArray(data.history) ? data.history : [];
    const forecast = Array.isArray(data.forecast) ? data.forecast : [];
    const fromTs = new Date(fromDate).getTime();
    const toTs = new Date(toDate).getTime();
    return {
      filteredHistory: history.filter((r) => {
        const t = new Date(r.date).getTime();
        return t >= fromTs && t <= toTs;
      }),
      filteredForecast: forecast.filter((r) => {
        const t = new Date(r.date).getTime();
        return t >= fromTs && t <= toTs;
      }),
    };
  })();
  vizDataCache.set(key, promise);
  return promise;
}

function destroyCharts() {
  if (actualForecastChart) actualForecastChart.destroy();
  if (categoryComparisonChart) categoryComparisonChart.destroy();
  if (stockRiskGaugeChart) stockRiskGaugeChart.destroy();
  actualForecastChart = null;
  categoryComparisonChart = null;
  stockRiskGaugeChart = null;
}
async function renderForecastVisualization() {
  if (typeof Chart === "undefined") return;
  const category = getSelectedOrFirstCategory();
  if (!category) {
    destroyCharts();
    document.getElementById("avgForecastKpi").textContent = "-";
    document.getElementById("peakDayKpi").textContent = "-";
    document.getElementById("riskLevelKpi").textContent = "-";
    document.getElementById("summaryHint").textContent = "Select a category to preview summary.";
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
    setChartEmptyState("stockRiskEmpty", true, "No risk data available.");
    return;
  }

  const { from, to } = getResolvedDateRange();
  const subtitleEl = document.getElementById("vizSubtitle");
  subtitleEl.textContent = `${category} | ${from} to ${to}`;

  let snapshot;
  try {
    snapshot = await fetchForecastSnapshot(category, from, to);
  } catch (err) {
    destroyCharts();
    subtitleEl.textContent = `Visualization unavailable: ${err.message}`;
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
    setChartEmptyState("stockRiskEmpty", true, "No risk data available.");
    return;
  }

  const history = snapshot.filteredHistory;
  const forecast = snapshot.filteredForecast;
  const labels = Array.from(new Set([
    ...history.map((x) => String(x.date || "")),
    ...forecast.map((x) => String(x.date || "")),
  ])).filter(Boolean).sort();

  const actualMap = new Map(history.map((r) => [r.date, Number(r.actual_units_sold || 0)]));
  const forecastMap = new Map(forecast.map((r) => [r.date, Number(r.forecast_units_sold || 0)]));
  const actualSeries = labels.map((d) => (actualMap.has(d) ? actualMap.get(d) : null));
  const forecastSeries = labels.map((d) => (forecastMap.has(d) ? forecastMap.get(d) : null));

  const totalForecast = forecast.reduce((sum, r) => sum + Number(r.forecast_units_sold || 0), 0);
  const avgForecast = forecast.length ? totalForecast / forecast.length : 0;
  const peakDayRow = forecast.reduce((max, row) => {
    const current = Number(row?.forecast_units_sold || 0);
    const best = Number(max?.forecast_units_sold || -Infinity);
    return current > best ? row : max;
  }, null);
  const latestActual = history.length ? Number(history[history.length - 1].actual_units_sold || 0) : 0;
  const coverageDays = avgForecast > 0 ? (latestActual * 10) / avgForecast : 0;

  let riskLabel = "Low";
  let riskValue = 25;
  let riskColor = "#22c55e";
  if (coverageDays < 5) {
    riskLabel = "High";
    riskValue = 90;
    riskColor = "#ef4444";
  } else if (coverageDays < 10) {
    riskLabel = "Medium";
    riskValue = 60;
    riskColor = "#f59e0b";
  }

  document.getElementById("avgForecastKpi").textContent = avgForecast > 0 ? avgForecast.toFixed(1) : "-";
  document.getElementById("peakDayKpi").textContent = peakDayRow?.date ? formatShortDate(peakDayRow.date) : "-";
  document.getElementById("riskLevelKpi").textContent = riskLabel;
  document.getElementById("summaryHint").textContent = peakDayRow?.date
    ? `Peak forecast on ${formatShortDate(peakDayRow.date)} with ${Number(peakDayRow.forecast_units_sold || 0).toFixed(1)} units.`
    : "Summary updates when category/date changes.";

  const lineCtx = document.getElementById("actualForecastChart").getContext("2d");
  const lineBlue = lineCtx.createLinearGradient(0, 0, 0, 320);
  lineBlue.addColorStop(0, "rgba(59, 130, 246, 0.95)");
  lineBlue.addColorStop(1, "rgba(59, 130, 246, 0.45)");
  const linePurple = lineCtx.createLinearGradient(0, 0, 0, 320);
  linePurple.addColorStop(0, "rgba(139, 92, 246, 0.95)");
  linePurple.addColorStop(1, "rgba(139, 92, 246, 0.45)");

  if (actualForecastChart) actualForecastChart.destroy();
  if (labels.length) {
    setChartEmptyState("actualForecastEmpty", false);
    actualForecastChart = new Chart(lineCtx, {
      type: "line",
      data: {
        labels: labels.map(formatShortDate),
        datasets: [
          { label: "Actual Demand", data: actualSeries, borderColor: lineBlue, backgroundColor: "rgba(59, 130, 246, 0.18)", tension: 0.35, borderWidth: 2, pointRadius: 1.6, spanGaps: true },
          { label: "Forecast Demand", data: forecastSeries, borderColor: linePurple, backgroundColor: "rgba(139, 92, 246, 0.18)", tension: 0.35, borderWidth: 2, pointRadius: 1.6, spanGaps: true },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 1200, easing: "easeOutQuart" },
        plugins: { legend: { labels: { color: "#dbe7ff", boxWidth: 12, boxHeight: 2, usePointStyle: true } } },
        scales: {
          x: { ticks: { color: "#9cb0d8" }, grid: { color: "rgba(148, 163, 184, 0.12)" } },
          y: { ticks: { color: "#9cb0d8" }, grid: { color: "rgba(148, 163, 184, 0.12)" } },
        },
      },
    });
  } else {
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
  }

  const categoryOptions = Array.from(categoryEl.options || []).map((opt) => String(opt.value || "").trim()).filter(Boolean).slice(0, 6);
  const categoryRows = await Promise.all(categoryOptions.map(async (cat) => {
    try {
      const snap = await fetchForecastSnapshot(cat, from, to);
      const totalActual = snap.filteredHistory.reduce((sum, r) => sum + Number(r.actual_units_sold || 0), 0);
      const totalProjected = snap.filteredForecast.reduce((sum, r) => sum + Number(r.forecast_units_sold || 0), 0);
      return { category: cat, units: totalActual > 0 ? totalActual : totalProjected };
    } catch (_) {
      return { category: cat, units: 0 };
    }
  }));

  const barCtx = document.getElementById("categoryComparisonChart").getContext("2d");
  const barGrad = barCtx.createLinearGradient(0, 0, 0, 320);
  barGrad.addColorStop(0, "rgba(79, 172, 254, 0.95)");
  barGrad.addColorStop(1, "rgba(123, 97, 255, 0.75)");

  if (categoryComparisonChart) categoryComparisonChart.destroy();
  if (categoryRows.length) {
    setChartEmptyState("categoryComparisonEmpty", false);
    categoryComparisonChart = new Chart(barCtx, {
      type: "bar",
      data: { labels: categoryRows.map((r) => r.category), datasets: [{ label: "Units Sold", data: categoryRows.map((r) => Number(r.units.toFixed(1))), backgroundColor: barGrad, borderRadius: 8, borderSkipped: false }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 1200, easing: "easeOutQuart" },
        plugins: { legend: { labels: { color: "#dbe7ff", boxWidth: 12, boxHeight: 2, usePointStyle: true } } },
        scales: {
          x: { ticks: { color: "#9cb0d8" }, grid: { display: false } },
          y: { ticks: { color: "#9cb0d8" }, grid: { color: "rgba(148, 163, 184, 0.12)" } },
        },
      },
    });
  } else {
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
  }

  const centerTextPlugin = {
    id: "centerTextPlugin",
    afterDraw(chart) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const x = (chartArea.left + chartArea.right) / 2;
      const y = (chartArea.top + chartArea.bottom) / 2;
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = "#dbe7ff";
      ctx.font = "700 26px Inter";
      ctx.fillText(riskLabel, x, y + 2);
      ctx.fillStyle = "#9cb0d8";
      ctx.font = "500 12px Inter";
      ctx.fillText("Inventory Risk", x, y + 22);
      ctx.restore();
    },
  };

  const gaugeCtx = document.getElementById("stockRiskGauge").getContext("2d");
  if (stockRiskGaugeChart) stockRiskGaugeChart.destroy();
  setChartEmptyState("stockRiskEmpty", false);
  stockRiskGaugeChart = new Chart(gaugeCtx, {
    type: "doughnut",
    data: { labels: ["Risk", "Remaining"], datasets: [{ data: [riskValue, Math.max(0, 100 - riskValue)], backgroundColor: [riskColor, "rgba(148, 163, 184, 0.18)"], borderWidth: 0, cutout: "72%", circumference: 360, rotation: -90 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 1200, easing: "easeOutQuart" }, plugins: { legend: { display: false } } },
    plugins: [centerTextPlugin],
  });

}

async function applyRoleUi() {
  try {
    const resp = await fetch(`${apiBase()}/auth/me`, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!resp.ok) {
      window.location.href = "/login";
      return;
    }
    const data = await resp.json();
    const role = String(data?.user?.role || "");
    const fullName = String(data?.user?.full_name || "User");
    const roleTitle = formatRole(role);
    if (userMetaChipEl) userMetaChipEl.textContent = `User: ${fullName}`;
    if (roleMetaChipEl) roleMetaChipEl.textContent = `Role: ${roleTitle}`;

    const adminDashBtn = document.getElementById("adminDashBtn");
    if (adminDashBtn) adminDashBtn.style.display = role === "admin" ? "" : "none";

    if (role === "admin") roleHintEl.textContent = "Admin access: full system operations enabled.";
    if (role === "inventory_manager") roleHintEl.textContent = "Inventory Manager access: operational inventory insights enabled.";
    if (role === "viewer") roleHintEl.textContent = "Viewer access: read-only dashboard and forecasts. Export and inventory insights are restricted.";

    const downloadBtn = document.getElementById("downloadBtn");
    if (downloadBtn) {
      const restrictDownload = role === "viewer";
      downloadBtn.disabled = restrictDownload;
      downloadBtn.style.opacity = restrictDownload ? "0.65" : "1";
      downloadBtn.title = restrictDownload ? "Download restricted for Viewer role" : "";
    }
  } catch (_) {
    window.location.href = "/login";
  }
}
function initDatePickers() {
  if (fromPicker) fromPicker.destroy();
  if (toPicker) toPicker.destroy();
  fromPicker = flatpickr(fromDateEl, {
    dateFormat: "Y-m-d",
    allowInput: false,
    monthSelectorType: "static",
    minDate: ALLOWED_MIN_DATE,
    maxDate: ALLOWED_MAX_DATE,
  });
  toPicker = flatpickr(toDateEl, {
    dateFormat: "Y-m-d",
    allowInput: false,
    monthSelectorType: "static",
    minDate: ALLOWED_MIN_DATE,
    maxDate: ALLOWED_MAX_DATE,
  });
}

async function loadCategories(preferredCategory) {
  try {
    const resp = await fetch(`${apiBase()}/categories`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const categories = Array.isArray(data.categories) ? data.categories : [];
    const dropdownValues = [...new Set(categories.map((v) => String(v).trim()).filter(Boolean))].sort();
    categoryEl.innerHTML = '<option value="" selected disabled hidden>Select category</option>';
    dropdownValues.forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      categoryEl.appendChild(opt);
    });
    if (preferredCategory && dropdownValues.includes(preferredCategory)) {
      categoryEl.value = preferredCategory;
    }
  } catch (err) {
    console.error(`Failed to load categories: ${err.message}`);
    setFieldError(categoryEl, categoryErrorEl, "Unable to load categories. Please refresh and try again.");
  }
}

function clearInputState() {
  categoryEl.innerHTML = "";
  fromDateEl.value = "";
  toDateEl.value = "";
}

function saveDashboardState() {
  const state = { category: categoryEl.value || "", fromDate: fromDateEl.value || "", toDate: toDateEl.value || "" };
  sessionStorage.setItem(DASHBOARD_STATE_KEY, JSON.stringify(state));
}

function clearDashboardState() {
  sessionStorage.removeItem(DASHBOARD_STATE_KEY);
}

function readDashboardState() {
  try {
    const raw = sessionStorage.getItem(DASHBOARD_STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return {
      category: String(parsed?.category || ""),
      fromDate: String(parsed?.fromDate || ""),
      toDate: String(parsed?.toDate || ""),
    };
  } catch (_) {
    return null;
  }
}

function getNavigationType() {
  const navEntry = performance.getEntriesByType("navigation")[0];
  return navEntry ? navEntry.type : "";
}

function consumeForceClearFlag() {
  const shouldClear = sessionStorage.getItem(DASHBOARD_FORCE_CLEAR_KEY) === "1";
  if (shouldClear) sessionStorage.removeItem(DASHBOARD_FORCE_CLEAR_KEY);
  return shouldClear;
}

async function initializeDefaults() {
  clearAllErrors();
  await applyRoleUi();
  const shouldClear = getNavigationType() === "reload" || consumeForceClearFlag();
  const savedState = shouldClear ? null : readDashboardState();
  if (shouldClear) clearDashboardState();
  clearInputState();
  initDatePickers();
  await loadCategories(savedState?.category || "");
  if (savedState?.fromDate) fromPicker.setDate(savedState.fromDate, true, "Y-m-d");
  if (savedState?.toDate) toPicker.setDate(savedState.toDate, true, "Y-m-d");
  const shell = document.querySelector(".page-shell");
  if (shell) {
    shell.classList.remove("preload");
    shell.classList.add("ready");
  }
  await renderForecastVisualization();
}

function validateInputs() {
  clearAllErrors();
  const category = categoryEl.value;
  const fromDate = fromDateEl.value;
  const toDate = toDateEl.value;
  let hasError = false;
  if (!category) {
    setFieldError(categoryEl, categoryErrorEl, "Please select a category.");
    hasError = true;
  }
  if (!fromDate) {
    setFieldError(fromDateEl, fromDateErrorEl, "Please select a start date.");
    hasError = true;
  }
  if (!toDate) {
    setFieldError(toDateEl, toDateErrorEl, "Please select an end date.");
    hasError = true;
  }
  if (hasError) return false;
  const from = new Date(fromDate);
  const to = new Date(toDate);
  if (from > to) {
    setFieldError(toDateEl, toDateErrorEl, "To Date must be on or after From Date.");
    return false;
  }
  const minAllowed = new Date(ALLOWED_MIN_DATE);
  const maxAllowed = new Date(ALLOWED_MAX_DATE);
  if (from < minAllowed || from > maxAllowed) {
    setFieldError(fromDateEl, fromDateErrorEl, "Use a date between 2025 and 2031.");
    hasError = true;
  }
  if (to < minAllowed || to > maxAllowed) {
    setFieldError(toDateEl, toDateErrorEl, "Use a date between 2025 and 2031.");
    hasError = true;
  }
  return !hasError;
}

function goToResults(mode) {
  if (!validateInputs()) return;
  saveDashboardState();
  const resultUrl = `/dashboard/results?category=${encodeURIComponent(categoryEl.value)}&from=${encodeURIComponent(fromDateEl.value)}&to=${encodeURIComponent(toDateEl.value)}&mode=${encodeURIComponent(mode)}`;
  window.location.href = resultUrl;
}

async function logoutAndGoLogin() {
  try {
    await fetch("/auth/logout", { method: "POST" });
  } catch (_) {
    // Continue even if logout API fails.
  }
  sessionStorage.removeItem(DASHBOARD_STATE_KEY);
  sessionStorage.setItem(DASHBOARD_FORCE_CLEAR_KEY, "1");
  sessionStorage.setItem(LOGIN_CLEAR_AFTER_LOGOUT_KEY, "1");
  window.location.href = "/login?logged_out=1";
}

categoryEl.addEventListener("change", async () => {
  clearFieldError(categoryEl, categoryErrorEl);
  await renderForecastVisualization();
});
fromDateEl.addEventListener("input", async () => {
  clearFieldError(fromDateEl, fromDateErrorEl);
  await renderForecastVisualization();
});
toDateEl.addEventListener("input", async () => {
  clearFieldError(toDateEl, toDateErrorEl);
  await renderForecastVisualization();
});

document.getElementById("generateBtn").addEventListener("click", () => goToResults("forecast"));
document.getElementById("downloadBtn").addEventListener("click", () => goToResults("forecast"));
document.getElementById("navReports").addEventListener("click", () => {
  goToResults("forecast");
});
document.getElementById("pastDemandBtn").addEventListener("click", () => {
  goToResults("past");
});
document.getElementById("backBtn").addEventListener("click", () => {
  if (window.history.length > 1) {
    window.history.back();
  } else {
    window.location.href = "/login";
  }
});
document.getElementById("adminDashBtn").addEventListener("click", () => {
  window.location.href = "/admin/dashboard";
});
document.getElementById("logoutBtn").addEventListener("click", () => {
  logoutAndGoLogin();
});

initializeDefaults();
window.addEventListener("pageshow", initializeDefaults);
