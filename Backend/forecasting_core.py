from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

TARGET_COL = "units sold"
DATE_COL = "date"
CATEGORY_COL = "category"
PRODUCT_COL = CATEGORY_COL
MODEL_INFO = "LinearRegression with lag + rolling + seasonal calendar features"


@dataclass
class TrainedForecastModel:
    model: Any
    features: List[str]
    target: str
    daily_history: pd.DataFrame
    last_date: pd.Timestamp


def parse_date_series(values: pd.Series) -> pd.Series:
    s = values.astype("string").str.strip()
    iso_mask = s.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False)

    # Use vectorized parsing paths and combine results instead of masked assignment,
    # which avoids pandas out-of-bounds assignment failures on malformed historical dates.
    iso_parsed = pd.to_datetime(s.where(iso_mask), format="%Y-%m-%d", errors="coerce")
    non_iso_parsed = pd.to_datetime(s.where(~iso_mask), dayfirst=True, errors="coerce")
    parsed = iso_parsed.fillna(non_iso_parsed)
    fallback = pd.to_datetime(s, dayfirst=False, errors="coerce")
    return parsed.fillna(fallback)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = out.columns.str.strip().str.lower()
    return out


def load_sales_data(csv_path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = normalize_columns(df)

    if DATE_COL not in df.columns:
        raise ValueError(f"Missing required column: {DATE_COL}")
    if PRODUCT_COL not in df.columns:
        raise ValueError(f"Missing required column: {PRODUCT_COL}")
    if TARGET_COL not in df.columns:
        raise ValueError(f"Missing required column: {TARGET_COL}")

    df[DATE_COL] = parse_date_series(df[DATE_COL])
    df = df.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL]).sort_values(DATE_COL)
    return df


def build_daily_series(df: pd.DataFrame, category: str, selector_col: str = PRODUCT_COL) -> pd.DataFrame:
    p = df[df[selector_col].astype(str) == str(category)].copy()
    if p.empty:
        raise ValueError(f"No rows found for category {category}")

    def _sum_preserve_missing(series: pd.Series) -> float:
        # Keep all-missing groups as NaN so downstream ffill can carry forward prior known values.
        return series.sum(min_count=1)

    agg_map: Dict[str, Any] = {TARGET_COL: "sum"}
    if "inventory level" in p.columns:
        agg_map["inventory level"] = _sum_preserve_missing
    if "units ordered" in p.columns:
        agg_map["units ordered"] = _sum_preserve_missing
    if "demand forecast" in p.columns:
        agg_map["demand forecast"] = _sum_preserve_missing
    if "price" in p.columns:
        agg_map["price"] = "mean"
    if "discount" in p.columns:
        agg_map["discount"] = "mean"
    if "competitor pricing" in p.columns:
        agg_map["competitor pricing"] = "mean"
    if "holiday/promotion" in p.columns:
        agg_map["holiday/promotion"] = "max"

    daily = p.groupby(DATE_COL, as_index=True).agg(agg_map).sort_index()
    daily = daily.asfreq("D")
    daily = daily.ffill().bfill()
    return daily


def _add_calendar_features(feat: pd.DataFrame) -> pd.DataFrame:
    out = feat.copy()
    dow = out.index.dayofweek
    month = out.index.month
    day_of_year = out.index.dayofyear

    out["dayofweek"] = dow
    out["month"] = month
    out["is_weekend"] = dow.isin([5, 6]).astype(int)

    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["month_sin"] = np.sin(2 * np.pi * month / 12.0)
    out["month_cos"] = np.cos(2 * np.pi * month / 12.0)
    out["doy_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    out["time_idx"] = np.arange(len(out), dtype=float)

    return out


def build_features(daily: pd.DataFrame, target: str = TARGET_COL) -> pd.DataFrame:
    feat = daily.copy()

    feat["lag_1"] = feat[target].shift(1)
    feat["lag_7"] = feat[target].shift(7)
    feat["lag_14"] = feat[target].shift(14)
    feat["lag_28"] = feat[target].shift(28)

    feat["rolling_mean_7"] = feat[target].shift(1).rolling(7).mean()
    feat["rolling_mean_14"] = feat[target].shift(1).rolling(14).mean()
    feat["rolling_std_7"] = feat[target].shift(1).rolling(7).std()

    feat = _add_calendar_features(feat)
    return feat


def train_forecast_model(daily: pd.DataFrame, target: str = TARGET_COL) -> TrainedForecastModel:
    feat = build_features(daily, target=target)
    feature_cols = [c for c in feat.columns if c != target]
    feat = feat.dropna(subset=feature_cols + [target])

    if len(feat) < 30:
        raise ValueError("Not enough history to train model. Need at least 30 days of data.")

    model = LinearRegression()
    model.fit(feat[feature_cols], feat[target])

    return TrainedForecastModel(
        model=model,
        features=feature_cols,
        target=target,
        daily_history=daily.copy(),
        last_date=daily.index.max(),
    )


def _feature_row(
    history: pd.DataFrame,
    forecast_date: pd.Timestamp,
    feature_cols: List[str],
    target: str,
    static_values: Dict[str, float],
) -> pd.DataFrame:
    row: Dict[str, float] = {}

    lag_lookup = {
        "lag_1": 1,
        "lag_7": 7,
        "lag_14": 14,
        "lag_28": 28,
    }

    for col in feature_cols:
        if col in lag_lookup:
            lag = lag_lookup[col]
            if len(history) >= lag:
                row[col] = float(history[target].iloc[-lag])
            else:
                row[col] = float(history[target].iloc[-1])
        elif col == "rolling_mean_7":
            row[col] = float(history[target].iloc[-7:].mean())
        elif col == "rolling_mean_14":
            tail = history[target].iloc[-14:] if len(history) >= 14 else history[target]
            row[col] = float(tail.mean())
        elif col == "rolling_std_7":
            row[col] = float(history[target].iloc[-7:].std(ddof=0)) if len(history) >= 2 else 0.0
        elif col == "dayofweek":
            row[col] = int(forecast_date.dayofweek)
        elif col == "month":
            row[col] = int(forecast_date.month)
        elif col == "is_weekend":
            row[col] = int(forecast_date.dayofweek in [5, 6])
        elif col == "dow_sin":
            row[col] = float(np.sin(2 * np.pi * forecast_date.dayofweek / 7.0))
        elif col == "dow_cos":
            row[col] = float(np.cos(2 * np.pi * forecast_date.dayofweek / 7.0))
        elif col == "month_sin":
            row[col] = float(np.sin(2 * np.pi * forecast_date.month / 12.0))
        elif col == "month_cos":
            row[col] = float(np.cos(2 * np.pi * forecast_date.month / 12.0))
        elif col == "doy_sin":
            row[col] = float(np.sin(2 * np.pi * forecast_date.dayofyear / 365.25))
        elif col == "doy_cos":
            row[col] = float(np.cos(2 * np.pi * forecast_date.dayofyear / 365.25))
        elif col == "time_idx":
            row[col] = float(len(history))
        elif col in static_values:
            row[col] = float(static_values[col])
        else:
            row[col] = 0.0

    return pd.DataFrame([row], index=[forecast_date])


def forecast_next_days(
    trained: TrainedForecastModel,
    horizon: int = 7,
    anchor_date: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    history = trained.daily_history.copy().sort_index()
    target = trained.target

    start_date = trained.last_date
    if anchor_date is not None:
        anchor_date = pd.to_datetime(anchor_date).normalize()
        history = history[history.index <= anchor_date]
        if history.empty:
            raise ValueError("anchor_date is earlier than available product history")
        start_date = anchor_date

    static_values: Dict[str, float] = {}
    for col in trained.features:
        if col in history.columns and col != target:
            static_values[col] = float(history[col].iloc[-1])

    # Build robust seasonal priors from non-recursive historical data.
    hist_target = history[target].dropna()
    baseline_mean = float(hist_target.mean()) if not hist_target.empty else 0.0
    baseline_mean = max(0.0, baseline_mean)

    dow_factor: Dict[int, float] = {i: 1.0 for i in range(7)}
    month_factor: Dict[int, float] = {i: 1.0 for i in range(1, 13)}
    if baseline_mean > 0 and not hist_target.empty:
        dow_means = history.groupby(history.index.dayofweek)[target].mean()
        for dow in range(7):
            if dow in dow_means.index and pd.notna(dow_means.loc[dow]):
                dow_factor[dow] = float(dow_means.loc[dow] / baseline_mean)

        month_means = history.groupby(history.index.month)[target].mean()
        for month in range(1, 13):
            if month in month_means.index and pd.notna(month_means.loc[month]):
                month_factor[month] = float(month_means.loc[month] / baseline_mean)

    # Use empirical bounds to prevent explosive/flat recursive behavior.
    q05 = float(hist_target.quantile(0.05)) if not hist_target.empty else 0.0
    q95 = float(hist_target.quantile(0.95)) if not hist_target.empty else max(1.0, baseline_mean)
    lower_bound = max(0.0, q05 * 0.75)
    upper_bound = max(lower_bound + 1.0, q95 * 1.20)

    rows = []
    current_date = start_date

    for _ in range(horizon):
        current_date = current_date + pd.Timedelta(days=1)
        X_next = _feature_row(history, current_date, trained.features, target, static_values)
        raw_pred = float(trained.model.predict(X_next[trained.features])[0])
        non_negative_pred = max(0.0, raw_pred)

        dow = int(current_date.dayofweek)
        month = int(current_date.month)
        seasonal_target = baseline_mean * dow_factor.get(dow, 1.0) * month_factor.get(month, 1.0)
        lag7 = float(history[target].iloc[-7]) if len(history) >= 7 else float(history[target].iloc[-1])

        # Blend model output with learned seasonal priors and weekly persistence.
        blended = (0.45 * non_negative_pred) + (0.35 * seasonal_target) + (0.20 * lag7)
        pred = float(np.clip(blended, lower_bound, upper_bound))

        new_hist_row = {col: np.nan for col in history.columns}
        new_hist_row[target] = pred
        for col, value in static_values.items():
            if col in history.columns:
                new_hist_row[col] = value

        history.loc[current_date] = new_hist_row

        rows.append(
            {
                "date": current_date.date().isoformat(),
                "forecast_units_sold": round(pred, 3),
            }
        )

    return pd.DataFrame(rows)
