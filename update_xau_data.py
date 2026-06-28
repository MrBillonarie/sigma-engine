#!/usr/bin/env python3
"""
Actualiza datos XAU/USD diariamente (GC=F COMEX Gold via yfinance).
Cron: 01:00 UTC diario (despues del cierre del mercado US)
"""
import sys
sys.path.insert(0, '/opt/sigma')
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime

OUTPUT = Path('/opt/sigma/models')
LOG    = Path('/opt/sigma/results/reports/xau_update.log')

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')

def download_and_merge(ticker, interval, period, tf_name):
    """Descarga y fusiona con CSV existente."""
    csv_path = OUTPUT / f'data_XAU_{tf_name}_max.csv'
    try:
        df_new = yf.download(ticker, period=period, interval=interval,
                             progress=False, auto_adjust=True)
        if df_new is None or df_new.empty:
            log(f"  {tf_name}: sin datos nuevos")
            return
        if hasattr(df_new.columns, 'levels'):
            df_new.columns = [c[0].lower() for c in df_new.columns]
        else:
            df_new.columns = [c.lower() for c in df_new.columns]
        df_new = df_new[['open','high','low','close','volume']].dropna(subset=['close'])
        if hasattr(df_new.index, 'tz') and df_new.index.tz is not None:
            df_new.index = df_new.index.tz_localize(None)
        df_new.index.name = 'timestamp'

        if csv_path.exists():
            df_old = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if hasattr(df_old.index, 'tz') and df_old.index.tz is not None:
                df_old.index = df_old.index.tz_localize(None)
            df_merged = pd.concat([df_old, df_new])
            df_merged = df_merged[~df_merged.index.duplicated(keep='last')].sort_index()
        else:
            df_merged = df_new

        df_merged.to_csv(csv_path)
        log(f"  {tf_name}: {len(df_merged):,} velas | hasta {df_merged.index[-1].date()}")
    except Exception as e:
        log(f"  {tf_name} ERROR: {e}")

log("=== XAU UPDATE START ===")
download_and_merge('GC=F', '1h',  '5d',  '1h')   # ultimos 5 dias
download_and_merge('GC=F', '15m', '5d',  '15m')
download_and_merge('GC=F', '5m',  '5d',  '5m')
download_and_merge('GC=F', '1d',  '5d',  '1d')

# 4h: regen desde 1h actualizado
try:
    df_1h = pd.read_csv(OUTPUT / 'data_XAU_1h_max.csv', index_col=0, parse_dates=True)
    df_4h = df_1h.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['close'])
    df_4h.index.name = 'timestamp'
    df_4h.to_csv(OUTPUT / 'data_XAU_4h_max.csv')
    log(f"  4h: {len(df_4h):,} velas (resampled desde 1h)")
except Exception as e:
    log(f"  4h regen ERROR: {e}")

log("=== XAU UPDATE DONE ===")
