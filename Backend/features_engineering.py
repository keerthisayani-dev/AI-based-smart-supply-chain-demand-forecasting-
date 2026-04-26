from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BASE_DIR / "Dataset"
CITY_LEVEL_CLEANED_PATH = DATASET_DIR / "retail_store_inventory_city_level_cleaned.csv"
CITY_LEVEL_RAW_PATH = DATASET_DIR / "retail_store_inventory_city_level.csv"
LEGACY_CLEANED_PATH = DATASET_DIR / "retail_store_inventory_cleaned.csv"
LEGACY_RAW_PATH = DATASET_DIR / "retail_store_inventory.csv"

if CITY_LEVEL_CLEANED_PATH.exists():
    INPUT_CSV_PATH = CITY_LEVEL_CLEANED_PATH
elif CITY_LEVEL_RAW_PATH.exists():
    INPUT_CSV_PATH = CITY_LEVEL_RAW_PATH
elif LEGACY_CLEANED_PATH.exists():
    INPUT_CSV_PATH = LEGACY_CLEANED_PATH
else:
    INPUT_CSV_PATH = LEGACY_RAW_PATH

OUTPUT_CSV_PATH = DATASET_DIR / f"{INPUT_CSV_PATH.stem}_features.csv"


def _normalize_holiday(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .isin(["1", "true", "yes", "y", "holiday", "promotion", "festival"])
        .astype(int)
    )


df = pd.read_csv(INPUT_CSV_PATH)
df.columns = df.columns.str.strip().str.lower()

if "date" not in df.columns or "product id" not in df.columns or "units sold" not in df.columns:
    raise ValueError("Input dataset must include date, product id, and units sold columns.")

df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=True, errors="coerce")
df = df.dropna(subset=["date"]).copy()
df = df.sort_values(["product id", "date"]).reset_index(drop=True)

if "holiday/promotion" in df.columns:
    df["holiday_flag"] = _normalize_holiday(df["holiday/promotion"])
else:
    df["holiday_flag"] = df["date"].dt.dayofweek.isin([5, 6]).astype(int)

df["day_of_week"] = df["date"].dt.dayofweek
df["month"] = df["date"].dt.month
df["week_of_year"] = df["date"].dt.isocalendar().week.astype(int)
df["quarter"] = df["date"].dt.quarter
df["is_weekend"] = df["day_of_week"].isin([5, 6]).astype(int)

group = df.groupby("product id")["units sold"]
df["lag_1"] = group.shift(1)
df["lag_7"] = group.shift(7)
df["lag_14"] = group.shift(14)
df["rolling_mean_7"] = group.shift(1).rolling(7).mean().reset_index(level=0, drop=True)
df["rolling_mean_14"] = group.shift(1).rolling(14).mean().reset_index(level=0, drop=True)
df["rolling_std_7"] = group.shift(1).rolling(7).std().reset_index(level=0, drop=True)
df["trend"] = df["units sold"] - df["lag_1"]

numeric_fill_cols = [
    "inventory level",
    "units ordered",
    "demand forecast",
    "price",
    "discount",
    "competitor pricing",
    "lag_1",
    "lag_7",
    "lag_14",
    "rolling_mean_7",
    "rolling_mean_14",
    "rolling_std_7",
    "trend",
]

for col in numeric_fill_cols:
    if col in df.columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        fill_value = float(numeric.median()) if numeric.notna().any() else 0.0
        df[col] = numeric.fillna(fill_value)

df["date"] = df["date"].dt.strftime("%Y-%m-%d")
df.to_csv(OUTPUT_CSV_PATH, index=False)

print(f"\nInput dataset: {INPUT_CSV_PATH.name}")
print(f"Feature-engineered dataset saved to: {OUTPUT_CSV_PATH.name}")
print("\nEngineered columns added:")
print([
    "holiday_flag",
    "day_of_week",
    "month",
    "week_of_year",
    "quarter",
    "is_weekend",
    "lag_1",
    "lag_7",
    "lag_14",
    "rolling_mean_7",
    "rolling_mean_14",
    "rolling_std_7",
    "trend",
])
print("\nFeature Engineering completed successfully")
print(df.head())
