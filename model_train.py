import warnings
warnings.filterwarnings("ignore")

import json
import time
import pandas as pd
import numpy as np
from pathlib import Path

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


BASE_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BASE_DIR / "Dataset"
FEATURE_PATHS = [
    DATASET_DIR / "retail_store_inventory_city_level_cleaned_features.csv",
    DATASET_DIR / "retail_store_inventory_city_level_features.csv",
    DATASET_DIR / "retail_store_inventory_cleaned_features.csv",
    DATASET_DIR / "retail_store_inventory_features.csv",
]
CSV_PATH = next((path for path in FEATURE_PATHS if path.exists()), None)
if CSV_PATH is None:
    raise FileNotFoundError("No feature-engineered dataset found. Run Backend/features_engineering.py first.")

DATE_COL = "date"
TARGET = "units sold"
MODEL_OUTPUT_DIR = BASE_DIR / "Backend"
METRICS_PATH = MODEL_OUTPUT_DIR / "model_metrics.json"
PREDICTIONS_PATH = MODEL_OUTPUT_DIR / "model_test_predictions.csv"
DROP_FEATURES = {"record_id"}


def _encode_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col == TARGET:
            continue
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            encoder = LabelEncoder()
            out[col] = encoder.fit_transform(out[col].astype(str))
    return out


df = pd.read_csv(CSV_PATH)
df.columns = df.columns.str.strip().str.lower()

if TARGET not in df.columns:
    raise ValueError(f"Missing required target column: {TARGET}")

if DATE_COL in df.columns:
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")
    df = df.sort_values(DATE_COL)
    df["date_ordinal"] = df[DATE_COL].map(lambda value: value.toordinal() if pd.notna(value) else np.nan)
    df["year"] = df[DATE_COL].dt.year.fillna(0).astype(int)
    df["month_from_date"] = df[DATE_COL].dt.month.fillna(0).astype(int)
    df["day_from_date"] = df[DATE_COL].dt.day.fillna(0).astype(int)
    df = df.drop(columns=[DATE_COL])

df = _encode_object_columns(df)
df = df.dropna(subset=[TARGET]).copy()

feature_cols = [col for col in df.columns if col != TARGET]
feature_cols = [col for col in feature_cols if col not in DROP_FEATURES]
X = df[feature_cols]
y = pd.to_numeric(df[TARGET], errors="coerce")

valid_mask = y.notna()
X = X.loc[valid_mask].copy()
y = y.loc[valid_mask].copy()

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    shuffle=False,
)

model = ExtraTreesRegressor(
    n_estimators=80,
    max_depth=18,
    min_samples_leaf=3,
    max_features="sqrt",
    random_state=42,
    n_jobs=1,
)
print(f"\nStarting model training on {len(X_train)} rows with {len(feature_cols)} features...")
fit_started_at = time.time()
model.fit(X_train, y_train)
fit_seconds = time.time() - fit_started_at

train_pred = model.predict(X_train)
test_pred = model.predict(X_test)

metrics = {
    "input_dataset": CSV_PATH.name,
    "train_rows": int(len(X_train)),
    "test_rows": int(len(X_test)),
    "feature_count": int(len(feature_cols)),
    "fit_seconds": float(fit_seconds),
    "mae": float(mean_absolute_error(y_test, test_pred)),
    "mse": float(mean_squared_error(y_test, test_pred)),
    "rmse": float(np.sqrt(mean_squared_error(y_test, test_pred))),
    "r2": float(r2_score(y_test, test_pred)),
    "train_mae": float(mean_absolute_error(y_train, train_pred)),
}

predictions_df = X_test.copy()
predictions_df["actual_units_sold"] = y_test.values
predictions_df["predicted_units_sold"] = test_pred
predictions_df.to_csv(PREDICTIONS_PATH, index=False)
METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

print(f"\nTraining dataset: {CSV_PATH.name}")
print(f"Saved metrics to: {METRICS_PATH.name}")
print(f"Saved test predictions to: {PREDICTIONS_PATH.name}")
print("\n===== Model Evaluation (ExtraTreesRegressor) =====")
print(f"Train rows: {metrics['train_rows']}")
print(f"Test rows : {metrics['test_rows']}")
print(f"Features  : {metrics['feature_count']}")
print(f"Fit time  : {metrics['fit_seconds']:.2f} sec")
print(f"MAE       : {metrics['mae']:.3f}")
print(f"MSE       : {metrics['mse']:.3f}")
print(f"RMSE      : {metrics['rmse']:.3f}")
print(f"R2        : {metrics['r2']:.3f}")
print(f"Accuracy (%): {metrics['r2'] * 100:.2f}")
print("\nSample predictions:")
print(predictions_df[["actual_units_sold", "predicted_units_sold"]].head(10))
