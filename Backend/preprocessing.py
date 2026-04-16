from pathlib import Path

import pandas as pd
from sklearn.preprocessing import LabelEncoder

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "Dataset" / "retail_store_inventory.csv"

df = pd.read_csv(CSV_PATH)
df.columns = df.columns.str.strip().str.lower()

df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=True, errors="coerce")


print("\nMissing values:")
print(df.isnull().sum())

categorical_cols = [
    "store id", "product id", "category",
    "location", "weather condition", "seasonality"
]

le = LabelEncoder()
for col in categorical_cols:
    df[col] = le.fit_transform(df[col])

X = df.drop(["units sold"], axis=1)

print("\nPreprocessing completed successfully")
print(X.head())
