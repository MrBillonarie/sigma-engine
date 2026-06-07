#!/usr/bin/env python3
"""
Descarga nuevas velas de BTC/USDT cada dia a las 00:30 UTC.
Actualiza los CSV max con las velas mas recientes.
"""
import sys, os
sys.path.insert(0, '/opt/sigma/engine')
os.chdir('/opt/sigma')

from core.data import fetch_ohlcv
from pathlib import Path
from datetime import datetime

LOG = Path('/opt/sigma/results/reports/data_update.log')

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        print(line, file=f)

log('=== DATA UPDATE START ===')
tfs = ['5m', '15m', '1h', '4h', '1d']
for tf in tfs:
    try:
        df = fetch_ohlcv(tf=tf, days=3300)
        out = Path(f'/opt/sigma/models/data_{tf}_max.csv')
        df.to_csv(out)
        log(f'  {tf}: {len(df):,} velas | {df.index[-1].strftime("%Y-%m-%d")} OK')
    except Exception as e:
        log(f'  {tf}: ERROR {e}')
log('=== DATA UPDATE DONE ===')
