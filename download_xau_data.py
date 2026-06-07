#!/usr/bin/env python3
"""
Descarga datos historicos de XAU/USD (Oro) via yfinance GC=F (COMEX Gold Futures).
Guarda en /opt/sigma/models/data_XAU_{tf}_max.csv en el mismo formato que otros activos.
"""
import sys
sys.path.insert(0, '/opt/sigma')
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime

OUTPUT = Path('/opt/sigma/models')

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def download_yf(ticker, interval, period, label):
    """Descarga desde yfinance y normaliza columnas."""
    log(f"Descargando {label} ({interval}, {period})...")
    df = yf.download(ticker, period=period, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        log(f"  FAIL: sin datos")
        return None
    # Flatten multi-level columns: ('Close','GC=F') -> 'close'
    if hasattr(df.columns, 'levels'):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = df[['open','high','low','close','volume']]
    df = df.dropna(subset=['close'])
    # Quitar timezone del index para consistencia con otros CSVs
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = 'timestamp'
    df = df[~df.index.duplicated(keep='last')].sort_index()
    log(f"  OK: {len(df):,} velas | {df.index[0].date()} -> {df.index[-1].date()}")
    return df

def resample_to_4h(df_1h):
    """Resamplea 1h -> 4h."""
    df = df_1h.resample('4h').agg({
        'open':  'first',
        'high':  'max',
        'low':   'min',
        'close': 'last',
        'volume':'sum'
    }).dropna(subset=['close'])
    df.index.name = 'timestamp'
    return df

log("=== XAU DATA DOWNLOADER (GC=F COMEX Gold) ===")

# 1h: 2 anos (730d) - max que permite yfinance para 1h
df_1h = download_yf('GC=F', '1h', '730d', 'XAU 1h')
if df_1h is not None:
    df_1h.to_csv(OUTPUT / 'data_XAU_1h_max.csv')
    log(f"  Saved data_XAU_1h_max.csv")

    # 4h: resampleado desde 1h
    df_4h = resample_to_4h(df_1h)
    df_4h.to_csv(OUTPUT / 'data_XAU_4h_max.csv')
    log(f"  Saved data_XAU_4h_max.csv ({len(df_4h):,} velas)")

# 15m: 60 dias (max yfinance)
df_15m = download_yf('GC=F', '15m', '60d', 'XAU 15m')
if df_15m is not None:
    df_15m.to_csv(OUTPUT / 'data_XAU_15m_max.csv')
    log(f"  Saved data_XAU_15m_max.csv")

# 5m: 60 dias
df_5m = download_yf('GC=F', '5m', '60d', 'XAU 5m')
if df_5m is not None:
    df_5m.to_csv(OUTPUT / 'data_XAU_5m_max.csv')
    log(f"  Saved data_XAU_5m_max.csv")

# 1d: desde siempre (decadas de historia)
df_1d = download_yf('GC=F', '1d', 'max', 'XAU 1d')
if df_1d is not None:
    df_1d.to_csv(OUTPUT / 'data_XAU_1d_max.csv')
    log(f"  Saved data_XAU_1d_max.csv")

log("=== DONE ===")
# Summary
for tf in ['1h','4h','15m','5m','1d']:
    p = OUTPUT / f'data_XAU_{tf}_max.csv'
    if p.exists():
        import csv
        with open(p) as f:
            rows = sum(1 for _ in f) - 1
        print(f"  {tf}: {rows:,} velas")
