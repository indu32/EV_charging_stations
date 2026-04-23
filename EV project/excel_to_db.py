import pandas as pd
import sqlite3

df = pd.read_excel("stations.xlsx")

print("Before cleaning:")
print(df.dtypes)
print(df.head())

# Fix Bug 1 & 2: convert lat/lng to numeric, drop rows where either is null/invalid
df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
df = df.dropna(subset=['latitude', 'longitude'])

# Fix Bug 3: type column stored as float (12.0) — convert to clean string
df['type'] = df['type'].apply(lambda x: str(int(x)) if pd.notna(x) and str(x).replace('.','').isdigit() else str(x))

print("\nAfter cleaning:")
print(df.dtypes)
print(f"Total rows: {len(df)}")

conn = sqlite3.connect("database/ev_data.db")
df.to_sql("stations", conn, if_exists="replace", index=False)
conn.close()

print("Database created successfully")
