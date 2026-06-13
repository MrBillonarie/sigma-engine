#!/usr/bin/env python3
"""
SIGMA Motor 2 - IBKR Historical Data Fetcher
Downloads 5+ years of 1H and 4H data for all Motor 2 commodities.
Requires IB Gateway running on localhost:4001.
"""
import sys, os, time, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, '/opt/sigma')

OUTPUT_DIR = Path('/opt/sigma/models')

# Commodity futures contracts
CONTRACTS = {
    'XAU': ('COMEX', 'GC', 'FUT', 'USD'),   # Gold
    'XAG': ('COMEX', 'SI', 'FUT', 'USD'),   # Silver
    'WTI': ('NYMEX', 'CL', 'FUT', 'USD'),   # Crude Oil
    'HG':  ('COMEX', 'HG', 'FUT', 'USD'),   # Copper
    'NG':  ('NYMEX', 'NG', 'FUT', 'USD'),   # Natural Gas
    'PL':  ('NYMEX', 'PL', 'FUT', 'USD'),   # Platinum
}

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def get_contract(ib, sym, exchange, sec_type='FUT'):
    from ib_insync import Future
    if sec_type == 'FUT':
        # Use continuous contract (conId) or front month
        # For continuous adjusted data, use underlying symbol
        contract = Future(sym, exchange=exchange, currency='USD')
    return contract

def fetch_historical(ib, sym, exchange, what='TRADES', duration='5 Y', bar_size='1 hour'):
    from ib_insync import Future, util
    contract = Future(sym, exchange=exchange, currency='USD')
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow=what,
        useRTH=False,   # Include extended hours (overnight session for commodities)
        formatDate=1,
    )
    if not bars:
        return None
    df = util.df(bars)
    df = df.rename(columns={'date': 'timestamp', 'open': 'open', 'high': 'high',
                             'low': 'low', 'close': 'close', 'volume': 'volume'})
    df.set_index('timestamp', inplace=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
    return df

def merge_with_existing(new_df, csv_path):
    """Merge new data with existing CSV, keeping all historical rows."""
    if not csv_path.exists():
        return new_df
    try:
        old_df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        old_df.index = pd.to_datetime(old_df.index, utc=True)
        base_cols = [c for c in ['open','high','low','close','volume'] if c in old_df.columns]
        combined = pd.concat([old_df[base_cols], new_df]).sort_index()
        combined = combined[~combined.index.duplicated(keep='last')]
        log(f"  Merged: {len(old_df)} + {len(new_df)} -> {len(combined)} rows")
        return combined
    except Exception as e:
        log(f"  Merge WARN: {e}, using new data only")
        return new_df

def main():
    try:
        from ib_insync import IB
    except ImportError:
        log("ERROR: ib_insync not installed")
        return 1

    log("Connecting to IB Gateway on 127.0.0.1:4001...")
    ib = IB()
    try:
        ib.connect('127.0.0.1', 4001, clientId=25, timeout=20)
        log(f"Connected! Account: {ib.managedAccounts()}")
    except Exception as e:
        log(f"ERROR connecting: {e}")
        return 1

    results = {}
    for asset, (exchange, sym, sec_type, currency) in CONTRACTS.items():
        log(f"\n--- {asset} ({sym} @ {exchange}) ---")

        # 1H data: 5 years
        for tf_label, duration, bar_size in [
            ('1h', '5 Y', '1 hour'),
            ('4h', '5 Y', '4 hours'),
        ]:
            try:
                log(f"  Fetching {tf_label} ({duration})...")
                df = fetch_historical(ib, sym, exchange, duration=duration, bar_size=bar_size)
                if df is None or len(df) < 10:
                    log(f"  {tf_label}: no data returned")
                    continue

                csv_path = OUTPUT_DIR / f'data_{asset}_{tf_label}_max.csv'
                df_save = merge_with_existing(df, csv_path)
                df_save.to_csv(csv_path)

                log(f"  {tf_label}: {len(df_save):,} rows | {str(df_save.index[0])[:10]} -> {str(df_save.index[-1])[:10]}")
                results[f'{asset}/{tf_label}'] = len(df_save)
                time.sleep(1)  # Rate limit
            except Exception as e:
                log(f"  {tf_label}: ERROR {e}")

        time.sleep(2)  # Between assets

    ib.disconnect()
    log("\n=== Download Complete ===")
    for k, v in sorted(results.items()):
        log(f"  {k}: {v:,} rows")
    return 0

if __name__ == '__main__':
    sys.exit(main())
