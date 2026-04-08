from pathlib import Path

import pandas as pd
from sklearn.preprocessing import LabelEncoder

BASE_DIR = Path(__file__).resolve().parents[1]
CLEANED_CITY_LEVEL_CSV_PATH = BASE_DIR / "Dataset" / "retail_store_inventory_city_level_cleaned.csv"
CITY_LEVEL_CSV_PATH = BASE_DIR / "Dataset" / "retail_store_inventory_city_level.csv"
LEGACY_CSV_PATH = BASE_DIR / "Dataset" / "retail_store_inventory.csv"
if CLEANED_CITY_LEVEL_CSV_PATH.exists():
    INPUT_CSV_PATH = CLEANED_CITY_LEVEL_CSV_PATH
elif CITY_LEVEL_CSV_PATH.exists():
    INPUT_CSV_PATH = CITY_LEVEL_CSV_PATH
else:
    INPUT_CSV_PATH = LEGACY_CSV_PATH
READABLE_OUTPUT_CSV_PATH = BASE_DIR / "Dataset" / f"{INPUT_CSV_PATH.stem}_cleaned.csv"
ENCODED_OUTPUT_CSV_PATH = BASE_DIR / "Dataset" / f"{INPUT_CSV_PATH.stem}_encoded.csv"


def _safe_mode(series: pd.Series, fallback: str = "Unknown") -> str:
    non_null = series.dropna().astype(str).str.strip()
    non_null = non_null[non_null.ne("") & non_null.str.lower().ne("nan")]
    if non_null.empty:
        return fallback
    mode = non_null.mode()
    return str(mode.iloc[0]) if not mode.empty else fallback


def _fill_categorical(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col not in df.columns:
            continue
        fallback = _safe_mode(df[col])
        cleaned = df[col].astype("string").str.strip()
        cleaned = cleaned.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "none": pd.NA})
        df[col] = cleaned.fillna(fallback)


def _fill_numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col not in df.columns:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        fill_value = float(numeric.median()) if numeric.notna().any() else 0.0
        df[col] = numeric.fillna(fill_value)


def _encode_categorical(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col not in df.columns:
            continue
        encoder = LabelEncoder()
        df[col] = encoder.fit_transform(df[col].astype(str))


df = pd.read_csv(INPUT_CSV_PATH)
df.columns = df.columns.str.strip().str.lower()

before_missing = df.isnull().sum()

if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=True, errors="coerce")
    if df["date"].isna().any():
        df["date"] = df["date"].fillna(df["date"].dropna().min())

categorical_fill_cols = [
    "store id",
    "product id",
    "category",
    "location",
    "weather condition",
    "holiday/promotion",
    "seasonality",
    "region",
    "city",
    "state",
    "country",
    "store_name",
]

numeric_fill_cols = [
    "inventory level",
    "units sold",
    "units ordered",
    "demand forecast",
    "price",
    "discount",
    "competitor pricing",
]

_fill_categorical(df, categorical_fill_cols)
_fill_numeric(df, numeric_fill_cols)

if "record_id" in df.columns:
    missing_record_ids = df["record_id"].isna() | df["record_id"].astype("string").str.strip().isin(["", "nan"])
    if missing_record_ids.any():
        start_idx = 1
        for row_idx in df.index[missing_record_ids]:
            df.at[row_idx, "record_id"] = f"rec-{start_idx:06d}"
            start_idx += 1

encoded_categorical_cols = [
    "store id",
    "product id",
    "category",
    "location",
    "weather condition",
    "seasonality",
    "region",
    "city",
    "state",
    "country",
    "store_name",
]

readable_df = df.copy()
encoded_df = df.copy()

_encode_categorical(encoded_df, encoded_categorical_cols)

if "date" in readable_df.columns:
    readable_df["date"] = readable_df["date"].dt.strftime("%Y-%m-%d")
if "date" in encoded_df.columns:
    encoded_df["date"] = encoded_df["date"].dt.strftime("%Y-%m-%d")

after_missing = readable_df.isnull().sum()
readable_df.to_csv(READABLE_OUTPUT_CSV_PATH, index=False)
encoded_df.to_csv(ENCODED_OUTPUT_CSV_PATH, index=False)

print(f"\nInput dataset: {INPUT_CSV_PATH.name}")
print(f"Readable cleaned dataset saved to: {READABLE_OUTPUT_CSV_PATH.name}")
print(f"Encoded ML-ready dataset saved to: {ENCODED_OUTPUT_CSV_PATH.name}")
print("\nMissing values before cleaning:")
print(before_missing)
print("\nMissing values after cleaning:")
print(after_missing)
print("\nPreprocessing completed successfully")
print("\nReadable cleaned preview:")
print(readable_df.head())
print("\nEncoded ML-ready preview:")
print(encoded_df.head())
