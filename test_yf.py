import yfinance as yf
import pandas as pd
# test small download
df = yf.download('GC=F', period='5d', interval='1h', progress=False, auto_adjust=True)
print('Shape:', df.shape)
print('Columns:', list(df.columns))
print(df.tail(3))
