#!/usr/bin/env python3
"""Descarga datos 1H y 1D de commodities via yfinance. Sin API key."""
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime

OUTPUT = Path("/opt/sigma/models")
OUTPUT.mkdir(exist_ok=True)

ASSETS = {
    "XAU": "GC=F",
    "XAG": "SI=F",
    "WTI": "CL=F",
    "HG":  "HG=F",
    "NG":  "NG=F",
    "PL":  "PL=F",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def save(asset, tf, df):
    path = OUTPUT / f"data_{asset}_{tf}_max.csv"
    df.index = pd.to_datetime(df.index, utc=True)
    if path.exists():
        old = pd.read_csv(path, index_col=0, parse_dates=True)
        old.index = pd.to_datetime(old.index, utc=True)
        merged = pd.concat([old, df]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        merged.to_csv(path)
        log(f"  {asset}/{tf}: {len(merged):,} rows (merged)")
    else:
        df.to_csv(path)
        log(f"  {asset}/{tf}: {len(df):,} rows (nuevo)")

results = {}
for asset, ticker in ASSETS.items():
    log(f"=== {asset} ({ticker}) ===")
    try:
        t = yf.Ticker(ticker)
        # 1H - maximo 730 dias en Yahoo
        df1h = t.history(period="2y", interval="1h", auto_adjust=True)
        if df1h is not None and len(df1h) > 0:
            df1h.index = pd.to_datetime(df1h.index, utc=True)
            df1h.columns = [c.lower() for c in df1h.columns]
            cols = [c for c in ["open","high","low","close","volume"] if c in df1h.columns]
            save(asset, "1h", df1h[cols])
            results[f"{asset}_1h"] = len(df1h)

        # 1D - hasta 10 años
        df1d = t.history(period="10y", interval="1d", auto_adjust=True)
        if df1d is not None and len(df1d) > 0:
            df1d.index = pd.to_datetime(df1d.index, utc=True)
            df1d.columns = [c.lower() for c in df1d.columns]
            cols = [c for c in ["open","high","low","close","volume"] if c in df1d.columns]
            save(asset, "1d", df1d[cols])
            results[f"{asset}_1d"] = len(df1d)
    except Exception as e:
        log(f"  ERROR: {e}")

log("\n=== RESULTADO ===")
for k, v in sorted(results.items()):
    log(f"  {k}: {v:,} filas")
log("Listo.")
