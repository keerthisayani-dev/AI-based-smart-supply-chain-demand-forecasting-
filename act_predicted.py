import pandas as pd
import joblib
import os

df = pd.read_csv("retail_store_inventory.csv")
df["Date"] = pd.to_datetime(df["Date"], dayfirst=True)

FEATURES = ["lag_1", "Price", "Discount"]
TARGET = "Units Sold"

results = []

for (product, store), group in df.groupby(["Product ID", "Store ID"]):

    model_path = f"trained_models/model_{product}_{store}.pkl"
    if not os.path.exists(model_path):
        continue

    bundle = joblib.load(model_path)
    model = bundle["model"]

    group = group.sort_values("Date")
    group["lag_1"] = group[TARGET].shift(1)
    group = group.dropna()

    X = group[FEATURES]
    y_actual = group[TARGET]

    y_pred = model.predict(X)

    for date, actual, pred in zip(group["Date"], y_actual, y_pred):
        results.append({
            "Date": date,
            "Product ID": product,
            "Store ID": store,
            "Actual_Demand": round(actual, 2),
            "Predicted_Demand": round(pred, 2)
        })

final_df = pd.DataFrame(results)

final_df.to_csv("actual_vs_predicted_demand_new.csv", index=False)

print("Actual vs Predicted demand file created successfully")
