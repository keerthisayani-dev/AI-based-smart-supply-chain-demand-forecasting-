import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

warnings.filterwarnings("ignore")


def load_product_daily_data(
    csv_path: Path,
    date_col: str,
    product_col: str,
    target: str,
    start_date: str,
    product_id: str | None = None,
) -> tuple[pd.DataFrame, str]:
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip().str.lower()

    df[date_col] = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce")
    df = df.dropna(subset=[date_col, product_col, target]).sort_values(date_col)

    start_ts = pd.to_datetime(start_date)
    df = df[df[date_col] >= start_ts].copy()
    if df.empty:
        raise ValueError(
            f"No rows found on/after {start_date}. Check DATE_COL format and START_DATE."
        )

    pid = product_id if product_id is not None else df[product_col].iloc[0]
    p = df[df[product_col] == pid].sort_values(date_col).copy()
    if p.empty:
        raise ValueError(f"No rows found for product id: {pid}")

    agg_map: dict[str, str] = {target: "sum"}
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

    daily = p.groupby(date_col, as_index=True).agg(agg_map).sort_index()
    daily = daily.asfreq("D").ffill()
    return daily, pid


def split_train_test(daily: pd.DataFrame, test_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(daily)
    split_idx = int(n * (1 - test_ratio))
    if split_idx <= 0 or split_idx >= n:
        raise ValueError(f"Invalid split index {split_idx} for dataset size {n}.")
    train = daily.iloc[:split_idx]
    test = daily.iloc[split_idx:]
    return train, test


def build_features(daily: pd.DataFrame, target: str) -> tuple[pd.DataFrame, list[str]]:
    feat = daily.copy()
    feat["lag_1"] = feat[target].shift(1)
    feat["lag_7"] = feat[target].shift(7)
    feat["rolling_mean_7"] = feat[target].shift(1).rolling(7).mean()
    feat["rolling_mean_14"] = feat[target].shift(1).rolling(14).mean()
    feat["dayofweek"] = feat.index.dayofweek
    feat["month"] = feat.index.month

    features = [c for c in feat.columns if c != target]
    feat = feat.dropna(subset=features + [target])
    return feat, features


def train_and_predict(
    feat: pd.DataFrame,
    target: str,
    features: list[str],
    split_date: pd.Timestamp,
    y_actual: pd.Series,
) -> pd.DataFrame:
    train_lr = feat[feat.index < split_date]
    test_lr = feat[feat.index >= split_date]

    if train_lr.empty or test_lr.empty:
        raise ValueError("Feature split produced empty train or test set.")

    X_train = train_lr[features]
    y_train = train_lr[target].astype(float)

    common_dates = y_actual.index.intersection(test_lr.index)
    if common_dates.empty:
        raise ValueError("No overlapping dates between test target and model features.")

    X_test = test_lr.loc[common_dates, features]
    y_true = y_actual.loc[common_dates].astype(float)

    model = LinearRegression()
    model.fit(X_train, y_train)
    y_pred = pd.Series(model.predict(X_test), index=common_dates)

    return pd.DataFrame({"Actual": y_true, "LR_Pred": y_pred})


def evaluate(compare: pd.DataFrame) -> dict[str, float]:
    mae = mean_absolute_error(compare["Actual"], compare["LR_Pred"])
    mse = mean_squared_error(compare["Actual"], compare["LR_Pred"])
    rmse = np.sqrt(mse)
    r2 = r2_score(compare["Actual"], compare["LR_Pred"])
    return {
        "mae": mae,
        "mse": mse,
        "rmse": rmse,
        "r2": r2,
        "accuracy_pct": r2 * 100,
    }


def plot_actual_vs_pred(compare: pd.DataFrame) -> None:
    plt.figure(figsize=(12, 6))
    plt.plot(compare.index, compare["Actual"], label="Actual")
    plt.plot(compare.index, compare["LR_Pred"], label="Linear Regression Prediction")
    plt.title("Actual vs Predicted Demand")
    plt.xlabel("Date")
    plt.ylabel("Units Sold")
    plt.legend()
    plt.show()
