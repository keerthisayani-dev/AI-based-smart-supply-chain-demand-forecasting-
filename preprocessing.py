import pandas as pd
from sklearn.preprocessing import LabelEncoder

df = pd.read_csv("retail_store_inventory.csv")

df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')


print("\nMissing values:")
print(df.isnull().sum())

categorical_cols = [
    'Store ID', 'Product ID', 'Category',
    'Location', 'Weather Condition', 'Seasonality'
]

le = LabelEncoder()
for col in categorical_cols:
    df[col] = le.fit_transform(df[col])

X = df.drop(['Units Sold'], axis=1)

print("\nPreprocessing completed successfully")
print(X.head())
