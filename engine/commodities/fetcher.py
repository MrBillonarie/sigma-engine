#!/usr/bin/env python3
"""
SIGMA Motor 2 - Commodities Fetcher
Descarga XAU (GC=F) y XAG (SI=F) desde yfinance.
Maneja NaN de fines de semana con forward-fill.
"""
import sys, os
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

OUTPUT_DIR = Path('/opt/sigma/models')
LOG_FILE   = Path('/opt/sigma/results/reports/commodities_update.log')

TICKERS = {
    'XAU': 'GC=F',   # Gold Futures
    'XAG': 'SI=F',   # Silver Futures
    'WTI': 'CL=F',   # Crude Oil WTI Futures
    'HG':  'HG=F',   # Copper Futures
    'NG':  'NG=F',   # Natural Gas Futures
    'PL':  'PL=F',   # Platinum Futures
}

# yfinance limita 1h a los ultimos 730 dias
# 4h: resample desde 1h
# 1d: sin limite (descarga desde 2015)
TIMEFRAMES_DOWNLOAD = {
    '5m':  {'interval': '5m',  'period': '7d',    'start': None},
    '15m': {'interval': '15m', 'period': '60d',   'start': None},
    '1h':  {'interval': '1h',  'period': '729d',  'start': None},
    '4h':  {'interval': '1h',  'period': '729d',  'start': None},
    '1d':  {'interval': '1d',  'period': 'max',   'start': None},        # hasta 28 años para GC=F/PL
}


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f)


def _normalize(raw):
    """Convierte output yfinance al formato estandar SIGMA."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(str(c) for c in col).lower() for col in df.columns]
        rename = {}
        for col in df.columns:
            for base in ['open','high','low','close','volume']:
                if col.startswith(base):
                    rename[col] = base
                    break
        df = df.rename(columns=rename)
    else:
        df.columns = [c.lower() for c in df.columns]
    available = [c for c in ['open','high','low','close','volume'] if c in df.columns]
    df = df[available].copy()
    if 'volume' not in df.columns:
        df['volume'] = 0.0
    df.index.name = 'timestamp'
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.ffill(limit=3)
    df = df.dropna(subset=['close'])
    df = df[df['close'] > 0]
    return df


def fetch_tf(sym, tf):
    ticker = TICKERS[sym]
    cfg = TIMEFRAMES_DOWNLOAD[tf]
    log(f"  Descargando {sym} {tf} ({ticker})...")
    kwargs = dict(
        tickers=ticker,
        interval=cfg['interval'],
        progress=False,
        auto_adjust=True,
    )
    if cfg.get('period'):
        kwargs['period'] = cfg['period']
    else:
        kwargs['start'] = cfg['start']
    raw = yf.download(**kwargs)
    if raw is None or len(raw) < 10:
        log(f"  {sym} {tf}: SIN DATOS de yfinance")
        return None
    df = _normalize(raw)
    if tf == '4h':
        df = df.resample('4h', label='right', closed='right').agg({
            'open':   'first',
            'high':   'max',
            'low':    'min',
            'close':  'last',
            'volume': 'sum',
        }).dropna(subset=['close'])
        df = df[df['open'].notna()]
    return df


def update_all(syms=None):
    log('=== COMMODITIES DATA UPDATE START ===')
    targets = syms or list(TICKERS.keys())
    for sym in targets:
        for tf in list(TIMEFRAMES_DOWNLOAD.keys()):
            try:
                df = fetch_tf(sym, tf)
                if df is None:
                    continue
                out = OUTPUT_DIR / f'data_{sym}_{tf}_max.csv'
                # Acumular historico: merge con CSV existente para no perder datos antiguos
                df_save = df
                if out.exists():
                    try:
                        df_old = pd.read_csv(out, index_col=0, parse_dates=True)
                        df_old.index = pd.to_datetime(df_old.index, utc=True)
                        base_cols = [c for c in ['open','high','low','close','volume'] if c in df_old.columns]
                        df_base_old = df_old[base_cols]
                        df_combined = pd.concat([df_base_old, df_save]).sort_index()
                        # Eliminar duplicados: en caso de overlap, priorizar datos nuevos
                        df_combined = df_combined[~df_combined.index.duplicated(keep='last')]
                        df_save = df_combined
                        log(f'  {sym} {tf}: acumulado {len(df_old):,}->{len(df_save):,} filas')
                    except Exception as _em:
                        log(f'  {sym} {tf}: merge WARN {_em}')
                df_save.to_csv(out)
                log(f'  {sym} {tf}: {len(df_save):,} filas | ultimo={df_save.index[-1].strftime("%Y-%m-%d")} OK')
            except Exception as e:
                log(f'  {sym} {tf}: ERROR {e}')
    # Merge macro features (DXY + 10Y) into commodity CSVs
    try:
        import sys as _ms; _ms.path.insert(0, '/opt/sigma/engine')
        from commodities.macro_fetcher import download_macro
        macro_data = download_macro()
        for tf, macro_df in macro_data.items():
            for sym in (targets or list(TICKERS.keys())):
                csv_path = OUTPUT_DIR / f'data_{sym}_{tf}_max.csv'
                if not csv_path.exists():
                    continue
                try:
                    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    df.index = pd.to_datetime(df.index, utc=True)
                    macro_r = macro_df.reindex(df.index, method='ffill', tolerance='4h')
                    for col in ['dxy', 'yield_10y']:
                        if col in macro_r.columns:
                            df[col] = macro_r[col]
                    df.to_csv(csv_path)
                    log(f'  {sym} {tf}: macro features merged OK')
                except Exception as em:
                    log(f'  {sym} {tf}: macro merge WARN {em}')
    except Exception as e:
        log(f'  macro merge ERROR: {e}')
    log('=== COMMODITIES DATA UPDATE DONE ===')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--sym', nargs='+', default=None)
    args = ap.parse_args()
    update_all(args.sym)
