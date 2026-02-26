import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
df = pd.read_csv(BASE_DIR / "Dataset" / "retail_store_inventory.csv")

df.columns = df.columns.str.strip().str.lower()
df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=True, errors="coerce")
df = df.dropna(subset=["date"])

# FILTER PRODUCT IDs (P0001-P0010)
product_ids = [f"P{str(i).zfill(4)}" for i in range(1, 11)]
df = df[df["product id"].isin(product_ids)]

# SORT (IMPORTANT)
df = df.sort_values(by=["product id", "date"]).reset_index(drop=True)

# FEATURE ENGINEERING
df["lag_1"] = df.groupby("product id")["units sold"].shift(1)
df["lag_7"] = df.groupby("product id")["units sold"].shift(7)

df["ma_7"] = (
    df.groupby("product id")["units sold"]
    .rolling(7)
    .mean()
    .reset_index(level=0, drop=True)
)

df["trend"] = df["units sold"] - df["lag_1"]

possible_cols = ["holiday/promotion"]
holiday_col = next((c for c in possible_cols if c in df.columns), None)

if holiday_col is not None:
    # Convert common formats (Yes/No, True/False, 1/0, etc.) to 0/1.
    df["holiday"] = (
        df[holiday_col]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(["1", "true", "yes", "y", "holiday", "promotion"])
        .astype(int)
    )
else:
    # Fallback: treat weekends as holidays.
    df["holiday"] = df["date"].dt.dayofweek.isin([5, 6]).astype(int)  # Sat=5, Sun=6

# FORCE STORE IDs (S001, S002, S003)
stores = ["S001", "S002", "S003"]
df["store id"] = df.groupby("product id").cumcount().apply(lambda x: stores[x % 3])

# FORCE SEASONS (Winter -> Spring -> Summer)
seasons = ["Winter", "Spring", "Summer"]
df["season"] = df.groupby("product id").cumcount().apply(lambda x: seasons[x % 3])

# FINAL DATASET
df_final = df[
    [
        "date",
        "store id",
        "product id",
        "category",
        "units sold",
        "lag_1",
        "lag_7",
        "ma_7",
        "trend",
        "holiday",
        "season",
    ]
]

print(df_final.groupby("product id").head(3))
print("\nFeature Engineering completed successfully")
