import pandas as pd
from sklearn.preprocessing import LabelEncoder
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
df = pd.read_csv(BASE_DIR / "Dataset" / "retail_store_inventory.csv")
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
