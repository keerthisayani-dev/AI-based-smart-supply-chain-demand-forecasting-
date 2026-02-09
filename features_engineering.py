import pandas as pd

df = pd.read_csv(
    r"C:\Users\Keerthi Sayani\OneDrive\Documents\supply chain project\project_implementation data\retail_store_inventory.csv"
)

df.columns = df.columns.str.strip().str.lower()
df['date'] = pd.to_datetime(df['date'], dayfirst=True)

# FILTER PRODUCT IDs (P0001–P0010)
product_ids = [f'P{str(i).zfill(4)}' for i in range(1,11)]
df = df[df['product id'].isin(product_ids)]

# SORT (IMPORTANT)
df = df.sort_values(by=['product id', 'date']).reset_index(drop=True)


# FEATURE ENGINEERING
df['lag_1'] = df.groupby('product id')['units sold'].shift(1)
df['lag_7'] = df.groupby('product id')['units sold'].shift(7)

df['ma_7'] = (
    df.groupby('product id')['units sold']
      .rolling(7)
      .mean()
      .reset_index(level=0, drop=True)
)

df['trend'] = df['units sold'] - df['lag_1']


# HOLIDAY (KEEP ORIGINAL LOGIC)
df['holiday'] = (df['discount'] > 0).astype(int)

# FORCE STORE IDs (S001, S002, S003)
stores = ['S001', 'S002', 'S003']
df['store id'] = df.groupby('product id').cumcount().apply(
    lambda x: stores[x % 3]
)


# FORCE SEASONS (Winter → Spring → Summer)
seasons = ['Winter', 'Spring', 'Summer']
df['season'] = df.groupby('product id').cumcount().apply(
    lambda x: seasons[x % 3]
)

# FINAL DATASET
df_final = df[
    [
        'date',
        'store id',
        'product id',
        'category',
        'units sold',
        'lag_1',
        'lag_7',
        'ma_7',
        'trend',
        'holiday',
        'season'
    ]
]

print(df_final.groupby('product id').head(3))
print("\nFeature Engineering completed successfully")
