#!/usr/bin/env python3
"""
F&G (Fear & Greed Index) fetcher para SIGMA.
Lee crypto Fear & Greed de alternative.me y persiste en SQLite.
Solo recoleccion. NO integra al pipeline de signals.
"""
import sys, os, json, sqlite3
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

DB_PATH = '/opt/sigma/results/fng.db'
ENDPOINT = 'https://api.alternative.me/fng/'


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS fng (
        ts INTEGER PRIMARY KEY,
        value INTEGER,
        classification TEXT,
        fetched_at TEXT
    )""")
    conn.commit()
    conn.close()


def _fetch(url, params, timeout=10):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    req = Request(f'{url}?{qs}', headers={'User-Agent': 'sigma-fng/1.0'})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_all(limit=30):
    """Fetch + upsert ultimo(s) N indices F&G."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    inserted, failed = 0, 0
    try:
        payload = _fetch(ENDPOINT, {'limit': limit}, timeout=10)
        rows = payload.get('data') if isinstance(payload, dict) else None
        if not rows:
            failed += 1
            print(f'FAIL fng: empty data payload', flush=True)
        else:
            for row in rows:
                try:
                    ts = int(row['timestamp'])
                    val = int(row['value'])
                    cls = str(row.get('value_classification') or '')
                    c.execute("""INSERT OR REPLACE INTO fng
                                 (ts, value, classification, fetched_at)
                                 VALUES (?, ?, ?, ?)""",
                              (ts, val, cls, now_iso))
                    inserted += 1
                except Exception as e:
                    failed += 1
                    print(f'FAIL fng row {row!r}: {type(e).__name__} {e}', flush=True)
    except URLError as e:
        failed += 1
        print(f'FAIL fng: URLError {e}', flush=True)
    except Exception as e:
        failed += 1
        print(f'FAIL fng: {type(e).__name__} {e}', flush=True)
    conn.commit()
    conn.close()
    print(f'{now_iso} F&G fetch: inserted={inserted} failed={failed}', flush=True)


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    fetch_all(limit=limit)
