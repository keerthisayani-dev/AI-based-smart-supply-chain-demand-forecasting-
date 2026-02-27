import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from statsmodels.tsa.statespace.sarimax import SARIMAX

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parents[1]
CSV_PATH = BASE_DIR / "Dataset" / "retail_store_inventory.csv"
DATE_COL = "date"
PRODUCT_COL = "product id"
TARGET = "units sold"
START_DATE = "2022-01-01"
TEST_RATIO = 0.2


def _daily_product_series(df: pd.DataFrame, pid: str) -> pd.DataFrame:
    p = df[df[PRODUCT_COL] == pid].sort_values(DATE_COL).copy()

    agg_map = {TARGET: "sum"}
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
    return daily.asfreq("D").ffill().bfill()


def _metrics(y_true: pd.Series, y_pred: pd.Series) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    mse = mean_squared_error(y_true, y_pred)
    rmse = np.sqrt(mse)
    r2 = r2_score(y_true, y_pred)
    return {
        "MAE": round(float(mae), 3),
        "MSE": round(float(mse), 3),
        "RMSE": round(float(rmse), 3),
        "R2": round(float(r2), 3),
        "Accuracy(%)": round(float(r2 * 100.0), 2),
    }


def main() -> None:
    df = pd.read_csv(CSV_PATH)
    df.columns = df.columns.str.strip().str.lower()
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], dayfirst=True, errors="coerce")
    df = df.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET]).sort_values(DATE_COL)
    df = df[df[DATE_COL] >= pd.to_datetime(START_DATE)].copy()

    pid = str(df[PRODUCT_COL].iloc[0])
    daily = _daily_product_series(df, pid)

    n = len(daily)
    split_idx = int(n * (1 - TEST_RATIO))
    train = daily.iloc[:split_idx].copy()
    test = daily.iloc[split_idx:].copy()

    print(f"Product: {pid}")
    print("Data range:", daily.index.min().date(), "to", daily.index.max().date())
    print("Train range:", train.index.min().date(), "to", train.index.max().date())
    print("Test range :", test.index.min().date(), "to", test.index.max().date())

    # SARIMAX benchmark
    exog_cols = [c for c in ["price", "discount", "competitor pricing", "holiday/promotion"] if c in daily.columns]
    exog_train = train[exog_cols] if exog_cols else None
    exog_test = test[exog_cols] if exog_cols else None

    sarimax = SARIMAX(
        endog=train[TARGET].astype(float),
        exog=exog_train,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 7),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    sarimax_fit = sarimax.fit(disp=False)
    sarimax_forecast = sarimax_fit.predict(
        start=test.index[0],
        end=test.index[-1],
        exog=exog_test,
    )
    sarimax_forecast = pd.Series(sarimax_forecast, index=test.index, name="sarimax_pred")

    # Existing ExtraTrees baseline (same style as model_train.py)
    feat = daily.copy()
    feat["lag_1"] = feat[TARGET].shift(1)
    feat["lag_7"] = feat[TARGET].shift(7)
    feat["lag_14"] = feat[TARGET].shift(14)
    feat["lag_28"] = feat[TARGET].shift(28)
    feat["rolling_mean_7"] = feat[TARGET].shift(1).rolling(7).mean()
    feat["rolling_mean_14"] = feat[TARGET].shift(1).rolling(14).mean()
    feat["rolling_std_7"] = feat[TARGET].shift(1).rolling(7).std()
    feat["dayofweek"] = feat.index.dayofweek
    feat["month"] = feat.index.month
    feat = feat.dropna()

    split_date = test.index.min()
    train_ml = feat[feat.index < split_date]
    test_ml = feat[feat.index >= split_date]
    features = [c for c in feat.columns if c != TARGET]

    et = ExtraTreesRegressor(n_estimators=500, random_state=42, n_jobs=-1)
    et.fit(train_ml[features], train_ml[TARGET])
    et_pred = pd.Series(et.predict(test_ml[features]), index=test_ml.index, name="et_pred")

    common_idx = test.index.intersection(et_pred.index).intersection(sarimax_forecast.index)
    y_true = test.loc[common_idx, TARGET].astype(float)

    sarimax_m = _metrics(y_true, sarimax_forecast.loc[common_idx])
    et_m = _metrics(y_true, et_pred.loc[common_idx])

    print("\nSARIMAX metrics")
    for k, v in sarimax_m.items():
        print(f"{k}: {v}")

    print("\nExtraTrees metrics")
    for k, v in et_m.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
