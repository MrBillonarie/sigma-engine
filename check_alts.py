
import ccxt, pandas as pd
ex = ccxt.binance({'timeout':30000,'options':{'defaultType':'future'}})
exs= ccxt.binance({'timeout':30000})
for sym in ['DOGE/USDT','AVAX/USDT','ADA/USDT','LTC/USDT','LINK/USDT']:
    try:
        first = ex.fetch_ohlcv(sym,'1h',since=0,limit=1)
        last  = ex.fetch_ohlcv(sym,'1h',limit=1)
        if first and last:
            d1 = pd.to_datetime(first[0][0],unit='ms')
            d2 = pd.to_datetime(last[0][0],unit='ms')
            days=(d2-d1).days
            print(f"{sym:12s} fut:{d1.strftime('%Y-%m')} ({days//365}y{(days%365)//30}m)")
    except Exception as e:
        print(f"{sym} ERR:{e}")
