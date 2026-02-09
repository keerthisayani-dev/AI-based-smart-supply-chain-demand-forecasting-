import pandas as pd
import joblib
from sklearn.linear_model import LinearRegression
import os

# Load data
df = pd.read_csv("retail_store_inventory.csv")

# Fix column name
df["Date"] = pd.to_datetime(df["Date"], dayfirst=True)

FEATURES = ["lag_1", "Price", "Discount"]
TARGET = "Units Sold"

os.makedirs("trained_models", exist_ok=True)

for (product, store), group in df.groupby(["Product ID", "Store ID"]):

    group = group.sort_values("Date")

    # Create lag feature
    group["lag_1"] = group[TARGET].shift(1)
    group = group.dropna()

    X = group[FEATURES]
    y = group[TARGET]

    model = LinearRegression()
    model.fit(X, y)

    joblib.dump(
        {
            "model": model,
            "features": FEATURES
        },
        f"trained_models/model_{product}_{store}.pkl"
    )

print("Models trained and saved successfully")
