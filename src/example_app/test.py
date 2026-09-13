import pandas as pd

df =pd.read_csv("data/NL52INGB0003610006_13-09-2016_12-09-2026.csv", sep=";")

print(df.head())

df.to_excel("test.xlsx")
