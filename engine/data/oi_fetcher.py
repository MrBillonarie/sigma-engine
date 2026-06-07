#!/usr/bin/env python3
"""
OI (Open Interest) fetcher para SIGMA.
Lee Open Interest historico de Binance Futures cada N min y persiste en SQLite.
Solo recoleccion. NO integra al pipeline de signals.
"""
import sys, os, json, sqlite3, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

DB_PATH = '/opt/sigma/results/oi.db'
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'LTCUSDT']
TFS = ['15m', '1h', '4h']
ENDPOINT = 'https://fapi.binance.com/futures/data/openInterestHist'


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS oi (
        symbol TEXT, tf TEXT, ts INTEGER,
        sum_open_interest REAL,
        sum_open_interest_value REAL,
        fetched_at TEXT,
        PRIMARY KEY (symbol, tf, ts)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_oi_sym_tf_ts ON oi(symbol, tf, ts DESC)")
    conn.commit()
    conn.close()


def _fetch(url, params, timeout=10):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    req = Request(f'{url}?{qs}', headers={'User-Agent': 'sigma-oi/1.0'})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_all(limit=1):
    """Fetch + upsert OI para 15 cruces (5 sym x 3 tf)."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    inserted, failed = 0, 0
    for sym in SYMBOLS:
        for tf in TFS:
            try:
                data = _fetch(ENDPOINT, {'symbol': sym, 'period': tf, 'limit': limit}, timeout=8)
                for row in data:
                    ts = int(row['timestamp'])
                    soi = float(row.get('sumOpenInterest') or 0)
                    soiv = float(row.get('sumOpenInterestValue') or 0)
                    c.execute("""INSERT OR REPLACE INTO oi
                                 (symbol, tf, ts, sum_open_interest, sum_open_interest_value, fetched_at)
                                 VALUES (?, ?, ?, ?, ?, ?)""",
                              (sym, tf, ts, soi, soiv, now_iso))
                    inserted += 1
                time.sleep(0.05)
            except URLError as e:
                failed += 1
                print(f'FAIL {sym} {tf}: URLError {e}', flush=True)
            except Exception as e:
                failed += 1
                print(f'FAIL {sym} {tf}: {type(e).__name__} {e}', flush=True)
    conn.commit()
    conn.close()
    print(f'{now_iso} OI fetch: inserted={inserted} failed={failed}', flush=True)


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    fetch_all(limit=limit)
