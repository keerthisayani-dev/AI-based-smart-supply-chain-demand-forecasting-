from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

TARGET_COL = "units sold"
DATE_COL = "date"
PRODUCT_COL = "product id"
MODEL_INFO = "RandomForestRegressor with lag + rolling + seasonal calendar features"


@dataclass
class TrainedForecastModel:
    model: Any
    features: List[str]
    target: str
    daily_history: pd.DataFrame
    last_date: pd.Timestamp


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

    df[DATE_COL] = pd.to_datetime(df[DATE_COL], dayfirst=True, errors="coerce")
    df = df.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET_COL]).sort_values(DATE_COL)
    return df


def build_daily_series(df: pd.DataFrame, product_id: str) -> pd.DataFrame:
    p = df[df[PRODUCT_COL].astype(str) == str(product_id)].copy()
    if p.empty:
        raise ValueError(f"No rows found for product {product_id}")

    agg_map: Dict[str, str] = {TARGET_COL: "sum"}
    if "inventory level" in p.columns:
        agg_map["inventory level"] = "sum"
    if "units ordered" in p.columns:
        agg_map["units ordered"] = "sum"
    if "demand forecast" in p.columns:
        agg_map["demand forecast"] = "sum"
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

    model = RandomForestRegressor(
        n_estimators=400,
        random_state=42,
        min_samples_leaf=2,
        n_jobs=-1,
    )
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


def forecast_next_days(trained: TrainedForecastModel, horizon: int = 7) -> pd.DataFrame:
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    history = trained.daily_history.copy()
    target = trained.target

    static_values: Dict[str, float] = {}
    for col in trained.features:
        if col in history.columns and col != target:
            static_values[col] = float(history[col].iloc[-1])

    rows = []
    current_date = trained.last_date

    for _ in range(horizon):
        current_date = current_date + pd.Timedelta(days=1)
        X_next = _feature_row(history, current_date, trained.features, target, static_values)
        pred = float(trained.model.predict(X_next[trained.features])[0])
        pred = max(0.0, pred)

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
