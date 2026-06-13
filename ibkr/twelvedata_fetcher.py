#!/usr/bin/env python3
"""
SIGMA Motor 2 - Twelve Data Historical Fetcher
Gets 5 years of 1H data for all commodity assets.
Free tier: 800 req/day, 8 req/min.
"""
import sys, time, requests, pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

OUTPUT_DIR = Path("/opt/sigma/models")
API_KEY = "demo"  # Replace with real key from twelvedata.com (free)

# Twelve Data symbols for commodities
SYMBOLS = {
    "XAU": "XAU/USD",
    "XAG": "XAG/USD",
    "WTI": "WTI/USD",
    "NG":  "NATURALGAS/USD",
    "HG":  "COPPER/USD",
    "PL":  "PLATINUM/USD",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_1h_chunk(symbol_td, api_key, start_date, end_date):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol_td,
        "interval": "1h",
        "start_date": start_date,
        "end_date": end_date,
        "outputsize": 5000,
        "apikey": api_key,
        "format": "JSON",
    }
    r = requests.get(url, params=params, timeout=30)
    data = r.json()
    if data.get("status") == "error":
        return None, data.get("message", "unknown error")
    values = data.get("values", [])
    if not values:
        return None, "no values"
    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                              "close": "close", "volume": "volume"})
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[cols].dropna(subset=["open", "close"]), None

def merge_csv(new_df, csv_path):
    if not csv_path.exists():
        return new_df
    try:
        old = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        old.index = pd.to_datetime(old.index, utc=True)
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in old.columns]
        combined = pd.concat([old[cols], new_df]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined
    except Exception as e:
        log(f"  merge warn: {e}")
        return new_df

def fetch_asset(asset, symbol_td, api_key):
    log(f"\n--- {asset} ({symbol_td}) ---")
    # Fetch 5 years in chunks of ~6 months (5000 hourly bars ~ 208 days)
    end = datetime.utcnow()
    all_dfs = []
    for year_back in range(0, 5):
        chunk_end = end - timedelta(days=year_back * 365)
        chunk_start = chunk_end - timedelta(days=208)
        df, err = fetch_1h_chunk(
            symbol_td, api_key,
            chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
            chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if err:
            log(f"  chunk {year_back}: {err}")
        elif df is not None and len(df) > 0:
            all_dfs.append(df)
            log(f"  chunk {year_back}: {len(df)} rows ({str(df.index[0])[:10]} -> {str(df.index[-1])[:10]})")
        time.sleep(8)  # 8s = ~7.5 req/min (under 8/min limit)

    if not all_dfs:
        log(f"  {asset}: no data fetched")
        return 0

    full_df = pd.concat(all_dfs).sort_index()
    full_df = full_df[~full_df.index.duplicated(keep="last")]

    csv_path = OUTPUT_DIR / f"data_{asset}_1h_max.csv"
    merged = merge_csv(full_df, csv_path)
    merged.to_csv(csv_path)
    log(f"  {asset} DONE: {len(merged):,} rows | {str(merged.index[0])[:10]} -> {str(merged.index[-1])[:10]}")
    return len(merged)

def main():
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        # Try to read from config
        try:
            import json
            cfg = json.load(open("/opt/sigma/config/secrets.json"))
            api_key = cfg.get("TWELVE_DATA_KEY") or cfg.get("twelve_data_key", "demo")
        except:
            api_key = "demo"

    log(f"Using API key: {api_key[:8]}...")

    # Test first with a small call
    df, err = fetch_1h_chunk("XAU/USD", api_key, "2024-01-01", "2024-01-10")
    if err:
        log(f"TEST FAILED: {err}")
        log("Get a free key at https://twelvedata.com/pricing (Free plan: 800 req/day)")
        return 1
    log(f"Test OK: {len(df)} rows")

    results = {}
    for asset, symbol_td in SYMBOLS.items():
        n = fetch_asset(asset, symbol_td, api_key)
        results[asset] = n

    log("\n=== SUMMARY ===")
    for k, v in results.items():
        log(f"  {k}: {v:,} rows")
    return 0

if __name__ == "__main__":
    sys.exit(main())
