#!/usr/bin/env python3
"""
LSR (Long/Short Ratio) fetcher para SIGMA.
Lee 3 metricas LSR de Binance Futures cada N min y persiste en SQLite.
Solo recoleccion. NO integra al pipeline de signals.
"""
import sys, os, json, sqlite3, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

DB_PATH = '/opt/sigma/results/lsr.db'
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'LTCUSDT']
TFS = ['15m', '1h', '4h']
ENDPOINTS = {
    'top_acct': 'https://fapi.binance.com/futures/data/topLongShortAccountRatio',
    'top_pos':  'https://fapi.binance.com/futures/data/topLongShortPositionRatio',
    'global':   'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
}


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS lsr (
        symbol TEXT, tf TEXT, ts INTEGER,
        kind TEXT,
        long_ratio REAL, short_ratio REAL, ls_ratio REAL,
        fetched_at TEXT,
        PRIMARY KEY (symbol, tf, ts, kind)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sym_tf_ts ON lsr(symbol, tf, ts DESC)")
    conn.commit()
    conn.close()


def _fetch(url, params, timeout=10):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    req = Request(f'{url}?{qs}', headers={'User-Agent': 'sigma-lsr/1.0'})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_all(limit=1):
    """Fetch + upsert los 45 cruces (5 sym x 3 tf x 3 endpoints)."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    inserted, failed = 0, 0
    for sym in SYMBOLS:
        for tf in TFS:
            for kind, url in ENDPOINTS.items():
                try:
                    data = _fetch(url, {'symbol': sym, 'period': tf, 'limit': limit}, timeout=8)
                    for row in data:
                        ts = int(row['timestamp'])
                        long_r = float(row.get('longAccount') or row.get('longPosition') or 0)
                        short_r = float(row.get('shortAccount') or row.get('shortPosition') or 0)
                        ls = float(row.get('longShortRatio') or 0)
                        c.execute("""INSERT OR REPLACE INTO lsr
                                     (symbol, tf, ts, kind, long_ratio, short_ratio, ls_ratio, fetched_at)
                                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                  (sym, tf, ts, kind, long_r, short_r, ls, now_iso))
                        inserted += 1
                    # gentle pacing to be polite to Binance
                    time.sleep(0.05)
                except URLError as e:
                    failed += 1
                    print(f'FAIL {sym} {tf} {kind}: URLError {e}', flush=True)
                except Exception as e:
                    failed += 1
                    print(f'FAIL {sym} {tf} {kind}: {type(e).__name__} {e}', flush=True)
    conn.commit()
    conn.close()
    print(f'{now_iso} LSR fetch: inserted={inserted} failed={failed}', flush=True)


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    fetch_all(limit=limit)
