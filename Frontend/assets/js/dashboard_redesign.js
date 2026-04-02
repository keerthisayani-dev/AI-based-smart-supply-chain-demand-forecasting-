
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
const liveModeStatusEl = document.getElementById("liveModeStatus");
const liveClearBtnEl = document.getElementById("liveClearBtn");
const liveStartBtnEl = document.getElementById("liveStartBtn");
const liveStopBtnEl = document.getElementById("liveStopBtn");
const liveTickBtnEl = document.getElementById("liveTickBtn");
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
const cityStoreSummaryMetaEl = document.getElementById("cityStoreSummaryMeta");
const cityStoreSummaryEmptyEl = document.getElementById("cityStoreSummaryEmpty");
const cityStoreSummaryWrapEl = document.getElementById("cityStoreSummaryWrap");
const cityStoreSummaryBodyEl = document.getElementById("cityStoreSummaryBody");

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
let dailyTrendChart = null;
let categoryComparisonChart = null;
let stockRiskGaugeChart = null;
const vizDataCache = new Map();
let latestDashboardData = null;
let renderDebounceTimer = null;
let renderRunId = 0;
let latestRenderKey = "";
let cityStoreSummaryRunId = 0;
let currentUserRole = "";
let liveStatusPollTimer = null;
let liveAutoRefreshTimer = null;
let liveControlBusy = false;
let lastAppliedLiveDate = "";
let dashboardInitialized = false;
const LIVE_REFRESH_INTERVAL_MS = 2000;
const LIVE_SIMULATION_INTERVAL_SECONDS = 1;
let liveSimulationState = {
  running: false,
  tick_count: 0,
  interval_seconds: LIVE_SIMULATION_INTERVAL_SECONDS,
  latest_data_date: "",
  live_dataset_rows: 0,
  last_error: "",
};
const scopeOptionsState = {
  cities: [],
  categories: [],
  categoryCityMap: new Map(),
  cityCategoryMap: new Map(),
  categoryCityStoreMap: new Map(),
  cityStoreMap: new Map(),
  cityStoreCategoryMap: new Map(),
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

async function fetchJson(url, options = {}) {
  const resp = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
  });
  const text = await resp.text();
  if (!resp.ok) throw new Error(text || `HTTP ${resp.status}`);
  try {
    return JSON.parse(text);
  } catch (_) {
    throw new Error(`Invalid JSON from ${url}`);
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

function formatMaybeWhole(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  const rounded = Math.round(num);
  if (Math.abs(num - rounded) < 1e-9) {
    return rounded.toLocaleString("en-US");
  }
  return num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
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

function getStoresForCity(city) {
  const key = String(city || "").trim();
  if (!key) return [];
  if (!scopeOptionsState.cityStoreMap.size) return [];
  return getScopeMapValues(scopeOptionsState.cityStoreMap, key);
}

function getStoresForCategoryCity(category, city) {
  const categoryKey = String(category || "").trim();
  const cityKey = String(city || "").trim();
  if (!categoryKey || !cityKey) return [];
  return getScopeMapValues(scopeOptionsState.categoryCityStoreMap, `${categoryKey}|||${cityKey}`);
}

function getCategoriesForCityStore(city, store) {
  const cityKey = String(city || "").trim();
  const storeKey = String(store || "").trim();
  if (!cityKey || !storeKey) return [];
  return getScopeMapValues(scopeOptionsState.cityStoreCategoryMap, `${cityKey}|||${storeKey}`);
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
  cityEl.disabled = false;
  if (cityTriggerEl) {
    cityTriggerEl.disabled = false;
    cityTriggerEl.setAttribute("aria-disabled", "false");
    cityTriggerEl.title = "";
  }
  if (cityShellEl) cityShellEl.classList.remove("is-disabled");
  const hasCity = Boolean(String(cityEl.value || "").trim());
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
  const hasStore = hasCity && Boolean(String(storeEl.value || "").trim());
  categoryEl.disabled = !hasStore;
  if (categoryTriggerEl) {
    categoryTriggerEl.disabled = !hasStore;
    categoryTriggerEl.setAttribute("aria-disabled", hasStore ? "false" : "true");
    categoryTriggerEl.title = hasStore ? "" : "Select a store first";
  }
  if (categoryShellEl) categoryShellEl.classList.toggle("is-disabled", !hasStore);
  if (!hasStore) {
    categoryEl.value = "";
    updateCategoryPlaceholderState();
  }
  if (cityMetaEl) {
    cityMetaEl.textContent = scopeOptionsState.cities.length
      ? `${scopeOptionsState.cities.length} cities available in the live scope`
      : "No cities available right now";
  }
  if (storeMetaEl) {
    const selectedCity = String(cityEl.value || "").trim();
    if (!selectedCity) {
      storeMetaEl.textContent = "Select a city to view available stores.";
    } else {
      const stores = getStoresForCity(selectedCity);
      storeMetaEl.textContent = stores.length
        ? `${stores.length} stores available in ${selectedCity}`
        : `No stores available in ${selectedCity}`;
    }
  }
  if (categoryMetaEl) {
    const selectedCity = String(cityEl.value || "").trim();
    const selectedStore = String(storeEl.value || "").trim();
    if (!selectedCity) {
      categoryMetaEl.textContent = "Select a city first, then choose a store.";
    } else if (!selectedStore) {
      categoryMetaEl.textContent = "Select a store to view available categories.";
    } else {
      const categories = getCategoriesForCityStore(selectedCity, selectedStore);
      categoryMetaEl.textContent = categories.length
        ? `${categories.length} categories available for ${selectedStore}`
        : `No categories available for ${selectedStore}`;
    }
  }
}

function syncScopeSelections(source = "init", preferredCity = "", preferredCategory = "", preferredStore = "") {
  let selectedCity = String(preferredCity || cityEl.value || "").trim();
  let selectedCategory = String(preferredCategory || categoryEl.value || "").trim();
  let selectedStore = String(preferredStore || storeEl.value || "").trim();

  let cityValues = [...scopeOptionsState.cities];
  if (selectedCity && !cityValues.includes(selectedCity)) selectedCity = "";

  let storeValues = selectedCity ? getStoresForCity(selectedCity) : [];
  if (selectedStore && !storeValues.includes(selectedStore)) selectedStore = "";

  let categoryValues = [];
  if (selectedCity && selectedStore) {
    categoryValues = getCategoriesForCityStore(selectedCity, selectedStore);
    if (!categoryValues.length) {
      categoryValues = getCategoriesForCity(selectedCity);
    }
  } else if (selectedCity) {
    categoryValues = getCategoriesForCity(selectedCity);
  }
  if (selectedCategory && !categoryValues.includes(selectedCategory)) selectedCategory = "";
  if (selectedCategory && selectedCity) {
    const validStoresForCategory = getStoresForCategoryCity(selectedCategory, selectedCity);
    if (validStoresForCategory.length && selectedStore && !validStoresForCategory.includes(selectedStore)) {
      selectedStore = "";
    }
    storeValues = selectedCategory
      ? (validStoresForCategory.length ? intersectScopeValues(storeValues, validStoresForCategory) : storeValues)
      : storeValues;
  }

  populateScopeSelect(cityEl, "Select city", cityValues, selectedCity);
  populateScopeSelect(storeEl, "Select store", storeValues, selectedStore);
  populateScopeSelect(categoryEl, "Select category", categoryValues, selectedCategory);
  updateCityPlaceholderState();
  updateStorePlaceholderState();
  updateCategoryPlaceholderState();
  rebuildCityMenu();
  rebuildStoreMenu();
  rebuildCategoryMenu();
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
  const liveMode = liveSimulationState.running ? "live" : "manual";
  return `${city}|${store}|${category}|${from}|${to}|${liveMode}`;
}

function setCityStoreSummaryState({ message = "", rows = [], selectedStore = "" } = {}) {
  if (cityStoreSummaryMetaEl && message) cityStoreSummaryMetaEl.textContent = message;
  if (!cityStoreSummaryBodyEl || !cityStoreSummaryWrapEl || !cityStoreSummaryEmptyEl) return;

  if (!rows.length) {
    cityStoreSummaryBodyEl.innerHTML = "";
    cityStoreSummaryWrapEl.classList.add("is-hidden");
    cityStoreSummaryEmptyEl.classList.remove("is-hidden");
    return;
  }

  cityStoreSummaryEmptyEl.classList.add("is-hidden");
  cityStoreSummaryWrapEl.classList.remove("is-hidden");
  cityStoreSummaryBodyEl.innerHTML = rows.map((row) => {
    const isSelected = selectedStore && String(row.store || "").trim() === String(selectedStore || "").trim();
    return `
      <tr class="${isSelected ? "is-selected" : ""}">
        <td>${row.store || "-"}</td>
        <td>${row.store_id || "-"}</td>
        <td>${row.latest_date || "-"}</td>
        <td>${formatMaybeWhole(row.inventory_level)}</td>
        <td>${formatMaybeWhole(row.units_sold)}</td>
        <td>${formatMaybeWhole(row.units_ordered)}</td>
        <td>${formatMaybeWhole(row.avg_demand_forecast)}</td>
        <td>${formatMaybeWhole(row.price)}</td>
        <td>${formatMaybeWhole(row.discount)}</td>
      </tr>
    `;
  }).join("");
}

async function renderCityStoreSummary() {
  const runId = ++cityStoreSummaryRunId;
  const city = getSelectedCity();
  const category = getSelectedOrFirstCategory();
  const store = getSelectedStore();
  const { from, to } = getResolvedDateRange();

  if (!city) {
    setCityStoreSummaryState({
      message: "Select a city to view all stores, quantities, units sold, and demand signals.",
      rows: [],
      selectedStore: "",
    });
    return;
  }

  const qs = new URLSearchParams();
  if (category) qs.set("category", category);
  if (from) qs.set("from_date", from);
  if (to) qs.set("to_date", to);

  try {
    const resp = await fetch(`${apiBase()}/cities/${encodeURIComponent(city)}/store-summary?${qs.toString()}`, {
      cache: "no-store",
    });
    if (!resp.ok) throw new Error(`Store summary load failed (${resp.status}).`);
    const data = await resp.json();
    if (runId !== cityStoreSummaryRunId) return;
    const rows = Array.isArray(data.stores) ? data.stores : [];
    const scopedCategory = String(data.category || "").trim();
    const rangeText = from && to ? `${from} to ${to}` : "current range";
    const summaryText = scopedCategory
      ? `${rows.length} stores in ${city} for ${scopedCategory} during ${rangeText}`
      : `${rows.length} stores in ${city} during ${rangeText}`;
    setCityStoreSummaryState({
      message: rows.length ? summaryText : `No store summary rows available for ${city}.`,
      rows,
      selectedStore: store,
    });
  } catch (err) {
    if (runId !== cityStoreSummaryRunId) return;
    setCityStoreSummaryState({
      message: `Unable to load city store summary: ${err.message}`,
      rows: [],
      selectedStore: "",
    });
  }
}

function queueRenderForecastVisualization(options = {}) {
  const immediate = Boolean(options?.immediate);
  const force = Boolean(options?.force);
  const run = async () => {
    const renderKey = buildRenderKey();
    if (!force && renderKey === latestRenderKey) return;
    const runId = ++renderRunId;
    await renderForecastVisualization(runId, renderKey, options);
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

async function fetchForecastSnapshot(city, store, category, fromDate, toDate, options = {}) {
  const liveMode = Boolean(options?.liveMode);
  const forceRefresh = Boolean(options?.forceRefresh);
  const key = `${city}|${store}|${category}|${fromDate}|${toDate}|${liveMode ? "live" : "manual"}`;
  if (forceRefresh) vizDataCache.delete(key);
  if (vizDataCache.has(key)) return vizDataCache.get(key);
  const promise = (async () => {
    const fromDateObj = new Date(fromDate);
    const toDateObj = new Date(toDate);
    const periodDays = Math.max(1, Math.floor((toDateObj - fromDateObj) / 86400000) + 1);
    const lookback = Math.max(60, periodDays);
    const anchor = new Date(fromDateObj);
    anchor.setDate(anchor.getDate() - 1);
    const anchorDate = anchor.toISOString().slice(0, 10);
    const url = liveMode
      ? `${apiBase()}/forecast/${encodeURIComponent(category)}?city=${encodeURIComponent(city)}&store=${encodeURIComponent(store)}&horizon=${periodDays}&history_lookback_days=${Math.max(60, periodDays * 2)}`
      : `${apiBase()}/forecast/${encodeURIComponent(category)}?city=${encodeURIComponent(city)}&store=${encodeURIComponent(store)}&horizon=${periodDays}&history_lookback_days=${lookback}&anchor_date=${anchorDate}`;
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) throw new Error(`Predicted load failed (${resp.status}).`);
    const data = await resp.json();
    const history = Array.isArray(data.history) ? data.history : [];
    const forecast = Array.isArray(data.forecast) ? data.forecast : [];
    if (liveMode) {
      return {
        filteredHistory: history.slice(-periodDays),
        filteredForecast: forecast.slice(0, periodDays),
      };
    }
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
  if (dailyTrendChart) dailyTrendChart.destroy();
  if (categoryComparisonChart) categoryComparisonChart.destroy();
  if (stockRiskGaugeChart) stockRiskGaugeChart.destroy();
  actualForecastChart = null;
  dailyTrendChart = null;
  categoryComparisonChart = null;
  stockRiskGaugeChart = null;
}
async function renderForecastVisualization(runId = null, requestedKey = "", options = {}) {
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
    document.getElementById("trendWindowValue").textContent = "-";
    document.getElementById("trendPeakValue").textContent = "-";
    document.getElementById("trendDirectionValue").textContent = "-";
    document.getElementById("summaryHint").textContent = "Select city, store, category, and date range to preview summary.";
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("dailyTrendEmpty", true, "No daily trend data available.");
    setChartEmptyState("categoryComparisonEmpty", true, "No category comparison data.");
    setChartEmptyState("stockRiskEmpty", true, "No risk data available.");
    resetAdvancedSections("Generate a prediction to view AI insight.");
    latestRenderKey = requestedKey || buildRenderKey();
    return;
  }

  const { from, to } = getResolvedDateRange();
  const subtitleEl = document.getElementById("vizSubtitle");
  const liveMode = Boolean(options?.liveMode ?? liveSimulationState.running);
  subtitleEl.textContent = liveMode
    ? `${city} | ${store} | ${category} | live rolling window`
    : `${city} | ${store} | ${category} | ${from} to ${to}`;

  let snapshot;
  try {
    snapshot = await fetchForecastSnapshot(city, store, category, from, to, {
      liveMode,
      forceRefresh: liveMode,
    });
    if (isStale()) return;
  } catch (err) {
    if (isStale()) return;
    destroyCharts();
    latestDashboardData = null;
    subtitleEl.textContent = `Visualization unavailable: ${err.message}`;
    setChartEmptyState("actualForecastEmpty", true, "No chart data available.");
    setChartEmptyState("dailyTrendEmpty", true, "No daily trend data available.");
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
  const trendBaseSeries = labels.map((d) => {
    if (actualMap.has(d)) return Number(actualMap.get(d) || 0);
    if (forecastMap.has(d)) return Number(forecastMap.get(d) || 0);
    return null;
  });
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

  const rollingTrendSeries = trendBaseSeries.map((_, idx) => {
    const window = trendBaseSeries.slice(Math.max(0, idx - 6), idx + 1).filter((v) => Number.isFinite(v));
    if (!window.length) return null;
    const avg = window.reduce((sum, value) => sum + Number(value || 0), 0) / window.length;
    return Number(avg.toFixed(2));
  });
  const trendPoints = labels
    .map((date, idx) => ({ date, value: trendBaseSeries[idx] }))
    .filter((point) => Number.isFinite(point.value));
  const peakTrendPoint = trendPoints.reduce((best, point) => {
    if (!best) return point;
    return point.value > best.value ? point : best;
  }, null);
  const trendStart = trendPoints.length ? Number(trendPoints[0].value || 0) : 0;
  const trendEnd = trendPoints.length ? Number(trendPoints[trendPoints.length - 1].value || 0) : 0;
  let trendDirection = "Stable";
  if (trendPoints.length >= 2) {
    if (trendEnd > trendStart) trendDirection = "Rising";
    else if (trendEnd < trendStart) trendDirection = "Falling";
  }
  document.getElementById("trendWindowValue").textContent = labels.length ? `${labels.length} days` : "-";
  document.getElementById("trendPeakValue").textContent = peakTrendPoint?.date ? formatShortDate(peakTrendPoint.date) : "-";
  document.getElementById("trendDirectionValue").textContent = trendDirection;

  const dailyTrendCtx = document.getElementById("dailyTrendChart").getContext("2d");
  if (dailyTrendChart) dailyTrendChart.destroy();
  if (labels.length) {
    setChartEmptyState("dailyTrendEmpty", false);
    dailyTrendChart = new Chart(dailyTrendCtx, {
      type: "bar",
      data: {
        labels: labels.map(formatShortDate),
        datasets: [
          {
            label: "Daily Units",
            data: trendBaseSeries,
            backgroundColor: "rgba(59, 130, 246, 0.24)",
            borderColor: "rgba(59, 130, 246, 0.86)",
            borderWidth: 1,
            borderRadius: 8,
          },
          {
            label: "7-Day Trend",
            data: rollingTrendSeries,
            type: "line",
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.18)",
            borderWidth: 2.2,
            pointRadius: 1.6,
            pointHoverRadius: 3,
            tension: 0.32,
            spanGaps: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 1100, easing: "easeOutQuart" },
        plugins: {
          legend: { labels: { color: "#dbe7ff", boxWidth: 12, boxHeight: 2, usePointStyle: true } },
        },
        scales: {
          x: { ticks: { color: "#9cb0d8" }, grid: { display: false } },
          y: { ticks: { color: "#9cb0d8" }, grid: { color: "rgba(148, 163, 184, 0.12)" } },
        },
      },
    });
  } else {
    setChartEmptyState("dailyTrendEmpty", true, "No daily trend data available.");
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
      const snap = await fetchForecastSnapshot(city, store, cat, from, to, {
        liveMode: liveSimulationState.running,
        forceRefresh: liveSimulationState.running,
      });
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

function updateLiveControlsUi() {
  const liveCapable = currentUserRole === "admin" || currentUserRole === "inventory_manager";
  if (liveModeStatusEl) {
    const baseText = liveSimulationState.running
      ? `Live: running every ${Number(liveSimulationState.interval_seconds || 0).toFixed(0)}s`
      : "Live: stopped";
    const latestDateText = liveSimulationState.latest_data_date
      ? `latest data ${liveSimulationState.latest_data_date}`
      : "waiting for data";
    const rowsText = `${Number(liveSimulationState.live_dataset_rows || 0)} rows`;
    const ticksText = `${Number(liveSimulationState.tick_count || 0)} ticks`;
    liveModeStatusEl.textContent = `${baseText} | ${latestDateText} | ${rowsText} | ${ticksText}`;
    liveModeStatusEl.title = liveSimulationState.last_error
      ? `Simulation warning: ${liveSimulationState.last_error}`
      : "Continuous live simulation status";
  }
  if (liveStartBtnEl) {
    liveStartBtnEl.style.display = liveCapable ? "" : "none";
    liveStartBtnEl.disabled = liveControlBusy;
  }
  if (liveClearBtnEl) {
    liveClearBtnEl.style.display = currentUserRole === "admin" ? "" : "none";
    liveClearBtnEl.disabled = liveControlBusy;
  }
  if (liveStopBtnEl) {
    liveStopBtnEl.style.display = liveCapable ? "" : "none";
    liveStopBtnEl.disabled = liveControlBusy;
  }
  if (liveTickBtnEl) {
    liveTickBtnEl.style.display = liveCapable ? "" : "none";
    liveTickBtnEl.disabled = liveControlBusy;
  }
}

function applyLiveDateWindow(latestIsoDate) {
  const latest = String(latestIsoDate || "").trim();
  if (!latest || !/^\d{4}-\d{2}-\d{2}$/.test(latest)) return false;
  if (latest === lastAppliedLiveDate) return false;

  const latestDate = clampDateToAllowedRange(new Date(`${latest}T00:00:00`));
  const from = toIsoDate(latestDate);
  const toDateRaw = new Date(latestDate);
  toDateRaw.setDate(toDateRaw.getDate() + 13);
  const to = toIsoDate(clampDateToAllowedRange(toDateRaw));

  if (fromPicker) fromPicker.setDate(from, true, "Y-m-d");
  else fromDateEl.value = from;

  if (toPicker) toPicker.setDate(to, true, "Y-m-d");
  else toDateEl.value = to;

  lastAppliedLiveDate = latest;
  saveDashboardState();
  return true;
}

function applyLiveStatusPayload(payload = {}, options = {}) {
  const status = payload && typeof payload === "object" && payload.status && typeof payload.status === "object"
    ? payload.status
    : payload;
  if (!status || typeof status !== "object") return;
  liveSimulationState = {
    ...liveSimulationState,
    ...status,
  };
  const liveWindowChanged = liveSimulationState.running && applyLiveDateWindow(liveSimulationState.latest_data_date);
  updateLiveControlsUi();
  if (options.forceRefresh || liveWindowChanged) {
    renderCityStoreSummary().catch(() => {});
    queueRenderForecastVisualization({ immediate: true, force: true, liveMode: liveSimulationState.running });
  }
}

async function refreshLiveStatus(options = {}) {
  try {
    const data = await fetchJsonWithDebug(`${apiBase()}/live-data/status`);
    const previousTick = Number(liveSimulationState.tick_count || 0);
    liveSimulationState = {
      ...liveSimulationState,
      ...data,
    };
    const liveWindowChanged = liveSimulationState.running && applyLiveDateWindow(liveSimulationState.latest_data_date);
    updateLiveControlsUi();
    const forceRefresh = Boolean(options?.forceRefresh);
    const tickChanged = Number(liveSimulationState.tick_count || 0) !== previousTick;
    if (forceRefresh || tickChanged || liveWindowChanged) {
      await renderCityStoreSummary();
      await queueRenderForecastVisualization({ immediate: true, force: true });
    }
  } catch (err) {
    if (liveModeStatusEl) liveModeStatusEl.textContent = `Live: unavailable (${err.message})`;
  }
}

function startLiveStatusPolling() {
  if (liveStatusPollTimer) window.clearInterval(liveStatusPollTimer);
  liveStatusPollTimer = window.setInterval(() => {
    refreshLiveStatus();
  }, LIVE_REFRESH_INTERVAL_MS);
}

function startLiveAutoRefreshLoop() {
  if (liveAutoRefreshTimer) window.clearInterval(liveAutoRefreshTimer);
  liveAutoRefreshTimer = null;
}

async function startLiveSimulation() {
  liveControlBusy = true;
  updateLiveControlsUi();
  try {
    const data = await fetchJson(`${apiBase()}/live-data/simulator/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interval_seconds: LIVE_SIMULATION_INTERVAL_SECONDS, batch_size: 4, horizon: 7 }),
    });
    applyLiveStatusPayload(data, { forceRefresh: true });
    await refreshLiveStatus({ forceRefresh: true });
  } catch (err) {
    if (liveModeStatusEl) liveModeStatusEl.textContent = `Live: start failed (${err.message})`;
  } finally {
    liveControlBusy = false;
    updateLiveControlsUi();
  }
}

async function stopLiveSimulation() {
  liveControlBusy = true;
  updateLiveControlsUi();
  try {
    const data = await fetchJson(`${apiBase()}/live-data/simulator/stop`, { method: "POST" });
    applyLiveStatusPayload(data, { forceRefresh: true });
    await refreshLiveStatus({ forceRefresh: true });
  } catch (err) {
    if (liveModeStatusEl) liveModeStatusEl.textContent = `Live: stop failed (${err.message})`;
  } finally {
    liveControlBusy = false;
    updateLiveControlsUi();
  }
}

async function tickLiveSimulation() {
  liveControlBusy = true;
  updateLiveControlsUi();
  try {
    const data = await fetchJson(`${apiBase()}/live-data/simulator/tick?batch_size=4&horizon=7`, { method: "POST" });
    applyLiveStatusPayload(data, { forceRefresh: true });
    await refreshLiveStatus({ forceRefresh: true });
  } catch (err) {
    if (liveModeStatusEl) liveModeStatusEl.textContent = `Live: tick failed (${err.message})`;
  } finally {
    liveControlBusy = false;
    updateLiveControlsUi();
  }
}

async function clearLiveSimulationData() {
  liveControlBusy = true;
  updateLiveControlsUi();
  try {
    const data = await fetchJson(`${apiBase()}/live-data/clear`, { method: "POST" });
    applyLiveStatusPayload(data, { forceRefresh: true });
    await refreshLiveStatus({ forceRefresh: true });
  } catch (err) {
    if (liveModeStatusEl) liveModeStatusEl.textContent = `Live: clear failed (${err.message})`;
  } finally {
    liveControlBusy = false;
    updateLiveControlsUi();
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
    currentUserRole = role;
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
    updateLiveControlsUi();
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
    scopeOptionsState.cityStoreMap = normalizeScopeMap(data.city_store_map);
    scopeOptionsState.cityStoreCategoryMap = normalizeScopeMap(data.city_store_category_map);
    debugLog("scope:loaded", {
      categories: scopeOptionsState.categories.length,
      categoryMapKeys: scopeOptionsState.categoryCityMap.size,
      cityMapKeys: scopeOptionsState.cityCategoryMap.size,
      storeMapKeys: scopeOptionsState.categoryCityStoreMap.size,
      cityStoreMapKeys: scopeOptionsState.cityStoreMap.size,
      cityStoreCategoryMapKeys: scopeOptionsState.cityStoreCategoryMap.size,
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

function ensureStoreOptionsForCurrentSelection() {
  const selectedCity = String(cityEl.value || "").trim();
  const selectedCategory = String(categoryEl.value || "").trim();
  if (!selectedCity) return false;
  let stores = normalizeScopeValues(getStoresForCity(selectedCity));
  if (selectedCategory) {
    const validStores = normalizeScopeValues(getStoresForCategoryCity(selectedCategory, selectedCity));
    if (validStores.length) stores = intersectScopeValues(stores, validStores);
  }
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

async function loadStoresForCity(city, preferredStore = "") {
  const selectedCity = String(city || "").trim();
  if (!selectedCity) {
    syncScopeSelections("city", "", "", "");
    return;
  }
  const cachedStores = normalizeScopeValues(getStoresForCity(selectedCity));
  if (cachedStores.length) {
    populateScopeSelect(storeEl, "Select store", cachedStores, preferredStore);
    storeEl.value = cachedStores.includes(preferredStore) ? preferredStore : "";
    updateStorePlaceholderState();
    rebuildStoreMenu();
    setCategoryFirstMode();
    return;
  }
  try {
    const data = await fetchJsonWithDebug(`${apiBase()}/cities/${encodeURIComponent(selectedCity)}/stores`);
    const resolvedCity = String(data?.city || selectedCity).trim();
    const stores = normalizeScopeValues(data?.stores);
    debugLog("stores:loaded", { city: resolvedCity, count: stores.length, stores });
    scopeOptionsState.cityStoreMap.set(resolvedCity, stores);
    const nextStore = stores.length === 1 && !preferredStore ? stores[0] : preferredStore;
    populateScopeSelect(storeEl, "Select store", stores, nextStore);
    storeEl.value = stores.includes(nextStore) ? nextStore : "";
    updateStorePlaceholderState();
    rebuildStoreMenu();
    setCategoryFirstMode();
  } catch (err) {
    debugLog("stores:error", { city: selectedCity, error: err?.message || String(err) });
    scopeOptionsState.cityStoreMap.set(selectedCity, []);
    populateScopeSelect(storeEl, "Select store", [], "");
    updateStorePlaceholderState();
    rebuildStoreMenu();
    setCategoryFirstMode();
    setFieldError(storeEl, storeErrorEl, "Unable to load stores for this city. Please refresh and try again.");
  }
}

async function loadCategoriesForCityStore(city, store, preferredCategory = "") {
  const selectedCity = String(city || "").trim();
  const selectedStore = String(store || "").trim();
  if (!selectedCity || !selectedStore) {
    syncScopeSelections("store", selectedCity, "", selectedStore);
    return;
  }
  const cachedCategories = normalizeScopeValues(getCategoriesForCityStore(selectedCity, selectedStore));
  if (cachedCategories.length) {
    const nextCategory = cachedCategories.includes(preferredCategory) ? preferredCategory : "";
    populateScopeSelect(categoryEl, "Select category", cachedCategories, nextCategory);
    categoryEl.value = nextCategory;
    updateCategoryPlaceholderState();
    rebuildCategoryMenu();
    setCategoryFirstMode();
    return;
  }
  try {
    const data = await fetchJsonWithDebug(
      `${apiBase()}/cities/${encodeURIComponent(selectedCity)}/stores/${encodeURIComponent(selectedStore)}/categories`,
    );
    const resolvedCity = String(data?.city || selectedCity).trim();
    const resolvedStore = String(data?.store || selectedStore).trim();
    const categories = normalizeScopeValues(data?.categories);
    debugLog("categories:loaded", { city: resolvedCity, store: resolvedStore, count: categories.length, categories });
    scopeOptionsState.cityStoreCategoryMap.set(`${resolvedCity}|||${resolvedStore}`, categories);
    const cityCategories = normalizeScopeValues([
      ...(scopeOptionsState.cityCategoryMap.get(resolvedCity) || []),
      ...categories,
    ]);
    scopeOptionsState.cityCategoryMap.set(resolvedCity, cityCategories);
    const nextCategory = categories.includes(preferredCategory) ? preferredCategory : "";
    populateScopeSelect(categoryEl, "Select category", categories, nextCategory);
    categoryEl.value = nextCategory;
    updateCategoryPlaceholderState();
    rebuildCategoryMenu();
    setCategoryFirstMode();
  } catch (err) {
    debugLog("categories:error", { city: selectedCity, store: selectedStore, error: err?.message || String(err) });
    scopeOptionsState.cityStoreCategoryMap.set(`${selectedCity}|||${selectedStore}`, []);
    populateScopeSelect(categoryEl, "Select category", [], "");
    updateCategoryPlaceholderState();
    rebuildCategoryMenu();
    setCategoryFirstMode();
    setFieldError(categoryEl, categoryErrorEl, "Unable to load categories for this store. Please refresh and try again.");
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
  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "category-empty";
    empty.textContent = "No cities available";
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
  if (dashboardInitialized) return;
  dashboardInitialized = true;
  clearAllErrors();
  await applyRoleUi();
  // Static behavior: always clear form state on browser refresh.
  const shouldClear = getNavigationType() === "reload" || consumeForceClearFlag();
  const savedState = shouldClear ? null : readDashboardState();
  if (shouldClear) clearDashboardState();
  clearInputState();
  initDatePickers();
  await loadScopeOptions(savedState?.city || "", savedState?.category || "");
  if (savedState?.city) {
    await loadStoresForCity(savedState.city, savedState?.store || "");
  }
  if (savedState?.city && savedState?.store) {
    await loadCategoriesForCityStore(savedState.city, savedState.store, savedState?.category || "");
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
  await renderCityStoreSummary();
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
  if (!city) {
    setFieldError(cityEl, cityErrorEl, "Please select a city first.");
    hasError = true;
  }
  if (!store) {
    setFieldError(storeEl, storeErrorEl, city ? "Please select a store from this city." : "Select a city first to view stores.");
    hasError = true;
  }
  if (!category) {
    setFieldError(categoryEl, categoryErrorEl, store ? "Please select a category for this store." : "Select a store first to view categories.");
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
  clearFieldError(categoryEl, categoryErrorEl);
  syncScopeSelections("city", cityEl.value || "", "", "");
  await loadStoresForCity(cityEl.value || "", storeEl.value || "");
  await renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
storeEl.addEventListener("change", async () => {
  clearFieldError(storeEl, storeErrorEl);
  clearFieldError(categoryEl, categoryErrorEl);
  syncScopeSelections("store", cityEl.value || "", "", storeEl.value || "");
  await loadCategoriesForCityStore(cityEl.value || "", storeEl.value || "", categoryEl.value || "");
  await renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
categoryEl.addEventListener("change", () => {
  clearFieldError(categoryEl, categoryErrorEl);
  updateCategoryPlaceholderState();
  rebuildCategoryMenu();
  syncScopeSelections("category", cityEl.value || "", categoryEl.value || "", storeEl.value || "");
  renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
fromDateEl.addEventListener("input", () => {
  clearFieldError(fromDateEl, fromDateErrorEl);
  renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
fromDateEl.addEventListener("change", () => {
  clearFieldError(fromDateEl, fromDateErrorEl);
  renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
toDateEl.addEventListener("input", () => {
  clearFieldError(toDateEl, toDateErrorEl);
  renderCityStoreSummary();
  saveDashboardState();
  queueRenderForecastVisualization();
});
toDateEl.addEventListener("change", () => {
  clearFieldError(toDateEl, toDateErrorEl);
  renderCityStoreSummary();
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
    queueRenderForecastVisualization({ immediate: true, force: true });
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
    if (categoryTriggerEl.disabled) {
      setFieldError(categoryEl, categoryErrorEl, "Select a store first to view categories.");
      return;
    }
    if (!categoryShellEl) return;
    const isOpen = categoryShellEl.classList.contains("is-open");
    if (isOpen) closeCategoryMenu();
    else openCategoryMenu();
  });
}
if (cityTriggerEl) {
  cityTriggerEl.addEventListener("click", () => {
    if (cityTriggerEl.disabled) {
      setFieldError(cityEl, cityErrorEl, "Please select a city.");
      return;
    }
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
if (liveStartBtnEl) {
  liveStartBtnEl.addEventListener("click", async () => {
    await startLiveSimulation();
  });
}
if (liveClearBtnEl) {
  liveClearBtnEl.addEventListener("click", async () => {
    await clearLiveSimulationData();
  });
}
if (liveStopBtnEl) {
  liveStopBtnEl.addEventListener("click", async () => {
    await stopLiveSimulation();
  });
}
if (liveTickBtnEl) {
  liveTickBtnEl.addEventListener("click", async () => {
    await tickLiveSimulation();
  });
}

initializeDefaults();
initializeSidebarState();
updateDashboardClock();
refreshLiveStatus();
startLiveStatusPolling();
startLiveAutoRefreshLoop();
window.setInterval(updateDashboardClock, 1000);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    window.location.reload();
  }
});
