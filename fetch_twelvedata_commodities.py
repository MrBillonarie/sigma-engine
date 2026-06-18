#!/usr/bin/env python3
"""
fetch_twelvedata_commodities.py
Descarga WTI, NG, HG, PL desde Twelve Data (free tier).
Timeframes: 15m, 1h, 4h (resampled desde 1h), 1d.
Extiende historial de 2 años a 2018+.

Symbols verificados 2026-06-16: WTI, NG, HG, PL, SI (silver), XAU/USD (gold).
"""
import os, sys, json, time, logging
from pathlib import Path
from datetime import datetime, timedelta

import requests
import pandas as pd

_SECRETS = json.load(open("/opt/sigma/engine/config/secrets.json"))
API_KEY  = _SECRETS.get("TWELVE_DATA_API_KEY", "")
OUTPUT   = Path("/opt/sigma/models")
LOG_PATH = Path("/opt/sigma/results/reports/twelvedata_fetch.log")
BASE_URL = "https://api.twelvedata.com/time_series"

# Symbols verified working on 2026-06-16
SYMBOLS = {
    "WTI": "WTI",
    "NG":  "NG",
    "HG":  "HG",
    "PL":  "PL",
}

START_DATE = "2018-01-01"
CALL_DELAY = 9.0  # 8 calls/min free tier → 7.5s min; use 9s to be safe

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_PATH), mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger().info


def fetch_chunk(symbol, interval, start_date, outputsize=5000):
    params = {
        "symbol":     symbol,
        "interval":   interval,
        "start_date": start_date,
        "outputsize": outputsize,
        "apikey":     API_KEY,
        "format":     "JSON",
        "timezone":   "UTC",
    }
    try:
        r = requests.get(BASE_URL, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "error":
            log(f"    API error: {data.get('message','?')}")
            return None
        values = data.get("values", [])
        if not values:
            return None
        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.rename(columns={"datetime": "timestamp"}).set_index("timestamp").sort_index()
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].dropna(subset=["close"])
    except Exception as e:
        log(f"    Request error: {e}")
        return None


def fetch_paginated(name, symbol, interval, start_date):
    log(f"  [{name}] {interval} desde {start_date}...")
    delta_map = {"15min": timedelta(minutes=15), "1h": timedelta(hours=1)}
    delta = delta_map.get(interval, timedelta(hours=1))
    chunks, current_start, chunk_n = [], start_date, 0

    while True:
        time.sleep(CALL_DELAY)
        chunk_n += 1
        df = fetch_chunk(symbol, interval, current_start)
        if df is None or len(df) == 0:
            break
        chunks.append(df)
        log(f"    chunk {chunk_n}: {len(df)} bars  {df.index[0].date()} -> {df.index[-1].date()}")
        if len(df) < 4990:
            break
        current_start = (df.index[-1] + delta).strftime("%Y-%m-%d %H:%M:%S")

    if not chunks:
        return None
    df_all = pd.concat(chunks)
    return df_all[~df_all.index.duplicated(keep="last")].sort_index()


def tz_strip(df):
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def merge_save(df_new, csv_path):
    df_new = tz_strip(df_new)
    if csv_path.exists():
        df_old = tz_strip(pd.read_csv(csv_path, index_col=0, parse_dates=True))
        df_merged = pd.concat([df_old, df_new])
        df_merged = df_merged[~df_merged.index.duplicated(keep="last")].sort_index()
    else:
        df_merged = df_new
    df_merged.index.name = "timestamp"
    df_merged.to_csv(csv_path)
    return df_merged


def main():
    if not API_KEY:
        log("ERROR: TWELVE_DATA_API_KEY vacío en secrets.json")
        return 1

    log("=" * 60)
    log(f"TWELVE DATA COMMODITIES FETCH  key={API_KEY[:8]}...")
    log(f"Activos: {list(SYMBOLS.keys())}  |  Desde: {START_DATE}")
    log("TFs: 15m, 1h, 4h (resample), 1d (resample)")
    log("=" * 60)

    for name, symbol in SYMBOLS.items():
        log(f"--- {name} ({symbol}) ---")

        # --- 15m ---
        df_15m = fetch_paginated(name, symbol, "15min", START_DATE)
        if df_15m is not None:
            m = merge_save(df_15m, OUTPUT / f"data_{name}_15m_max.csv")
            log(f"  15m TOTAL: {len(m)} bars  {m.index[0].date()} -> {m.index[-1].date()}")
        else:
            log("  15m: sin datos")

        # --- 1h (y resample a 4h/1d) ---
        df_1h = fetch_paginated(name, symbol, "1h", START_DATE)
        if df_1h is not None:
            m1h = merge_save(df_1h, OUTPUT / f"data_{name}_1h_max.csv")
            log(f"  1h TOTAL: {len(m1h)} bars  {m1h.index[0].date()} -> {m1h.index[-1].date()}")

            # 4h resample
            df_4h = tz_strip(df_1h).resample("4h").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["close"])
            m4h = merge_save(df_4h, OUTPUT / f"data_{name}_4h_max.csv")
            log(f"  4h TOTAL: {len(m4h)} bars  {m4h.index[0].date()} -> {m4h.index[-1].date()}")

            # 1d resample
            df_1d = tz_strip(df_1h).resample("1D").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["close"])
            m1d = merge_save(df_1d, OUTPUT / f"data_{name}_1d_max.csv")
            log(f"  1d TOTAL: {len(m1d)} bars  {m1d.index[0].date()} -> {m1d.index[-1].date()}")
        else:
            log("  1h/4h/1d: sin datos")

        log("")

    log("=" * 60)
    log("DONE — M2 data extendido. Reiniciar trainer para usar nueva historia.")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
