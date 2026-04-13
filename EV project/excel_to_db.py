import pandas as pd
import sqlite3

# read csv file
df = pd.read_excel("stations.xlsx")

print(df)   # check data

# connect database
conn = sqlite3.connect("database/ev_data.db")

# store exactly same columns
df.to_sql("stations", conn, if_exists="replace", index=False)

conn.close()

print("Database created successfully")