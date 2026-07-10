import pandas as pd
import sqlite3
import re

df = pd.read_excel("ev_stations.xlsx")

print("Before cleaning:")
print(df.dtypes)
print(df.head())

# Fix 1 & 2: lat/lng to numeric, drop invalid rows
df['latitude']  = pd.to_numeric(df['latitude'],  errors='coerce')
df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
df = df.dropna(subset=['latitude', 'longitude'])

# Fix 3: normalise city casing
df['city'] = df['city'].str.strip().str.title()

# Fix 4: connector type integer codes → human-readable labels
TYPE_MAP = {
    '6': 'Type 2 AC',     '7': 'CCS2 DC',       '8': 'CHAdeMO',
    '10': 'Type 1 AC',    '11': 'AC Slow',        '12': 'DC Fast',
    '13': 'AC Level 2',   '14': 'AC Level 1',     '15': 'DC Ultra-Fast',
    '16': 'CCS1 DC',      '17': 'GB/T AC',        '18': 'GB/T DC',
    '19': 'Bharat AC-001','20': 'Bharat DC-001',  '21': 'Type 2 Combo',
    '22': 'AC Portable',  '23': 'DC Combined',    '24': 'Multi-Standard',
    'CCS2 ': 'CCS2 DC',   'CCS2': 'CCS2 DC',
}
def map_type(x):
    if pd.isna(x): return 'Unknown'
    s = str(x).strip()
    if re.match(r'^\d+\.0$', s): s = str(int(float(s)))
    return TYPE_MAP.get(s, s)

df['type'] = df['type'].apply(map_type)

# Fix 5: replace 'Mach' → 'Lastica' in station names
df['name'] = df['name'].apply(
    lambda x: re.sub(r'(?i)\bmach\b', 'Lastica', str(x)) if pd.notna(x) else x
)

# Fix 6: is_priority → int 0 or 1
if 'is_priority' in df.columns:
    df['is_priority'] = pd.to_numeric(df['is_priority'], errors='coerce').fillna(0).astype(int).clip(0, 1)
else:
    df['is_priority'] = 0

# Fix 7: image_url — strip stray newlines
if 'image_url' not in df.columns:
    df['image_url'] = None
else:
    df['image_url'] = df['image_url'].apply(
        lambda x: str(x).strip().replace('\n','').replace('\r','') if pd.notna(x) else None
    )

# Fix 8: slots → integer
if 'slots' in df.columns:
    df['slots'] = pd.to_numeric(df['slots'], errors='coerce').fillna(0).astype(int)
else:
    df['slots'] = 0

print("\nAfter cleaning:")
print(df.dtypes)
print(f"Total rows:        {len(df)}")
print(f"Priority stations: {df['is_priority'].sum()}")
print(f"Type distribution:\n{df['type'].value_counts().head(10)}")

conn = sqlite3.connect("database/ev_data.db")
df.to_sql("stations", conn, if_exists="replace", index=False)
conn.close()

print("\nDatabase created successfully at database/ev_data.db")