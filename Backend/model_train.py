import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


# SETTINGS
BASE_DIR = Path(__file__).resolve().parents[1]
CSV_PATH = BASE_DIR / "Dataset" / "retail_store_inventory.csv"
DATE_COL = "date"
PRODUCT_COL = "product id"
TARGET = "units sold"
TEST_RATIO = 0.2
START_DATE = "2022-01-01"

# Load data
df = pd.read_csv(CSV_PATH)
df.columns = df.columns.str.strip().str.lower()

df[DATE_COL] = pd.to_datetime(df[DATE_COL], dayfirst=True, errors="coerce")
df = df.dropna(subset=[DATE_COL, PRODUCT_COL, TARGET]).sort_values(DATE_COL)

# Keep data from the expected dataset start
start_ts = pd.to_datetime(START_DATE)
df = df[df[DATE_COL] >= start_ts].copy()
if df.empty:
    raise ValueError(f"No rows found on/after {START_DATE}. Check DATE_COL format and START_DATE.")

# Pick one product
pid = df[PRODUCT_COL].iloc[0]
p = df[df[PRODUCT_COL] == pid].sort_values(DATE_COL).copy()

print("Product:", pid, "| rows:", len(p))

#DAILY DATA (Fix duplicate dates + keep useful predictors)
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

daily = daily.asfreq("D")
daily = daily.ffill()

# Train test split
n = len(daily)
split_idx = int(n * (1 - TEST_RATIO))

train = daily.iloc[:split_idx]
test = daily.iloc[split_idx:]

split_date = test.index.min()

print("Data range used:", daily.index.min().date(), "to", daily.index.max().date())
print("Train range    :", train.index.min().date(), "to", train.index.max().date())
print("Test range     :", test.index.min().date(), "to", test.index.max().date())

y_test = test[TARGET].astype(float)

# EXTRA TREES MODEL
feat = daily.copy()

# Lag + time features
feat["lag_1"] = feat[TARGET].shift(1)
feat["lag_7"] = feat[TARGET].shift(7)
feat["lag_14"] = feat[TARGET].shift(14)
feat["lag_28"] = feat[TARGET].shift(28)
feat["rolling_mean_7"] = feat[TARGET].shift(1).rolling(7).mean()
feat["rolling_mean_14"] = feat[TARGET].shift(1).rolling(14).mean()
feat["rolling_std_7"] = feat[TARGET].shift(1).rolling(7).std()
feat["dayofweek"] = feat.index.dayofweek
feat["month"] = feat.index.month

FEATURES = [c for c in feat.columns if c != TARGET]

feat = feat.dropna(subset=FEATURES + [TARGET])

train_lr = feat[feat.index < split_date]
test_lr = feat[feat.index >= split_date]

X_train = train_lr[FEATURES]
y_train_lr = train_lr[TARGET]

X_test = test_lr[FEATURES]
y_test_lr = test_lr[TARGET]

# Align same dates
common_dates = y_test.index.intersection(y_test_lr.index)
y_actual = y_test.loc[common_dates]
X_test = X_test.loc[common_dates]

model = ExtraTreesRegressor(
    n_estimators=500,
    random_state=42,
    n_jobs=-1,
)
model.fit(X_train, y_train_lr)
train_pred = pd.Series(model.predict(X_train), index=X_train.index)
test_pred = pd.Series(model.predict(X_test), index=common_dates)

compare = pd.DataFrame({
    "Actual": y_actual,
    "Predicted": test_pred,
})

print("\n--- Actual vs Predicted ---")
print(compare.head(10))

# ---- Metrics ----
test_mae = mean_absolute_error(compare["Actual"], compare["Predicted"])
test_mse = mean_squared_error(compare["Actual"], compare["Predicted"])
test_rmse = np.sqrt(test_mse)
test_r2 = r2_score(compare["Actual"], compare["Predicted"])

print("\n===== Model Evaluation (ExtraTreesRegressor) =====")
print("\nMAE  :", round(test_mae, 3))
print("MSE  :", round(test_mse, 3))
print("RMSE :", round(test_rmse, 3))
print("R2   :", round(test_r2, 3))
print("Accuracy (%):", round(test_r2 * 100, 2))

# GRAPH: ACTUAL vs PREDICTED
plt.figure(figsize=(12,6))
plt.plot(compare.index, compare["Actual"], label="Actual")
plt.plot(compare.index, compare["Predicted"], label="Model Prediction")

plt.title("Actual vs Predicted Demand (ExtraTreesRegressor)")
plt.xlabel("Date")
plt.ylabel("Units Sold")
plt.legend()
plt.show()
