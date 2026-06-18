#!/usr/bin/env python3
"""
OANDA Practice API -- Bulk historical download para WTI/NG/HG/PL
Instrumentos: WTICO_USD, NATGAS_USD, XCU_USD, XPT_USD
TFs: M15 (15m), H1 (1h), H4 (4h)
Historia: 5+ anyos (OANDA practice tipicamente tiene 2020->now en M15, 2015->now en H1)

Setup:
  1. Crea cuenta demo gratis: https://fxtrade.oanda.com/account/demo-open/
  2. Login -> My Account -> Settings -> Manage API Access -> copia Access Token
  3. Agrega a /root/.claude/settings.json: "env": {"OANDA_ACCESS_TOKEN": "tu_token_aqui"}
  4. python3 /opt/sigma/fetch_oanda_commodities.py
"""
import sys, json, time
sys.path.insert(0, '/opt/sigma')
import urllib.request, urllib.parse
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

OANDA_API  = "https://api-fxpractice.oanda.com/v3"
OUTPUT_DIR = Path('/opt/sigma/models')
LOG_FILE   = Path('/opt/sigma/results/reports/oanda_fetch.log')

INSTRUMENTS = {
    'WTI': 'WTICO_USD',
    'NG':  'NATGAS_USD',
    'HG':  'XCU_USD',
    'PL':  'XPT_USD',
}

TFS = {
    'M15': '15m',
    'H1':  '1h',
    'H4':  '4h',
}

HISTORY_START = {
    'M15': datetime(2020, 1, 1, tzinfo=timezone.utc),
    'H1':  datetime(2019, 1, 1, tzinfo=timezone.utc),
    'H4':  datetime(2015, 1, 1, tzinfo=timezone.utc),
}

CHUNK_DAYS = {'M15': 52, 'H1': 200, 'H4': 800}


def get_token():
    sp = Path('/opt/sigma/engine/config/secrets.json')
    s = json.loads(sp.read_text())
    tok = s.get('OANDA_ACCESS_TOKEN', '').strip()
    if not tok:
        raise RuntimeError("OANDA_ACCESS_TOKEN vacio. Edita /opt/sigma/engine/config/secrets.json")
    return tok


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f)


def fetch_chunk(oanda_name, granularity, from_dt, to_dt, token):
    params = {
        'granularity': granularity,
        'from': from_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'to':   to_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count': '5000',
        'price': 'M',
    }
    url = f"{OANDA_API}/instruments/{oanda_name}/candles?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    candles = [c for c in data.get('candles', []) if c.get('complete')]
    if not candles:
        return pd.DataFrame()
    rows = []
    for c in candles:
        mid = c.get('mid', {})
        rows.append({
            'timestamp': c['time'],
            'open':   float(mid.get('o', 0)),
            'high':   float(mid.get('h', 0)),
            'low':    float(mid.get('l', 0)),
            'close':  float(mid.get('c', 0)),
            'volume': float(c.get('volume', 0)),
        })
    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    return df.set_index('timestamp')


def fetch_instrument_tf(sigma_name, oanda_name, granularity, sigma_tf, token):
    out_path = OUTPUT_DIR / f'data_{sigma_name}_{sigma_tf}_max.csv'
    start_from = HISTORY_START[granularity]

    df_existing = None
    existing_count = 0
    if out_path.exists():
        try:
            df_existing = pd.read_csv(out_path, index_col=0, parse_dates=True)
            df_existing.index = pd.to_datetime(df_existing.index, utc=True)
            existing_count = len(df_existing)
            start_from = max(start_from, df_existing.index[-1] + timedelta(seconds=1))
        except Exception as e:
            log(f"  {sigma_name}/{sigma_tf}: warn leyendo existente: {e}")

    now = datetime.now(timezone.utc)
    chunk_days = CHUNK_DAYS[granularity]
    cur = start_from
    all_new = []

    while cur < now:
        end = min(cur + timedelta(days=chunk_days), now)
        try:
            df_c = fetch_chunk(oanda_name, granularity, cur, end, token)
            if not df_c.empty:
                all_new.append(df_c)
                log(f"  {sigma_name}/{sigma_tf}: {cur.strftime('%Y-%m-%d')}->{end.strftime('%Y-%m-%d')}: +{len(df_c)} filas")
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')[:150]
            if e.code in (401, 403):
                raise RuntimeError(f"Auth error {e.code}: {body}")
            log(f"  {sigma_name}/{sigma_tf}: HTTP {e.code} ({cur.strftime('%Y-%m-%d')}) -- skip chunk")
        except Exception as e:
            log(f"  {sigma_name}/{sigma_tf}: ERROR chunk {cur.strftime('%Y-%m-%d')}: {e}")
        cur = end + timedelta(seconds=1)
        time.sleep(0.3)

    if not all_new:
        log(f"  {sigma_name}/{sigma_tf}: sin datos nuevos (ya al dia o sin historial)")
        return 0

    df_new = pd.concat(all_new).sort_index()
    df_new = df_new[~df_new.index.duplicated(keep='last')]

    if df_existing is not None:
        df_final = pd.concat([df_existing, df_new]).sort_index()
        df_final = df_final[~df_final.index.duplicated(keep='last')]
    else:
        df_final = df_new

    df_final.to_csv(out_path)
    log(f"  {sigma_name}/{sigma_tf}: GUARDADO {existing_count:,} -> {len(df_final):,} filas (+{len(df_new)})")
    return len(df_new)


def main():
    try:
        token = get_token()
    except RuntimeError as e:
        log(f"FATAL: {e}")
        sys.exit(1)

    log("=== OANDA BULK FETCH START ===")
    log(f"Instrumentos: {list(INSTRUMENTS.keys())} | TFs: {list(TFS.values())}")

    for sigma_name, oanda_name in INSTRUMENTS.items():
        for granularity, sigma_tf in TFS.items():
            log(f"--- {sigma_name} ({oanda_name}) {sigma_tf} ---")
            try:
                fetch_instrument_tf(sigma_name, oanda_name, granularity, sigma_tf, token)
            except RuntimeError as e:
                log(f"FATAL auth error: {e}")
                sys.exit(1)
            except Exception as e:
                log(f"  {sigma_name}/{sigma_tf}: skip: {e}")

    log("=== OANDA BULK FETCH DONE ===")


if __name__ == '__main__':
    main()
