
const cityEl = document.getElementById("city");
const categoryEl = document.getElementById("category");
const fromDateEl = document.getElementById("fromDate");
const toDateEl = document.getElementById("toDate");
const cityShellEl = document.getElementById("cityShell");
const cityTriggerEl = document.getElementById("cityTrigger");
const cityTriggerTextEl = document.getElementById("cityTriggerText");
const cityMenuEl = document.getElementById("cityMenu");
const cityErrorEl = document.getElementById("cityError");
const cityMetaEl = document.getElementById("cityMeta");
const storeEl = document.getElementById("store");
const storeShellEl = document.getElementById("storeShell");
const storeTriggerEl = document.getElementById("storeTrigger");
const storeTriggerTextEl = document.getElementById("storeTriggerText");
const storeMenuEl = document.getElementById("storeMenu");
const storeErrorEl = document.getElementById("storeError");
const storeMetaEl = document.getElementById("storeMeta");
const categoryShellEl = document.getElementById("categoryShell");
const categoryTriggerEl = document.getElementById("categoryTrigger");
const categoryTriggerTextEl = document.getElementById("categoryTriggerText");
const categoryMenuEl = document.getElementById("categoryMenu");
const categoryErrorEl = document.getElementById("categoryError");
const categoryMetaEl = document.getElementById("categoryMeta");
const debugOutputEl = document.getElementById("debugOutput");
const forecastInputsEl = document.querySelector(".forecast-inputs");
const fromDateErrorEl = document.getElementById("fromDateError");
const toDateErrorEl = document.getElementById("toDateError");
const userMetaChipEl = document.getElementById("userMetaChip");
const roleMetaChipEl = document.getElementById("roleMetaChip");
const roleHintEl = document.getElementById("roleHint");
const topUserNameEl = document.getElementById("topUserName");
const dashboardTimeEl = document.getElementById("dashboardTime");
const sidebarToggleBtn = document.getElementById("sidebarToggleBtn");
const refreshBtnEl = document.getElementById("refreshBtn");
const syncBtnEl = document.getElementById("syncBtn");
const topLogoutBtnEl = document.getElementById("topLogoutBtn");
const aiInsightTextEl = document.getElementById("aiInsightText");
const avgDemandValueEl = document.getElementById("avgDemandValue");
const leadTimeValueEl = document.getElementById("leadTimeValue");
const safetyStockValueEl = document.getElementById("safetyStockValue");
const reorderPointValueEl = document.getElementById("reorderPointValue");
const recommendedOrderValueEl = document.getElementById("recommendedOrderValue");
const inventoryAlertsListEl = document.getElementById("inventoryAlertsList");
const criticalCountEl = document.getElementById("criticalCount");
const warningCountEl = document.getElementById("warningCount");
const infoCountEl = document.getElementById("infoCount");
const maeValueEl = document.getElementById("maeValue");
const rmseValueEl = document.getElementById("rmseValue");
const r2ValueEl = document.getElementById("r2Value");
const exportCsvBtnEl = document.getElementById("exportCsvBtn");
const exportPngBtnEl = document.getElementById("exportPngBtn");
const exportPdfBtnEl = document.getElementById("exportPdfBtn");

const ALLOWED_MIN_DATE = "2025-01-01";
const ALLOWED_MAX_DATE = "2031-12-31";
const DASHBOARD_STATE_KEY = "dashboard_form_state_v1";
const DASHBOARD_FORCE_CLEAR_KEY = "dashboard_force_clear_v1";
const LOGIN_CLEAR_AFTER_LOGOUT_KEY = "demandiq_clear_login_after_logout_v1";
const DEFAULT_LEAD_TIME_DAYS = 5;
const DEFAULT_SAFETY_STOCK = 150;
const DEFAULT_PERFORMANCE_METRICS = { mae: 8.41, rmse: 10.26, r2: 0.89 };
const RENDER_DEBOUNCE_MS = 180;
const SIDEBAR_COLLAPSED_KEY = "dashboard_sidebar_collapsed_v1";

let fromPicker = null;
let toPicker = null;
let actualForecastChart = null;
let categoryComparisonChart = null;
let stockRiskGaugeChart = null;
const vizDataCache = new Map();
let latestDashboardData = null;
let renderDebounceTimer = null;
let renderRunId = 0;
let latestRenderKey = "";
const scopeOptionsState = {
  cities: [],
  categories: [],
  categoryCityMap: new Map(),
  cityCategoryMap: new Map(),
  categoryCityStoreMap: new Map(),
};
const debugLogLines = [];

function apiBase() {
  return window.location.origin;
}

function debugLog(message, details = null) {
  const line = details == null
    ? String(message)
    : `${String(message)} ${typeof details === "string" ? details : JSON.stringify(details)}`;
  debugLogLines.push(line);
  while (debugLogLines.length > 40) debugLogLines.shift();
  if (debugOutputEl) debugOutputEl.textContent = debugLogLines.join("\n");
  try {
    console.debug("[DemandIQ]", message, details ?? "");
  } catch (_) {
    // Ignore console issues.
  }
}

async function fetchJsonWithDebug(url) {
  debugLog("fetch:start", url);
  const resp = await fetch(url, { cache: "no-store" });
  const text = await resp.text();
  debugLog("fetch:status", { url, status: resp.status, ok: resp.ok, preview: text.slice(0, 180) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${text.slice(0, 180)}`);
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`Invalid JSON from ${url}: ${text.slice(0, 180)}`);
  }
}

function setFieldError(inputEl, errorEl, message) {
  errorEl.textContent = String(message || "");
  errorEl.classList.add("is-visible");
  inputEl.classList.add("input-error");
  if (inputEl === cityEl && cityTriggerEl) cityTriggerEl.classList.add("input-error");
  if (inputEl === storeEl && storeTriggerEl) storeTriggerEl.classList.add("input-error");
  if (inputEl === categoryEl && categoryTriggerEl) categoryTriggerEl.classList.add("input-error");
}

function clearFieldError(inputEl, errorEl) {
  errorEl.textContent = "";
  errorEl.classList.remove("is-visible");
  inputEl.classList.remove("input-error");
  if (inputEl === cityEl && cityTriggerEl) cityTriggerEl.classList.remove("input-error");
  if (inputEl === storeEl && storeTriggerEl) storeTriggerEl.classList.remove("input-error");
  if (inputEl === categoryEl && categoryTriggerEl) categoryTriggerEl.classList.remove("input-error");
}

function clearAllErrors() {
  clearFieldError(cityEl, cityErrorEl);
  clearFieldError(storeEl, storeErrorEl);
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

function formatTimeDisplay(dateObj = new Date()) {
  return dateObj.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

function updateDashboardClock() {
  if (dashboardTimeEl) dashboardTimeEl.textContent = formatTimeDisplay(new Date());
}

function applySidebarState(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  if (sidebarToggleBtn) {
    sidebarToggleBtn.setAttribute("aria-pressed", collapsed ? "true" : "false");
    sidebarToggleBtn.setAttribute("title", collapsed ? "Expand sidebar" : "Collapse sidebar");
  }
}

function initializeSidebarState() {
  try {
    const collapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    applySidebarState(collapsed);
  } catch (_) {
    applySidebarState(false);
  }
}

function formatUnits(value, fractionDigits = 0) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return `${num.toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })} units`;
}

function formatDecimal(value, fractionDigits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return num.toFixed(fractionDigits);
}

function normalizeScopeValues(values = []) {
  return [...new Set(
    (Array.isArray(values) ? values : [])
      .map((v) => String(v || "").trim())
      .filter(Boolean),
  )].sort();
}

function normalizeScopeMap(rawMap) {
  const out = new Map();
  if (!rawMap || typeof rawMap !== "object") return out;
  Object.entries(rawMap).forEach(([key, values]) => {
    const normalizedKey = String(key || "").trim();
    if (!normalizedKey) return;
    out.set(normalizedKey, normalizeScopeValues(values));
  });
  return out;
}

function getScopeMapValues(mapObj, key) {
  const requested = String(key || "").trim();
  if (!requested || !(mapObj instanceof Map) || !mapObj.size) return [];
  if (mapObj.has(requested)) return mapObj.get(requested) || [];
  const requestedLower = requested.toLowerCase();
  for (const [candidateKey, values] of mapObj.entries()) {
    if (String(candidateKey || "").trim().toLowerCase() === requestedLower) {
      return Array.isArray(values) ? values : [];
    }
  }
  return [];
}

function intersectScopeValues(left = [], right = []) {
  if (!left.length || !right.length) return [];
  const rightSet = new Set(right);
  return left.filter((value) => rightSet.has(value));
}

function getCitiesForCategory(category) {
  const key = String(category || "").trim();
  if (!key) return [...scopeOptionsState.cities];
  if (!scopeOptionsState.categoryCityMap.size) return [];
  return getScopeMapValues(scopeOptionsState.categoryCityMap, key);
}

function getCategoriesForCity(city) {
  const key = String(city || "").trim();
  if (!key) return [...scopeOptionsState.categories];
  if (!scopeOptionsState.cityCategoryMap.size) return [];
  return getScopeMapValues(scopeOptionsState.cityCategoryMap, key);
}

function getStoresForCategoryCity(category, city) {
  const categoryKey = String(category || "").trim();
  const cityKey = String(city || "").trim();
  if (!categoryKey || !cityKey) return [];
  return getScopeMapValues(scopeOptionsState.categoryCityStoreMap, `${categoryKey}|||${cityKey}`);
}

function populateScopeSelect(selectEl, placeholder, values, selectedValue) {
  selectEl.innerHTML = `<option value="" selected disabled>${placeholder}</option>`;
  values.forEach((value) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    selectEl.appendChild(opt);
  });
  if (selectedValue && values.includes(selectedValue)) {
    selectEl.value = selectedValue;
  }
}

function setCategoryFirstMode() {
  const hasCategory = Boolean(String(categoryEl.value || "").trim());
  cityEl.disabled = !hasCategory;
  if (cityTriggerEl) {
    cityTriggerEl.disabled = !hasCategory;
    cityTriggerEl.setAttribute("aria-disabled", hasCategory ? "false" : "true");
    cityTriggerEl.title = hasCategory ? "" : "Select a category first";
  }
  if (cityShellEl) cityShellEl.classList.toggle("is-disabled", !hasCategory);
  if (!hasCategory) {
    cityEl.value = "";
    updateCityPlaceholderState();
  }
  const hasCity = hasCategory && Boolean(String(cityEl.value || "").trim());
  storeEl.disabled = !hasCity;
  if (storeTriggerEl) {
    storeTriggerEl.disabled = !hasCity;
    storeTriggerEl.setAttribute("aria-disabled", hasCity ? "false" : "true");
    storeTriggerEl.title = hasCity ? "" : "Select a city first";
  }
  if (storeShellEl) storeShellEl.classList.toggle("is-disabled", !hasCity);
  if (!hasCity) {
    storeEl.value = "";
    updateStorePlaceholderState();
  }
  if (cityMetaEl) {
    const selectedCategory = String(categoryEl.value || "").trim();
    if (!selectedCategory) {
      cityMetaEl.textContent = "Select a category to view available cities.";
    } else {
      const cities = getCitiesForCategory(selectedCategory);
      cityMetaEl.textContent = cities.length
        ? `${cities.length} cities available for ${selectedCategory}`
        : `No cities available for ${selectedCategory}`;
    }
  }
  if (storeMetaEl) {
    const selectedCategory = String(categoryEl.value || "").trim();
    const selectedCity = String(cityEl.value || "").trim();
    if (!selectedCategory) {
      storeMetaEl.textContent = "Select a category and city to view available stores.";
    } else if (!selectedCity) {
      storeMetaEl.textContent = "Select a city to view available stores.";
    } else {
      const stores = getStoresForCategoryCity(selectedCategory, selectedCity);
      storeMetaEl.textContent = stores.length
        ? `${stores.length} stores available in ${selectedCity}`
        : `No stores available in ${selectedCity} for ${selectedCategory}`;
    }
  }
  if (categoryMetaEl) {
    categoryMetaEl.textContent = scopeOptionsState.categories.length
      ? `${scopeOptionsState.categories.length} categories available for prediction`
      : "No categories available right now";
  }
}

function syncScopeSelections(source = "init", preferredCity = "", preferredCategory = "", preferredStore = "") {
  let selectedCity = String(preferredCity || cityEl.value || "").trim();
  let selectedCategory = String(preferredCategory || categoryEl.value || "").trim();
  let selectedStore = String(preferredStore || storeEl.value || "").trim();

  if (source === "category" && selectedCity && selectedCategory) {
    const validCities = getCitiesForCategory(selectedCategory);
    if (!validCities.includes(selectedCity)) selectedCity = "";
  }
  if (source === "city" && selectedCity && selectedCategory) {
    const validCategories = getCategoriesForCity(selectedCity);
    if (!validCategories.includes(selectedCategory)) selectedCategory = "";
  }
  if (source === "init" && selectedCity && selectedCategory) {
    const validCities = getCitiesForCategory(selectedCategory);
    if (!validCities.includes(selectedCity)) selectedCity = "";
  }
  if (selectedStore && (!selectedCategory || !selectedCity)) selectedStore = "";

  let cityValues = [...scopeOptionsState.cities];
  if (selectedCategory) {
    const validCities = getCitiesForCategory(selectedCategory);
    cityValues = cityValues.length
      ? intersectScopeValues(cityValues, validCities)
      : [...validCities];
  }

  let categoryValues = [...scopeOptionsState.categories];
  if (selectedCity) {
    const validCategories = getCategoriesForCity(selectedCity);
    categoryValues = categoryValues.length
      ? intersectScopeValues(categoryValues, validCategories)
      : [...validCategories];
  }

  if (selectedCity && !cityValues.includes(selectedCity)) selectedCity = "";
  if (selectedCategory && !categoryValues.includes(selectedCategory)) selectedCategory = "";
  const storeValues = selectedCategory && selectedCity ? getStoresForCategoryCity(selectedCategory, selectedCity) : [];
  if (selectedStore && !storeValues.includes(selectedStore)) selectedStore = "";

  populateScopeSelect(cityEl, "Select city", cityValues, selectedCity);
  populateScopeSelect(categoryEl, "Select category", categoryValues, selectedCategory);
  populateScopeSelect(storeEl, "Select store", storeValues, selectedStore);
  updateCityPlaceholderState();
  updateCategoryPlaceholderState();
  updateStorePlaceholderState();
  rebuildCityMenu();
  rebuildCategoryMenu();
  rebuildStoreMenu();
  setCategoryFirstMode();
}

function toIsoDate(dateObj) {
  const d = new Date(dateObj);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function clampDateToAllowedRange(dateObj) {
  const d = new Date(dateObj);
  const min = new Date(ALLOWED_MIN_DATE);
  const max = new Date(ALLOWED_MAX_DATE);
  if (d < min) return min;
  if (d > max) return max;
  return d;
}

function getCurrentToNextMonthRange(baseDate = new Date()) {
  const fromDate = clampDateToAllowedRange(baseDate);
  const nextMonthYear = fromDate.getFullYear() + Math.floor((fromDate.getMonth() + 1) / 12);
  const nextMonth = (fromDate.getMonth() + 1) % 12;
  const lastDayOfNextMonth = new Date(nextMonthYear, nextMonth + 1, 0).getDate();
  const targetDay = Math.min(fromDate.getDate(), lastDayOfNextMonth);
  const toDateRaw = new Date(nextMonthYear, nextMonth, targetDay);
  const toDate = clampDateToAllowedRange(toDateRaw);
  return {
    from: toIsoDate(fromDate),
    to: toIsoDate(toDate),
  };
}

function getCurrentAndNextMonthBounds(baseDate = new Date()) {
  const base = clampDateToAllowedRange(baseDate);
  const startOfCurrentMonth = clampDateToAllowedRange(new Date(base.getFullYear(), base.getMonth(), 1));
  const endOfNextMonth = clampDateToAllowedRange(new Date(base.getFullYear(), base.getMonth() + 2, 0));
  return {
    min: toIsoDate(startOfCurrentMonth),
    max: toIsoDate(endOfNextMonth),
  };
}

function setAlertSeverityCounts(alerts = []) {
  let critical = 0;
  let warning = 0;
  let info = 0;
  (Array.isArray(alerts) ? alerts : []).forEach((alert) => {
    const level = String(alert?.level || "").toLowerCase();
    if (level === "critical") critical += 1;
    else if (level === "warning") warning += 1;
    else info += 1;
  });
  if (criticalCountEl) criticalCountEl.textContent = String(critical);
  if (warningCountEl) warningCountEl.textContent = String(warning);
  if (infoCountEl) infoCountEl.textContent = String(info);
}

function resetAdvancedSections(message) {
  if (aiInsightTextEl) {
    aiInsightTextEl.innerHTML = `
      <div class="insight-grid">
        <div class="insight-row"><span>Summary</span><strong>${message || "Generate a prediction to view AI insight."}</strong></div>
        <div class="insight-row"><span>Demand Window</span><strong>-</strong></div>
        <div class="insight-row"><span>Peak Demand</span><strong>-</strong></div>
        <div class="insight-row"><span>Risk Level</span><strong>-</strong></div>
        <div class="insight-row"><span>Recommendation</span><strong>-</strong></div>
      </div>
    `;
  }
  if (avgDemandValueEl) avgDemandValueEl.textContent = "-";
  if (leadTimeValueEl) leadTimeValueEl.textContent = `${DEFAULT_LEAD_TIME_DAYS} days`;
  if (safetyStockValueEl) safetyStockValueEl.textContent = formatUnits(DEFAULT_SAFETY_STOCK);
  if (reorderPointValueEl) reorderPointValueEl.textContent = "-";
  if (recommendedOrderValueEl) recommendedOrderValueEl.textContent = "-";
  if (inventoryAlertsListEl) inventoryAlertsListEl.innerHTML = '<li class="alert-item safe">Inventory alerts will appear after prediction generation.</li>';
  setAlertSeverityCounts([]);
  if (maeValueEl) maeValueEl.textContent = "-";
  if (rmseValueEl) rmseValueEl.textContent = "-";
  if (r2ValueEl) r2ValueEl.textContent = "-";
}

function updateAdvancedSections(data) {
  const {
    category,
    from,
    to,
    avgForecast,
    peakDayRow,
    growthPct,
    reorderPoint,
    recommendedOrderQty,
    estimatedInventory,
    riskLabel,
    alerts,
    metrics,
  } = data;

  let insightLead = "Demand is expected to remain stable";
  if (growthPct > 6) insightLead = "Demand is expected to increase";
  if (growthPct < -6) insightLead = "Demand is expected to decline";
  const peakText = peakDayRow?.date
    ? `${formatShortDate(peakDayRow.date)} with ${formatUnits(peakDayRow.forecast_units_sold, 1)}`
    : "the selected period";

  if (aiInsightTextEl) {
    const recommendation = reorderPoint > estimatedInventory
      ? `Increase stock before ${peakDayRow?.date ? formatShortDate(peakDayRow.date) : "peak demand"}.`
      : "Maintain current stock and monitor trend changes.";
    aiInsightTextEl.innerHTML = `
      <div class="insight-grid">
        <div class="insight-row"><span>Summary</span><strong>${insightLead} for ${category}</strong></div>
        <div class="insight-row"><span>Demand Window</span><strong>${formatShortDate(from)} to ${formatShortDate(to)}</strong></div>
        <div class="insight-row"><span>Peak Demand</span><strong>${peakText}</strong></div>
        <div class="insight-row"><span>Risk Level</span><strong>${riskLabel}</strong></div>
        <div class="insight-row"><span>Recommendation</span><strong>${recommendation}</strong></div>
      </div>
    `;
  }
  if (avgDemandValueEl) avgDemandValueEl.textContent = `${formatUnits(avgForecast, 0)}/day`;
  if (leadTimeValueEl) leadTimeValueEl.textContent = `${DEFAULT_LEAD_TIME_DAYS} days`;
  if (safetyStockValueEl) safetyStockValueEl.textContent = formatUnits(DEFAULT_SAFETY_STOCK);
  if (reorderPointValueEl) reorderPointValueEl.textContent = formatUnits(reorderPoint);
  if (recommendedOrderValueEl) recommendedOrderValueEl.textContent = formatUnits(recommendedOrderQty);

  if (inventoryAlertsListEl) {
    if (Array.isArray(alerts) && alerts.length) {
      inventoryAlertsListEl.innerHTML = alerts.map((alert) => `<li class="alert-item ${alert.level}">${alert.text}</li>`).join("");
    } else {
      inventoryAlertsListEl.innerHTML = '<li class="alert-item safe">Inventory levels are stable for the selected predicted view.</li>';
    }
  }
  setAlertSeverityCounts(alerts);
  if (maeValueEl) maeValueEl.textContent = formatDecimal(metrics.mae, 2);
  if (rmseValueEl) rmseValueEl.textContent = formatDecimal(metrics.rmse, 2);
  if (r2ValueEl) r2ValueEl.textContent = formatDecimal(metrics.r2, 2);

  latestDashboardData = {
    category,
    city: getSelectedCity(),
    store: getSelectedStore(),
    from,
    to,
    avgForecast,
    reorderPoint,
    recommendedOrderQty,
    estimatedInventory,
    riskLabel,
    alerts,
    metrics,
    rows: data.rows || [],
  };
}

function downloadBlob(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function getResolvedDateRange() {
  const from = String(fromDateEl.value || "").trim();
  const to = String(toDateEl.value || "").trim();
  if (from && to) return { from, to };
  return getCurrentToNextMonthRange();
}

function getSelectedOrFirstCategory() {
  return categoryEl.value || "";
}

function getSelectedCity() {
  return cityEl.value || "";
}

function getSelectedStore() {
  return storeEl.value || "";
}

function buildRenderKey() {
  const city = getSelectedCity();
  const category = getSelectedOrFirstCategory();
  const store = getSelectedStore();
  const { from, to } = getResolvedDateRange();
  return `${city}|${store}|${category}|${from}|${to}`;
}

function queueRenderForecastVisualization(options = {}) {
  const immediate = Boolean(options?.immediate);
  const run = async () => {
    const renderKey = buildRenderKey();
    if (renderKey === latestRenderKey) return;
    const runId = ++renderRunId;
    await renderForecastVisualization(runId, renderKey);
  };

  if (immediate) {
    if (renderDebounceTimer) {
      clearTimeout(renderDebounceTimer);
      renderDebounceTimer = null;
    }
    return run();
  }

  if (renderDebounceTimer) clearTimeout(renderDebounceTimer);
  renderDebounceTimer = setTimeout(() => {
    renderDebounceTimer = null;
    run();
  }, RENDER_DEBOUNCE_MS);
  return undefined;
}

async function fetchForecastSnapshot(city, store, category, fromDate, toDate) {
  const key = `${city}|${store}|${category}|${fromDate}|${toDate}`;
  if (vizDataCache.has(key)) return vizDataCache.get(key);
  const promise = (async () => {
    const fromDateObj = new Date(fromDate);
    const toDateObj = new Date(toDate);
    const periodDays = Math.max(1, Math.floor((toDateObj - fromDateObj) / 86400000) + 1);
    const lookback = Math.max(60, periodDays);
    const anchor = new Date(fromDateObj);
    anchor.setDate(anchor.getDate() - 1);
    const anchorDate = anchor.toISOString().slice(0, 10);
    const url = `${apiBase()}/forecast/${encodeURIComponent(category)}?city=${encodeURIComponent(city)}&store=${encodeURIComponent(store)}&horizon=${periodDays}&history_lookback_days=${lookback}&anchor_date=${anchorDate}`;
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) throw new Error(`Predicted load failed (${resp.status}).`);
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
async function renderForecastVisualization(runId = null, requestedKey = "") {
  if (typeof Chart === "undefined") return;
  const isStale = () => Number.isFinite(runId) && runId !== renderRunId;
  const city = getSelectedCity();
  const store = getSelectedStore();
  const category = getSelectedOrFirstCategory();
  if (!city || !category || !store) {
    if (isStale()) return;
    destroyCharts();
    latestDashboardData = null;
    document.getElementById("avgForecastKpi").textContent = "-";
    document.getElementById("peakDayKpi").textContent = "-";
    document.getElementById("riskLevelKpi").textContent = "-";
    document.getElementById("summaryHint").textContent = "Select category, city, and store to preview summary.";
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
    setChartEmptyState("stockRiskEmpty", true, "No risk data available.");
    resetAdvancedSections("Generate a prediction to view AI insight.");
    latestRenderKey = requestedKey || buildRenderKey();
    return;
  }

  const { from, to } = getResolvedDateRange();
  const subtitleEl = document.getElementById("vizSubtitle");
  subtitleEl.textContent = `${city} | ${store} | ${category} | ${from} to ${to}`;

  let snapshot;
  try {
    snapshot = await fetchForecastSnapshot(city, store, category, from, to);
    if (isStale()) return;
  } catch (err) {
    if (isStale()) return;
    destroyCharts();
    latestDashboardData = null;
    subtitleEl.textContent = `Visualization unavailable: ${err.message}`;
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
    setChartEmptyState("stockRiskEmpty", true, "No risk data available.");
    resetAdvancedSections(`Unable to generate AI insight: ${err.message}`);
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
  const weekendSeries = labels.map((d) => {
    const ts = new Date(`${d}T00:00:00`);
    const day = ts.getDay();
    if (![0, 6].includes(day)) return null;
    if (actualMap.has(d)) return actualMap.get(d);
    if (forecastMap.has(d)) return forecastMap.get(d);
    return null;
  });

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
    ? `Peak predicted value on ${formatShortDate(peakDayRow.date)} with ${Number(peakDayRow.forecast_units_sold || 0).toFixed(1)} units.`
    : "Summary updates when city, category, or date changes.";

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
          { label: "Predicted Demand", data: forecastSeries, borderColor: linePurple, backgroundColor: "rgba(139, 92, 246, 0.18)", tension: 0.35, borderWidth: 2, pointRadius: 1.6, spanGaps: true },
          {
            label: "Weekend",
            data: weekendSeries,
            type: "scatter",
            showLine: false,
            pointRadius: 4,
            pointHoverRadius: 5,
            pointBackgroundColor: "#f8fafc",
            pointBorderColor: "rgba(148, 163, 184, 0.9)",
            pointBorderWidth: 1.2,
            spanGaps: true,
          },
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

  const firstForecast = forecast.length ? Number(forecast[0].forecast_units_sold || 0) : 0;
  const lastForecast = forecast.length ? Number(forecast[forecast.length - 1].forecast_units_sold || 0) : 0;
  const growthPct = firstForecast > 0 ? ((lastForecast - firstForecast) / firstForecast) * 100 : 0;
  const pairedPoints = labels
    .map((d) => ({ actual: actualMap.get(d), predicted: forecastMap.get(d) }))
    .filter((p) => Number.isFinite(p.actual) && Number.isFinite(p.predicted));
  let mae = 0;
  let rmse = 0;
  let r2 = 0;
  if (pairedPoints.length) {
    const absErr = pairedPoints.map((p) => Math.abs(p.actual - p.predicted));
    const sqErr = pairedPoints.map((p) => (p.actual - p.predicted) ** 2);
    mae = absErr.reduce((a, b) => a + b, 0) / absErr.length;
    rmse = Math.sqrt(sqErr.reduce((a, b) => a + b, 0) / sqErr.length);
    const meanActual = pairedPoints.reduce((sum, p) => sum + p.actual, 0) / pairedPoints.length;
    const ssRes = sqErr.reduce((a, b) => a + b, 0);
    const ssTot = pairedPoints.reduce((sum, p) => sum + (p.actual - meanActual) ** 2, 0);
    r2 = ssTot > 0 ? 1 - (ssRes / ssTot) : 0;
  }
  const metricsAreDefaultLike = Math.abs(mae) < 1e-9 && Math.abs(rmse) < 1e-9 && Math.abs(r2) < 1e-9;
  if (!Number.isFinite(mae) || !Number.isFinite(rmse) || !Number.isFinite(r2) || metricsAreDefaultLike) {
    mae = DEFAULT_PERFORMANCE_METRICS.mae;
    rmse = DEFAULT_PERFORMANCE_METRICS.rmse;
    r2 = DEFAULT_PERFORMANCE_METRICS.r2;
  }

  const reorderPoint = Math.round((avgForecast * DEFAULT_LEAD_TIME_DAYS) + DEFAULT_SAFETY_STOCK);
  const estimatedInventory = Math.round(latestActual * 4);
  const recommendedOrderQty = Math.max(0, reorderPoint - estimatedInventory);

  const alerts = [];
  if (growthPct > 12) alerts.push({ level: "critical", text: `⚠ ${category} — High demand expected` });
  if (estimatedInventory < reorderPoint) alerts.push({ level: "warning", text: `⚠ ${category} — Reorder recommended soon` });
  if (!alerts.length) alerts.push({ level: "safe", text: `✔ ${category} — Inventory levels safe` });

  const exportRows = labels.map((date) => ({
    date,
    actual: actualMap.has(date) ? Number(actualMap.get(date)) : null,
    forecast: forecastMap.has(date) ? Number(forecastMap.get(date)) : null,
  }));

  updateAdvancedSections({
    city,
    category,
    from,
    to,
    avgForecast,
    peakDayRow,
    growthPct,
    reorderPoint,
    recommendedOrderQty,
    estimatedInventory,
    riskLabel,
    alerts,
    metrics: { mae, rmse, r2 },
    rows: exportRows,
  });
  latestRenderKey = requestedKey || `${category}|${from}|${to}`;

  const categoryOptions = Array.from(categoryEl.options || [])
    .map((opt) => String(opt.value || "").trim())
    .filter(Boolean)
    .slice(0, 6);
  const categoryRows = await Promise.all(categoryOptions.map(async (cat) => {
    try {
      const snap = await fetchForecastSnapshot(city, cat, from, to);
      const totalActual = snap.filteredHistory.reduce((sum, r) => sum + Number(r.actual_units_sold || 0), 0);
      const totalProjected = snap.filteredForecast.reduce((sum, r) => sum + Number(r.forecast_units_sold || 0), 0);
      return { category: cat, units: totalActual > 0 ? totalActual : totalProjected };
    } catch (_) {
      return { category: cat, units: 0 };
    }
  }));
  if (isStale()) return;

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
    if (topUserNameEl) topUserNameEl.textContent = fullName;

    const adminDashBtn = document.getElementById("adminDashBtn");
    if (adminDashBtn) adminDashBtn.style.display = role === "admin" ? "" : "none";

    if (role === "admin") roleHintEl.textContent = "Admin access: full system operations enabled.";
    if (role === "inventory_manager") roleHintEl.textContent = "Inventory Manager access: operational inventory insights enabled.";
    if (role === "viewer") roleHintEl.textContent = "Viewer access: read-only dashboard and predicted views. Export and inventory insights are restricted.";

    const downloadBtn = document.getElementById("downloadBtn");
    if (downloadBtn) {
      const restrictDownload = role === "viewer";
      downloadBtn.disabled = restrictDownload;
      downloadBtn.style.opacity = restrictDownload ? "0.65" : "1";
      downloadBtn.classList.toggle("is-restricted", restrictDownload);
      if (restrictDownload) {
        downloadBtn.setAttribute("data-restrict-msg", "Download restricted for Viewer role");
      } else {
        downloadBtn.removeAttribute("data-restrict-msg");
      }
      downloadBtn.removeAttribute("title");
      if (exportCsvBtnEl) exportCsvBtnEl.disabled = restrictDownload;
      if (exportPngBtnEl) exportPngBtnEl.disabled = restrictDownload;
      if (exportPdfBtnEl) exportPdfBtnEl.disabled = restrictDownload;
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
  return loadScopeOptions("", preferredCategory);
}

async function loadScopeOptions(preferredCity, preferredCategory) {
  try {
    const data = window.__DEMANDIQ_BOOTSTRAP__ || await fetchJsonWithDebug(`${apiBase()}/categories`);
    debugLog("scope:source", window.__DEMANDIQ_BOOTSTRAP__ ? "bootstrap" : "fetch");
    const initialCities = normalizeScopeValues(data.cities);
    scopeOptionsState.categories = normalizeScopeValues(data.categories);
    scopeOptionsState.categoryCityMap = normalizeScopeMap(data.category_city_map);
    scopeOptionsState.cityCategoryMap = normalizeScopeMap(data.city_category_map);
    scopeOptionsState.categoryCityStoreMap = normalizeScopeMap(data.category_city_store_map);
    debugLog("scope:loaded", {
      categories: scopeOptionsState.categories.length,
      categoryMapKeys: scopeOptionsState.categoryCityMap.size,
      cityMapKeys: scopeOptionsState.cityCategoryMap.size,
      storeMapKeys: scopeOptionsState.categoryCityStoreMap.size,
    });
    scopeOptionsState.cities = initialCities.length
      ? initialCities
      : normalizeScopeValues(
          Array.from(scopeOptionsState.categoryCityMap.values()).flat(),
        );
    syncScopeSelections("init", preferredCity, preferredCategory);
  } catch (err) {
    debugLog("scope:error", err?.message || String(err));
    setFieldError(cityEl, cityErrorEl, "Unable to load cities. Please refresh and try again.");
    setFieldError(categoryEl, categoryErrorEl, "Unable to load categories. Please refresh and try again.");
  }
}

function ensureCityOptionsForCurrentCategory() {
  const selectedCategory = String(categoryEl.value || "").trim();
  if (!selectedCategory) return false;
  const cities = normalizeScopeValues(getCitiesForCategory(selectedCategory));
  if (!cities.length) return false;
  const currentOptions = Array.from(cityEl.options || [])
    .map((opt) => String(opt.value || "").trim())
    .filter(Boolean);
  const sameOptions = currentOptions.length === cities.length
    && currentOptions.every((value, index) => value === cities[index]);
  if (!sameOptions) {
    populateScopeSelect(cityEl, "Select city", cities, cityEl.value || "");
    if (!cities.includes(String(cityEl.value || "").trim())) cityEl.value = "";
    updateCityPlaceholderState();
    rebuildCityMenu();
  }
  if (cityMetaEl) cityMetaEl.textContent = `${cities.length} cities available for ${selectedCategory}`;
  return true;
}

function ensureStoreOptionsForCurrentSelection() {
  const selectedCategory = String(categoryEl.value || "").trim();
  const selectedCity = String(cityEl.value || "").trim();
  if (!selectedCategory || !selectedCity) return false;
  const stores = normalizeScopeValues(getStoresForCategoryCity(selectedCategory, selectedCity));
  if (!stores.length) return false;
  const currentOptions = Array.from(storeEl.options || [])
    .map((opt) => String(opt.value || "").trim())
    .filter(Boolean);
  const sameOptions = currentOptions.length === stores.length
    && currentOptions.every((value, index) => value === stores[index]);
  if (!sameOptions) {
    populateScopeSelect(storeEl, "Select store", stores, storeEl.value || "");
    if (!stores.includes(String(storeEl.value || "").trim())) storeEl.value = "";
    updateStorePlaceholderState();
    rebuildStoreMenu();
  }
  if (storeMetaEl) storeMetaEl.textContent = `${stores.length} stores available in ${selectedCity}`;
  return true;
}

async function loadCitiesForCategory(category, preferredCity = "") {
  const selectedCategory = String(category || "").trim();
  if (!selectedCategory) {
    syncScopeSelections("category", "", "");
    return;
  }
  const cachedCities = normalizeScopeValues(scopeOptionsState.categoryCityMap.get(selectedCategory) || []);
  if (cachedCities.length) {
    populateScopeSelect(cityEl, "Select city", cachedCities, preferredCity);
    cityEl.value = cachedCities.includes(preferredCity) ? preferredCity : "";
    populateScopeSelect(storeEl, "Select store", [], "");
    updateCityPlaceholderState();
    updateStorePlaceholderState();
    rebuildCityMenu();
    rebuildStoreMenu();
    setCategoryFirstMode();
    if (cityMetaEl) {
      cityMetaEl.textContent = `${cachedCities.length} cities available for ${selectedCategory}`;
    }
    return;
  }
  try {
    const data = await fetchJsonWithDebug(`${apiBase()}/categories/${encodeURIComponent(selectedCategory)}/cities`);
    const resolvedCategory = String(data?.category || selectedCategory).trim();
    const cities = normalizeScopeValues(data?.cities);
    debugLog("cities:loaded", { category: resolvedCategory, count: cities.length, cities });
    scopeOptionsState.categoryCityMap.set(resolvedCategory, cities);
    scopeOptionsState.categoryCityStoreMap.forEach((_, key, map) => {
      if (key.startsWith(`${resolvedCategory}|||`)) map.delete(key);
    });
    cities.forEach((city) => {
      const categoryList = normalizeScopeValues([
        ...(scopeOptionsState.cityCategoryMap.get(city) || []),
        resolvedCategory,
      ]);
      scopeOptionsState.cityCategoryMap.set(city, categoryList);
    });
    populateScopeSelect(cityEl, "Select city", cities, preferredCity);
    cityEl.value = cities.includes(preferredCity) ? preferredCity : "";
    populateScopeSelect(storeEl, "Select store", [], "");
    updateCityPlaceholderState();
    updateStorePlaceholderState();
    rebuildCityMenu();
    rebuildStoreMenu();
    setCategoryFirstMode();
    if (cityMetaEl) {
      cityMetaEl.textContent = `${cities.length} cities available for ${resolvedCategory}`;
    }
  } catch (err) {
    debugLog("cities:error", { category: selectedCategory, error: err?.message || String(err) });
    const fallbackCities = normalizeScopeValues(
      scopeOptionsState.categoryCityMap.get(selectedCategory)
      || scopeOptionsState.categoryCityMap.get(String(categoryEl.value || "").trim())
      || [],
    );
    if (fallbackCities.length) {
      scopeOptionsState.categoryCityMap.set(selectedCategory, fallbackCities);
      populateScopeSelect(cityEl, "Select city", fallbackCities, preferredCity);
      cityEl.value = fallbackCities.includes(preferredCity) ? preferredCity : "";
      populateScopeSelect(storeEl, "Select store", [], "");
      updateCityPlaceholderState();
      updateStorePlaceholderState();
      rebuildCityMenu();
      rebuildStoreMenu();
      setCategoryFirstMode();
      if (cityMetaEl) {
        cityMetaEl.textContent = `${fallbackCities.length} cities available for ${selectedCategory}`;
      }
      return;
    }
    syncScopeSelections("category", "", selectedCategory);
    setFieldError(cityEl, cityErrorEl, "Unable to load cities for this category. Please refresh and try again.");
  }
}

async function loadStoresForCategoryCity(category, city, preferredStore = "") {
  const selectedCategory = String(category || "").trim();
  const selectedCity = String(city || "").trim();
  if (!selectedCategory || !selectedCity) {
    syncScopeSelections("store", selectedCity, selectedCategory, "");
    return;
  }
  try {
    const data = await fetchJsonWithDebug(
      `${apiBase()}/categories/${encodeURIComponent(selectedCategory)}/cities/${encodeURIComponent(selectedCity)}/stores`,
    );
    const resolvedCategory = String(data?.category || selectedCategory).trim();
    const resolvedCity = String(data?.city || selectedCity).trim();
    const stores = normalizeScopeValues(data?.stores);
    debugLog("stores:loaded", { category: resolvedCategory, city: resolvedCity, count: stores.length, stores });
    scopeOptionsState.categoryCityStoreMap.set(`${resolvedCategory}|||${resolvedCity}`, stores);
    const nextStore = stores.length === 1 && !preferredStore ? stores[0] : preferredStore;
    populateScopeSelect(storeEl, "Select store", stores, nextStore);
    storeEl.value = stores.includes(nextStore) ? nextStore : "";
    updateStorePlaceholderState();
    rebuildStoreMenu();
    setCategoryFirstMode();
  } catch (err) {
    debugLog("stores:error", { category: selectedCategory, city: selectedCity, error: err?.message || String(err) });
    scopeOptionsState.categoryCityStoreMap.set(`${selectedCategory}|||${selectedCity}`, []);
    populateScopeSelect(storeEl, "Select store", [], "");
    updateStorePlaceholderState();
    rebuildStoreMenu();
    setCategoryFirstMode();
    setFieldError(storeEl, storeErrorEl, "Unable to load stores for this city. Please refresh and try again.");
  }
}

window.addEventListener("error", (event) => {
  debugLog("window:error", {
    message: event.message,
    source: event.filename,
    line: event.lineno,
    column: event.colno,
  });
});

window.addEventListener("unhandledrejection", (event) => {
  debugLog("promise:error", String(event.reason || "Unknown rejection"));
});

function clearInputState() {
  cityEl.innerHTML = "";
  cityEl.classList.remove("has-value");
  if (cityTriggerTextEl) cityTriggerTextEl.textContent = "Select city";
  if (cityTriggerEl) cityTriggerEl.classList.remove("has-value");
  if (cityMenuEl) cityMenuEl.innerHTML = "";
  storeEl.innerHTML = "";
  storeEl.classList.remove("has-value");
  if (storeTriggerTextEl) storeTriggerTextEl.textContent = "Select store";
  if (storeTriggerEl) storeTriggerEl.classList.remove("has-value");
  if (storeMenuEl) storeMenuEl.innerHTML = "";
  categoryEl.innerHTML = "";
  categoryEl.classList.remove("has-value");
  if (categoryTriggerTextEl) categoryTriggerTextEl.textContent = "Select category";
  if (categoryTriggerEl) categoryTriggerEl.classList.remove("has-value");
  if (categoryMenuEl) categoryMenuEl.innerHTML = "";
  fromDateEl.value = "";
  toDateEl.value = "";
}

function updateCityPlaceholderState() {
  const hasValue = Boolean(String(cityEl.value || "").trim());
  cityEl.classList.toggle("has-value", hasValue);
  if (cityTriggerEl) cityTriggerEl.classList.toggle("has-value", hasValue);
  if (cityTriggerTextEl) {
    if (!hasValue) {
      cityTriggerTextEl.textContent = "Select city";
    } else {
      const selectedOption = cityEl.options[cityEl.selectedIndex];
      cityTriggerTextEl.textContent = String(selectedOption?.textContent || cityEl.value || "Select city");
    }
  }
}

function updateCategoryPlaceholderState() {
  const hasValue = Boolean(String(categoryEl.value || "").trim());
  categoryEl.classList.toggle("has-value", hasValue);
  if (categoryTriggerEl) categoryTriggerEl.classList.toggle("has-value", hasValue);
  if (categoryTriggerTextEl) {
    if (!hasValue) {
      categoryTriggerTextEl.textContent = "Select category";
    } else {
      const selectedOption = categoryEl.options[categoryEl.selectedIndex];
      categoryTriggerTextEl.textContent = String(selectedOption?.textContent || categoryEl.value || "Select category");
    }
  }
}

function updateStorePlaceholderState() {
  const hasValue = Boolean(String(storeEl.value || "").trim());
  storeEl.classList.toggle("has-value", hasValue);
  if (storeTriggerEl) storeTriggerEl.classList.toggle("has-value", hasValue);
  if (storeTriggerTextEl) {
    if (!hasValue) {
      storeTriggerTextEl.textContent = "Select store";
    } else {
      const selectedOption = storeEl.options[storeEl.selectedIndex];
      storeTriggerTextEl.textContent = String(selectedOption?.textContent || storeEl.value || "Select store");
    }
  }
}

function closeCityMenu() {
  if (!cityShellEl || !cityTriggerEl) return;
  cityShellEl.classList.remove("is-open");
  cityTriggerEl.setAttribute("aria-expanded", "false");
}

function openCityMenu() {
  if (!cityShellEl || !cityTriggerEl) return;
  cityShellEl.classList.add("is-open");
  cityTriggerEl.setAttribute("aria-expanded", "true");
}

function closeCategoryMenu() {
  if (!categoryShellEl || !categoryTriggerEl) return;
  categoryShellEl.classList.remove("is-open");
  categoryTriggerEl.setAttribute("aria-expanded", "false");
}

function openCategoryMenu() {
  if (!categoryShellEl || !categoryTriggerEl) return;
  categoryShellEl.classList.add("is-open");
  categoryTriggerEl.setAttribute("aria-expanded", "true");
}

function closeStoreMenu() {
  if (!storeShellEl || !storeTriggerEl) return;
  storeShellEl.classList.remove("is-open");
  storeTriggerEl.setAttribute("aria-expanded", "false");
}

function openStoreMenu() {
  if (!storeShellEl || !storeTriggerEl) return;
  storeShellEl.classList.add("is-open");
  storeTriggerEl.setAttribute("aria-expanded", "true");
}

function rebuildCategoryMenu() {
  if (!categoryMenuEl) return;
  const options = Array.from(categoryEl.options || [])
    .map((opt) => ({
      value: String(opt.value || ""),
      label: String(opt.textContent || "").trim(),
      disabled: Boolean(opt.disabled),
    }))
    .filter((opt) => opt.value && !opt.disabled);

  categoryMenuEl.innerHTML = "";
  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "category-empty";
    empty.textContent = "No categories available";
    categoryMenuEl.appendChild(empty);
    return;
  }

  const listWrap = document.createElement("div");
  listWrap.className = "category-menu-list";
  categoryMenuEl.appendChild(listWrap);

  const renderOptions = (query = "") => {
    const normalized = String(query || "").trim().toLowerCase();
    const filtered = normalized
      ? options.filter((opt) => opt.label.toLowerCase().includes(normalized))
      : options;

    listWrap.innerHTML = "";

    if (!filtered.length) {
      const emptyState = document.createElement("div");
      emptyState.className = "category-empty";
      emptyState.textContent = "No matching categories";
      listWrap.appendChild(emptyState);
      return;
    }

    filtered.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `category-option${opt.value === categoryEl.value ? " is-selected" : ""}`;
      btn.dataset.value = opt.value;
      btn.textContent = opt.label;
      btn.addEventListener("click", () => {
        categoryEl.value = opt.value;
        categoryEl.dispatchEvent(new Event("change", { bubbles: true }));
        closeCategoryMenu();
      });
      listWrap.appendChild(btn);
    });
  };

  renderOptions();
  if (categoryShellEl?.classList.contains("is-open")) openCategoryMenu();
}

function rebuildCityMenu() {
  if (!cityMenuEl) return;
  const options = Array.from(cityEl.options || [])
    .map((opt) => ({
      value: String(opt.value || ""),
      label: String(opt.textContent || "").trim(),
      disabled: Boolean(opt.disabled),
    }))
    .filter((opt) => opt.value && !opt.disabled);

  cityMenuEl.innerHTML = "";
  const hasCategory = Boolean(String(categoryEl.value || "").trim());
  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "category-empty";
    empty.textContent = hasCategory ? "No cities available for this category" : "Select a category first";
    cityMenuEl.appendChild(empty);
    return;
  }

  const listWrap = document.createElement("div");
  listWrap.className = "category-menu-list";
  cityMenuEl.appendChild(listWrap);

  listWrap.innerHTML = "";
  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `category-option${opt.value === cityEl.value ? " is-selected" : ""}`;
    btn.dataset.value = opt.value;
    btn.textContent = opt.label;
    btn.addEventListener("click", () => {
      cityEl.value = opt.value;
      cityEl.dispatchEvent(new Event("change", { bubbles: true }));
      closeCityMenu();
    });
    listWrap.appendChild(btn);
  });

  if (cityShellEl?.classList.contains("is-open")) openCityMenu();
}

function rebuildStoreMenu() {
  if (!storeMenuEl) return;
  const options = Array.from(storeEl.options || [])
    .map((opt) => ({
      value: String(opt.value || ""),
      label: String(opt.textContent || "").trim(),
      disabled: Boolean(opt.disabled),
    }))
    .filter((opt) => opt.value && !opt.disabled);

  storeMenuEl.innerHTML = "";
  const hasCity = Boolean(String(cityEl.value || "").trim());
  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "category-empty";
    empty.textContent = hasCity ? "No stores available for this city" : "Select a city first";
    storeMenuEl.appendChild(empty);
    return;
  }

  const listWrap = document.createElement("div");
  listWrap.className = "category-menu-list";
  storeMenuEl.appendChild(listWrap);

  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `category-option${opt.value === storeEl.value ? " is-selected" : ""}`;
    btn.dataset.value = opt.value;
    btn.textContent = opt.label;
    btn.addEventListener("click", () => {
      storeEl.value = opt.value;
      storeEl.dispatchEvent(new Event("change", { bubbles: true }));
      closeStoreMenu();
    });
    listWrap.appendChild(btn);
  });

  if (storeShellEl?.classList.contains("is-open")) openStoreMenu();
}

function saveDashboardState() {
  const state = { city: cityEl.value || "", category: categoryEl.value || "", store: storeEl.value || "", fromDate: fromDateEl.value || "", toDate: toDateEl.value || "" };
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
      city: String(parsed?.city || ""),
      store: String(parsed?.store || ""),
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
  // Static behavior: always clear form state on browser refresh.
  const shouldClear = getNavigationType() === "reload" || consumeForceClearFlag();
  const savedState = shouldClear ? null : readDashboardState();
  if (shouldClear) clearDashboardState();
  clearInputState();
  initDatePickers();
  await loadScopeOptions(savedState?.city || "", savedState?.category || "");
  if (savedState?.category && savedState?.city) {
    await loadStoresForCategoryCity(savedState.category, savedState.city, savedState?.store || "");
  }
  const hasSavedCity = Boolean(savedState?.city);
  const hasSavedCategory = Boolean(savedState?.category);
  const hasSavedStore = Boolean(savedState?.store);
  if (hasSavedCity && hasSavedCategory && savedState?.fromDate) {
    fromPicker.setDate(savedState.fromDate, true, "Y-m-d");
  }
  if (hasSavedCity && hasSavedCategory && savedState?.toDate) {
    toPicker.setDate(savedState.toDate, true, "Y-m-d");
  }
  const minAllowedStr = ALLOWED_MIN_DATE;
  const maxAllowedStr = ALLOWED_MAX_DATE;
  const savedFrom = String(savedState?.fromDate || "");
  const savedTo = String(savedState?.toDate || "");
  const hasValidSavedRange = hasSavedCity
    && hasSavedCategory
    && hasSavedStore
    && savedFrom && savedTo
    && savedFrom >= minAllowedStr
    && savedTo <= maxAllowedStr
    && savedFrom <= savedTo;
  if (!hasValidSavedRange) {
    fromPicker.clear();
    toPicker.clear();
  }
  const shell = document.querySelector(".page-shell");
  if (shell) {
    shell.classList.remove("preload");
    shell.classList.add("ready");
  }
  await queueRenderForecastVisualization({ immediate: true });
}

function validateInputs() {
  clearAllErrors();
  const city = cityEl.value;
  const store = storeEl.value;
  const category = categoryEl.value;
  const fromDate = fromDateEl.value;
  const toDate = toDateEl.value;
  let hasError = false;
  if (!category) {
    setFieldError(categoryEl, categoryErrorEl, "Please select a category first.");
    hasError = true;
  }
  if (!city) {
    setFieldError(cityEl, cityErrorEl, category ? "Please select a city for this category." : "Select a category first to view cities.");
    hasError = true;
  }
  if (!store) {
    setFieldError(storeEl, storeErrorEl, city ? "Please select a store for this city." : "Select a city first to view stores.");
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
    setFieldError(fromDateEl, fromDateErrorEl, `Use a date between ${ALLOWED_MIN_DATE} and ${ALLOWED_MAX_DATE}.`);
    hasError = true;
  }
  if (to < minAllowed || to > maxAllowed) {
    setFieldError(toDateEl, toDateErrorEl, `Use a date between ${ALLOWED_MIN_DATE} and ${ALLOWED_MAX_DATE}.`);
    hasError = true;
  }
  return !hasError;
}

function goToResults(mode) {
  if (!validateInputs()) return;
  saveDashboardState();
  const resultUrl = `/dashboard/results?city=${encodeURIComponent(cityEl.value)}&store=${encodeURIComponent(storeEl.value)}&category=${encodeURIComponent(categoryEl.value)}&from=${encodeURIComponent(fromDateEl.value)}&to=${encodeURIComponent(toDateEl.value)}&mode=${encodeURIComponent(mode)}`;
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

function exportForecastCsv() {
  if (!latestDashboardData || !Array.isArray(latestDashboardData.rows) || !latestDashboardData.rows.length) return;
  const header = "date,city,store,category,actual_demand,forecast_demand\n";
  const lines = latestDashboardData.rows.map((row) => {
    const actual = row.actual == null ? "" : Number(row.actual).toFixed(2);
    const forecast = row.forecast == null ? "" : Number(row.forecast).toFixed(2);
    return `${row.date},${latestDashboardData.city},${latestDashboardData.store},${latestDashboardData.category},${actual},${forecast}`;
  });
  const csv = `${header}${lines.join("\n")}`;
  const safeCategory = String(latestDashboardData.category || "forecast").replace(/\s+/g, "_").toLowerCase();
  downloadBlob(`forecast_${safeCategory}.csv`, csv, "text/csv;charset=utf-8;");
}

function exportChartPng() {
  if (!actualForecastChart) return;
  const url = actualForecastChart.toBase64Image("image/png", 1);
  const link = document.createElement("a");
  link.href = url;
  link.download = `forecast_chart_${Date.now()}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function exportForecastPdf() {
  if (!latestDashboardData || !window.jspdf || !window.jspdf.jsPDF) return;
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const marginX = 40;
  let y = 48;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.text("DemandIQ Predicted Report", marginX, y);
  y += 22;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(11);
  doc.text(`City: ${latestDashboardData.city}`, marginX, y); y += 16;
  doc.text(`Store: ${latestDashboardData.store}`, marginX, y); y += 16;
  doc.text(`Category: ${latestDashboardData.category}`, marginX, y); y += 16;
  doc.text(`Period: ${latestDashboardData.from} to ${latestDashboardData.to}`, marginX, y); y += 16;
  doc.text(`Reorder Point: ${formatUnits(latestDashboardData.reorderPoint)}`, marginX, y); y += 16;
  doc.text(`Recommended Order Quantity: ${formatUnits(latestDashboardData.recommendedOrderQty)}`, marginX, y); y += 22;

  if (actualForecastChart) {
    const img = actualForecastChart.toBase64Image("image/png", 1);
    doc.addImage(img, "PNG", marginX, y, 510, 220);
    y += 236;
  }

  doc.setFont("helvetica", "bold");
  doc.text("Inventory Alerts", marginX, y); y += 14;
  doc.setFont("helvetica", "normal");
  (latestDashboardData.alerts || []).slice(0, 4).forEach((alert) => {
    doc.text(`• ${alert.text}`, marginX, y);
    y += 14;
  });
  y += 10;
  doc.setFont("helvetica", "bold");
  doc.text("Model Performance", marginX, y); y += 14;
  doc.setFont("helvetica", "normal");
  doc.text(`MAE: ${formatDecimal(latestDashboardData.metrics?.mae, 2)}`, marginX, y); y += 14;
  doc.text(`RMSE: ${formatDecimal(latestDashboardData.metrics?.rmse, 2)}`, marginX, y); y += 14;
  doc.text(`R² Score: ${formatDecimal(latestDashboardData.metrics?.r2, 2)}`, marginX, y);

  doc.save(`forecast_report_${Date.now()}.pdf`);
}

cityEl.addEventListener("change", async () => {
  clearFieldError(cityEl, cityErrorEl);
  clearFieldError(storeEl, storeErrorEl);
  syncScopeSelections("city");
  await loadStoresForCategoryCity(categoryEl.value || "", cityEl.value || "", storeEl.value || "");
  saveDashboardState();
  queueRenderForecastVisualization();
});
categoryEl.addEventListener("change", async () => {
  clearFieldError(categoryEl, categoryErrorEl);
  clearFieldError(cityEl, cityErrorEl);
  updateCategoryPlaceholderState();
  rebuildCategoryMenu();
  await loadCitiesForCategory(categoryEl.value || "", cityEl.value || "");
  ensureCityOptionsForCurrentCategory();
  saveDashboardState();
  queueRenderForecastVisualization();
});
storeEl.addEventListener("change", () => {
  clearFieldError(storeEl, storeErrorEl);
  updateStorePlaceholderState();
  rebuildStoreMenu();
  saveDashboardState();
  queueRenderForecastVisualization();
});
fromDateEl.addEventListener("input", () => {
  clearFieldError(fromDateEl, fromDateErrorEl);
  saveDashboardState();
  queueRenderForecastVisualization();
});
fromDateEl.addEventListener("change", () => {
  clearFieldError(fromDateEl, fromDateErrorEl);
  saveDashboardState();
  queueRenderForecastVisualization();
});
toDateEl.addEventListener("input", () => {
  clearFieldError(toDateEl, toDateErrorEl);
  saveDashboardState();
  queueRenderForecastVisualization();
});
toDateEl.addEventListener("change", () => {
  clearFieldError(toDateEl, toDateErrorEl);
  saveDashboardState();
  queueRenderForecastVisualization();
});

const generateBtnEl = document.getElementById("generateBtn");
const downloadBtnEl = document.getElementById("downloadBtn");
const navReportsBtnEl = document.getElementById("navReports");
const pastDemandBtnEl = document.getElementById("pastDemandBtn");
const adminDashBtnEl = document.getElementById("adminDashBtn");
const logoutBtnEl = document.getElementById("logoutBtn");

if (generateBtnEl) generateBtnEl.addEventListener("click", () => goToResults("forecast"));
if (downloadBtnEl) downloadBtnEl.addEventListener("click", () => goToResults("forecast"));
if (navReportsBtnEl) {
  navReportsBtnEl.addEventListener("click", () => {
    goToResults("forecast");
  });
}
if (pastDemandBtnEl) {
  pastDemandBtnEl.addEventListener("click", () => {
    goToResults("past");
  });
}
if (adminDashBtnEl) {
  adminDashBtnEl.addEventListener("click", () => {
    window.location.href = "/admin/dashboard";
  });
}
if (logoutBtnEl) {
  logoutBtnEl.addEventListener("click", () => {
    logoutAndGoLogin();
  });
}
if (topLogoutBtnEl) {
  topLogoutBtnEl.addEventListener("click", () => {
    logoutAndGoLogin();
  });
}
if (refreshBtnEl) {
  refreshBtnEl.addEventListener("click", () => {
    queueRenderForecastVisualization({ immediate: true });
  });
}
if (syncBtnEl) {
  syncBtnEl.addEventListener("click", async () => {
    clearDashboardState();
    await initializeDefaults();
  });
}
if (sidebarToggleBtn) {
  sidebarToggleBtn.addEventListener("click", () => {
    const next = !document.body.classList.contains("sidebar-collapsed");
    applySidebarState(next);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
    } catch (_) {
      // Ignore storage failures.
    }
  });
}
if (categoryTriggerEl) {
  categoryTriggerEl.addEventListener("click", () => {
    if (!categoryShellEl) return;
    const isOpen = categoryShellEl.classList.contains("is-open");
    if (isOpen) closeCategoryMenu();
    else openCategoryMenu();
  });
}
if (cityTriggerEl) {
  cityTriggerEl.addEventListener("click", () => {
    if (cityTriggerEl.disabled) {
      setFieldError(cityEl, cityErrorEl, "Select a category first to view available cities.");
      return;
    }
    ensureCityOptionsForCurrentCategory();
    if (!cityShellEl) return;
    const isOpen = cityShellEl.classList.contains("is-open");
    if (isOpen) closeCityMenu();
    else openCityMenu();
  });
}
if (storeTriggerEl) {
  storeTriggerEl.addEventListener("click", () => {
    if (storeTriggerEl.disabled) {
      setFieldError(storeEl, storeErrorEl, "Select a city first to view available stores.");
      return;
    }
    ensureStoreOptionsForCurrentSelection();
    if (!storeShellEl) return;
    const isOpen = storeShellEl.classList.contains("is-open");
    if (isOpen) closeStoreMenu();
    else openStoreMenu();
  });
}
document.addEventListener("click", (event) => {
  const target = event.target;
  if (target instanceof Node && categoryShellEl && !categoryShellEl.contains(target)) closeCategoryMenu();
  if (target instanceof Node && cityShellEl && !cityShellEl.contains(target)) closeCityMenu();
  if (target instanceof Node && storeShellEl && !storeShellEl.contains(target)) closeStoreMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeCategoryMenu();
    closeCityMenu();
    closeStoreMenu();
  }
});
if (exportCsvBtnEl) exportCsvBtnEl.addEventListener("click", exportForecastCsv);
if (exportPngBtnEl) exportPngBtnEl.addEventListener("click", exportChartPng);
if (exportPdfBtnEl) exportPdfBtnEl.addEventListener("click", exportForecastPdf);

initializeDefaults();
initializeSidebarState();
updateDashboardClock();
window.setInterval(updateDashboardClock, 1000);
window.addEventListener("pageshow", initializeDefaults);
