#!/usr/bin/env python3
"""
SIGMA Motor 3 — Stocks Fetcher
Descarga AAPL/NVDA/TSLA/JPM/XOM desde yfinance.
15m: 60 días | 1h: 730 días | 4h: resample 1h | 1d: max
"""
import sys, os
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

OUTPUT_DIR = Path('/opt/sigma/models')
LOG_FILE   = Path('/opt/sigma/results/reports/stocks_update.log')

TICKERS = {
    'AAPL': 'AAPL',
    'NVDA': 'NVDA',
    'TSLA': 'TSLA',
    'JPM':  'JPM',
    'XOM':  'XOM',
}

# 15m: solo 60 días disponibles en yfinance
# 4h: resample desde 1h (mismo límite 730 días)
# 1d: max history (20+ años para S&P 500 stocks)
TIMEFRAMES_DOWNLOAD = {
    '15m': {'interval': '15m', 'period': '60d',  'start': None},
    '1h':  {'interval': '1h',  'period': '729d', 'start': None},
    '4h':  {'interval': '1h',  'period': '729d', 'start': None},  # resampled
    '1d':  {'interval': '1d',  'period': 'max',  'start': None},
}


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] [M3-STOCKS] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f)


def _normalize(raw):
    """Convierte output yfinance al formato estándar SIGMA."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(str(c) for c in col).lower() for col in df.columns]
        rename = {}
        for col in df.columns:
            for base in ['open', 'high', 'low', 'close', 'volume']:
                if col.startswith(base):
                    rename[col] = base
                    break
        df = df.rename(columns=rename)
    else:
        df.columns = [c.lower() for c in df.columns]

    for col in ['open', 'high', 'low', 'close']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    if 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)

    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = 'timestamp'

    # Eliminar filas fuera de horario de mercado (pre/post market de yfinance)
    # Stocks: lunes-viernes, no weekends
    df = df[df.index.dayofweek < 5]
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    df = df[df['close'] > 0]
    return df.sort_index()


def _resample_4h(df_1h):
    """Resamplea 1h → 4h (igual que M2)."""
    df = df_1h.resample('4h', label='left', closed='left').agg({
        'open':   'first',
        'high':   'max',
        'low':    'min',
        'close':  'last',
        'volume': 'sum',
    }).dropna(subset=['open', 'close'])
    return df[df['volume'] > 0]


def fetch_ticker(name, yf_symbol):
    log(f"Descargando {name} ({yf_symbol})...")
    for tf, cfg in TIMEFRAMES_DOWNLOAD.items():
        try:
            raw = yf.download(
                yf_symbol,
                interval=cfg['interval'],
                period=cfg['period'],
                progress=False,
                auto_adjust=True,
            )
            if raw.empty:
                log(f"  {tf}: sin datos")
                continue

            df = _normalize(raw)

            # 4h: resamplear desde 1h descargado
            if tf == '4h':
                df = _resample_4h(df)

            out = OUTPUT_DIR / f'data_{name}_{tf}_max.csv'
            df.to_csv(out)
            log(f"  {tf}: {len(df):,} filas → {out.name}")

        except Exception as e:
            log(f"  {tf}: ERROR {e}")


def main():
    log("=== Motor 3 Stocks — actualización de datos ===")
    for name, symbol in TICKERS.items():
        fetch_ticker(name, symbol)
    log("=== Completado ===")


if __name__ == '__main__':
    main()
