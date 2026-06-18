#!/usr/bin/env python3
"""EIA daily price updater — cron: 01:30 UTC daily"""
import sys, json, requests, pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

EIA_KEY = json.loads(open('/opt/sigma/engine/config/secrets.json').read())['EIA_API_KEY']
OUTPUT  = Path('/opt/sigma/models')
LOG     = Path('/opt/sigma/results/reports/eia_update.log')

def log(msg):
    line = f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '
')

def fetch_recent(url, params, n=20):
    r = requests.get(url, params={**params, 'length': n, 'sort[0][direction]': 'desc'}, timeout=20)
    return r.json().get('response', {}).get('data', [])

def merge_close(rows, value_key, asset, tf='1d'):
    if not rows: return
    csv = OUTPUT / f'data_{asset}_{tf}_max.csv'
    df = pd.read_csv(csv, index_col=0, parse_dates=True) if csv.exists() else pd.DataFrame()
    for row in rows:
        dt = pd.to_datetime(row.get('period'))
        val = float(row.get(value_key, 0) or 0)
        if val <= 0 or dt in df.index: continue
        df.loc[dt] = [val, val, val, val, 0.0]
    df = df.sort_index()
    df.index.name = 'timestamp'
    df.to_csv(csv)

log('=== EIA UPDATE ===')
# WTI
wti_rows = fetch_recent('https://api.eia.gov/v2/petroleum/pri/spt/data/', {
    'api_key': EIA_KEY, 'frequency': 'daily', 'data[0]': 'value', 'facets[series][]': 'RWTC',
    'sort[0][column]': 'period'
})
merge_close(wti_rows, 'value', 'WTI')
log(f'WTI: {len(wti_rows)} rows updated')

# NG
ng_rows = fetch_recent('https://api.eia.gov/v2/natural-gas/pri/fut/data/', {
    'api_key': EIA_KEY, 'frequency': 'daily', 'data[0]': 'value', 'facets[series][]': 'RNGWHHD',
    'sort[0][column]': 'period'
})
merge_close(ng_rows, 'value', 'NG')
log(f'NG: {len(ng_rows)} rows updated')

log('=== DONE ===')
