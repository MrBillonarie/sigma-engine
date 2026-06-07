import sys, os
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')
import ccxt, pandas as pd

assets = ['BTC/USDT','ETH/USDT','XRP/USDT','SOL/USDT','BNB/USDT']
tfs    = ['1h','4h','15m']

ex_fut  = ccxt.binance({'timeout':30000,'options':{'defaultType':'future'}})
ex_spot = ccxt.binance({'timeout':30000})

print('=== DATOS DISPONIBLES EN BINANCE ===')
print(f'{"Activo":12s} {"TF":5s} {"Tipo":7s} {"Inicio":12s} {"Fin":12s} {"Dias":6s} {"Anos":5s}')
print('-'*65)

for sym in assets:
    for tf in tfs:
        for ex_type, ex in [('Futuros', ex_fut), ('Spot', ex_spot)]:
            try:
                first = ex.fetch_ohlcv(sym, tf, since=0, limit=1)
                last  = ex.fetch_ohlcv(sym, tf, limit=1)
                if first and last:
                    d1   = pd.to_datetime(first[0][0], unit='ms')
                    d2   = pd.to_datetime(last[0][0],  unit='ms')
                    days = (d2 - d1).days
                    anos = days / 365.25
                    print(f'{sym:12s} {tf:5s} {ex_type:7s} {d1.strftime("%Y-%m-%d"):12s} {d2.strftime("%Y-%m-%d"):12s} {days:6d} {anos:5.1f}y')
            except Exception as e:
                print(f'{sym:12s} {tf:5s} {ex_type:7s} ERROR: {str(e)[:40]}')
        print()
