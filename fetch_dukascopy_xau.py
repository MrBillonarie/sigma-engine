#!/usr/bin/env python3
"""
fetch_dukascopy_xau.py
Descarga datos historicos XAUUSD desde Dukascopy (gratis, desde 2014).
Genera: data_XAU_5m, 15m, 1h, 4h _max.csv con 10+ años de historia.

Arquitectura: descarga ticks horarios, los agrega a 5m/15m/1h/4h en una sola pasada.
Fase 1: 2014-2024 (rapido, ~30s/mes)
Fase 2: 2004-2013 (lento, servidor old, corre overnight)
"""
import os, sys, struct, lzma, json, time, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import requests
import pandas as pd
import numpy as np

OUTPUT_DIR  = Path('/opt/sigma/models')
LOG_PATH    = Path('/opt/sigma/results/reports/dukascopy_fetch.log')
PROGRESS_F  = Path('/tmp/dukascopy_progress.json')
CACHE_DIR   = Path('/tmp/duka_cache')
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer':    'https://www.dukascopy.com/',
}
PRICE_DIV   = 1000.0   # XAU/USD: raw_int / 1000 = USD price
MAX_WORKERS = 6        # polite — Dukascopy throttles >10 concurrent

_local = threading.local()

def get_session():
    if not hasattr(_local, 'session'):
        from requests.adapters import HTTPAdapter
        s = requests.Session()
        s.headers.update(HEADERS)
        s.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _local.session = s
    return _local.session


LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(str(LOG_PATH), mode='a'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger()


def duka_url(year, month0, day, hour):
    return f"https://datafeed.dukascopy.com/datafeed/XAUUSD/{year}/{month0:02d}/{day:02d}/{hour:02d}h_ticks.bi5"


def fetch_hour_ticks(year, month0, day, hour, retries=2):
    """Download one hour of tick data. Returns df with (timestamp, mid, volume) or None."""
    cache_key = f"{year}_{month0:02d}_{day:02d}_{hour:02d}.pkl"
    cache_path = CACHE_DIR / cache_key

    if cache_path.exists():
        sz = cache_path.stat().st_size
        if sz == 0:
            return None
        try:
            return pd.read_pickle(str(cache_path))
        except Exception:
            cache_path.unlink(missing_ok=True)

    url = duka_url(year, month0, day, hour)
    sess = get_session()
    for attempt in range(retries + 1):
        try:
            time.sleep(0.15)
            r = sess.get(url, timeout=15)
            if r.status_code in (404, 204) or len(r.content) == 0:
                cache_path.write_bytes(b'')
                return None
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                return None
            try:
                raw = lzma.decompress(r.content)
            except lzma.LZMAError:
                return None
            n = len(raw) // 20
            if n == 0:
                cache_path.write_bytes(b'')
                return None

            data = np.frombuffer(raw[:n*20], dtype=np.dtype([
                ('ms',  '>u4'), ('ask', '>u4'), ('bid', '>u4'),
                ('av',  '>f4'), ('bv',  '>f4')
            ]))
            hour_start = datetime(year, month0 + 1, day, hour, tzinfo=timezone.utc)
            timestamps = pd.to_datetime(
                [hour_start + timedelta(milliseconds=int(ms)) for ms in data['ms']]
            )
            df = pd.DataFrame({
                'timestamp': timestamps,
                'mid':    (data['ask'].astype(float) + data['bid'].astype(float)) / (2 * PRICE_DIV),
                'volume': data['av'].astype(float) + data['bv'].astype(float),
            })
            df.to_pickle(str(cache_path))
            return df

        except requests.RequestException:
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                return None
    return None


def ticks_to_ohlcv(df_ticks, freq):
    """Aggregate tick DataFrame to OHLCV at given pandas offset frequency (e.g. '5min', '15min', '1h')."""
    if df_ticks is None or len(df_ticks) == 0:
        return pd.DataFrame()
    df = df_ticks.set_index('timestamp').sort_index()
    ohlcv = df['mid'].resample(freq).agg(
        open='first', high='max', low='min', close='last'
    ).dropna()
    vol = df['volume'].resample(freq).sum()
    ohlcv['volume'] = vol
    return ohlcv.reset_index().rename(columns={'timestamp': 'ts'})


def iter_hours(start_dt, end_dt):
    dt = start_dt
    while dt < end_dt:
        wd = dt.weekday()
        if wd == 5:
            dt += timedelta(hours=1)
            continue
        if wd == 6 and dt.hour < 22:
            dt += timedelta(hours=1)
            continue
        yield dt.year, dt.month - 1, dt.day, dt.hour
        dt += timedelta(hours=1)


def _iter_months(start_dt, end_dt):
    current = start_dt
    while current < end_dt:
        yield current.strftime('%Y-%m')
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)


def load_progress():
    if PROGRESS_F.exists():
        try:
            return json.loads(PROGRESS_F.read_text())
        except Exception:
            pass
    return {'completed_months': [], 'bars_1h': [], 'bars_15m': [], 'bars_5m': []}


def save_progress(progress):
    PROGRESS_F.write_text(json.dumps(progress, default=str))


def fetch_month(year, month1):
    """Fetch all hours in a calendar month. Returns dict of OHLCV lists per TF."""
    if month1 == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month1 + 1, 1, tzinfo=timezone.utc)
    start = datetime(year, month1, 1, tzinfo=timezone.utc)

    hours = list(iter_hours(start, end))
    bars = {'1h': [], '15m': [], '5m': []}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_hour_ticks, y, m0, d, h): (y, m0, d, h)
                   for y, m0, d, h in hours}
        for fut in as_completed(futures):
            y, m0, d, h = futures[fut]
            try:
                ticks = fut.result()
                if ticks is None or len(ticks) == 0:
                    continue

                # 1h bar
                df_1h = ticks_to_ohlcv(ticks, '1h')
                for _, row in df_1h.iterrows():
                    bars['1h'].append({'timestamp': row['ts'], 'open': row['open'],
                                       'high': row['high'], 'low': row['low'],
                                       'close': row['close'], 'volume': row['volume']})
                # 15m bars
                df_15m = ticks_to_ohlcv(ticks, '15min')
                for _, row in df_15m.iterrows():
                    bars['15m'].append({'timestamp': row['ts'], 'open': row['open'],
                                        'high': row['high'], 'low': row['low'],
                                        'close': row['close'], 'volume': row['volume']})
                # 5m bars
                df_5m = ticks_to_ohlcv(ticks, '5min')
                for _, row in df_5m.iterrows():
                    bars['5m'].append({'timestamp': row['ts'], 'open': row['open'],
                                       'high': row['high'], 'low': row['low'],
                                       'close': row['close'], 'volume': row['volume']})
            except Exception:
                pass

    return bars


def merge_and_save(tf, duka_bars, label=""):
    """Merge Dukascopy bars with existing yfinance CSV and save."""
    if not duka_bars:
        return 0

    df_duka = pd.DataFrame(duka_bars)
    df_duka['timestamp'] = pd.to_datetime(df_duka['timestamp'], utc=True)
    df_duka = df_duka.set_index('timestamp').sort_index()
    df_duka = df_duka[['open', 'high', 'low', 'close', 'volume']]

    csv_path = OUTPUT_DIR / f'data_XAU_{tf}_max.csv'
    if csv_path.exists():
        df_yf = pd.read_csv(str(csv_path), index_col=0, parse_dates=True)
        # Normalize timezone
        if hasattr(df_yf.index, 'tz') and df_yf.index.tz is not None:
            df_yf.index = df_yf.index.tz_convert('UTC')
        else:
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize('UTC', ambiguous='infer', nonexistent='shift_forward')
        df_yf = df_yf[['open', 'high', 'low', 'close', 'volume']]
        # Use Dukascopy for history, yfinance for recent
        cutoff = pd.Timestamp('2024-01-17', tz='UTC')
        df_merged = pd.concat([df_duka[df_duka.index < cutoff],
                                df_yf[df_yf.index >= cutoff]]).sort_index()
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')]
    else:
        df_merged = df_duka

    # Save as timezone-naive for compatibility
    df_merged.index = df_merged.index.tz_localize(None)
    df_merged.index.name = 'timestamp'
    df_merged.to_csv(str(csv_path))
    log.info(f"  [{label}] XAU {tf}: {len(df_merged):,} bars ({str(df_merged.index[0])[:10]}->{str(df_merged.index[-1])[:10]})")
    return len(df_merged)


def save_all_tfs(all_bars_dict, label=""):
    """Save 5m, 15m, 1h, and regenerate 4h from 1h."""
    merge_and_save('5m',  all_bars_dict['5m'],  label)
    merge_and_save('15m', all_bars_dict['15m'], label)
    n_1h = merge_and_save('1h', all_bars_dict['1h'], label)

    # Regenerate 4h from updated 1h
    try:
        df_1h = pd.read_csv(str(OUTPUT_DIR / 'data_XAU_1h_max.csv'), index_col=0, parse_dates=True)
        df_4h = df_1h.resample('4h', label='left', closed='left').agg(
            open=('open', 'first'), high=('high', 'max'),
            low=('low', 'min'), close=('close', 'last'),
            volume=('volume', 'sum')
        ).dropna(subset=['close'])
        df_4h.index.name = 'timestamp'
        df_4h.to_csv(str(OUTPUT_DIR / 'data_XAU_4h_max.csv'))
        log.info(f"  [{label}] XAU 4h: {len(df_4h):,} bars (resampled from 1h)")
    except Exception as e:
        log.warning(f"  4h resample error: {e}")


def fetch_range_and_save(start_dt, end_dt, progress):
    """Download months in range, save progress and intermediate CSVs."""
    all_bars = {
        '1h':  list(progress.get('bars_1h', [])),
        '15m': list(progress.get('bars_15m', [])),
        '5m':  list(progress.get('bars_5m', [])),
    }
    completed = set(progress['completed_months'])
    current = start_dt

    while current < end_dt:
        month_key = current.strftime('%Y-%m')
        if month_key not in completed:
            t0 = time.time()
            bars = fetch_month(current.year, current.month)
            all_bars['1h'].extend(bars['1h'])
            all_bars['15m'].extend(bars['15m'])
            all_bars['5m'].extend(bars['5m'])
            completed.add(month_key)
            elapsed = time.time() - t0
            n1h = len(bars['1h'])
            log.info(f"  {month_key}: {n1h}×1h / {len(bars['15m'])}×15m / {len(bars['5m'])}×5m in {elapsed:.0f}s")
            progress['completed_months'] = list(completed)
            progress['bars_1h']  = all_bars['1h']
            progress['bars_15m'] = all_bars['15m']
            progress['bars_5m']  = all_bars['5m']
            save_progress(progress)

        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)

    return all_bars, progress


def main():
    log.info("=" * 60)
    log.info("DUKASCOPY XAU FETCHER — 5m/15m/1h/4h desde 2014")

    PHASE1_START = datetime(2014, 1, 1, tzinfo=timezone.utc)
    PHASE2_START = datetime(2004, 1, 1, tzinfo=timezone.utc)
    END          = datetime(2024, 1, 1, tzinfo=timezone.utc)

    progress = load_progress()
    completed = set(progress['completed_months'])
    log.info(f"Resume: {len(completed)} months done")

    # Phase 1: 2014-2024 (fast, ~30s/month)
    p1_pending = [m for m in _iter_months(PHASE1_START, END) if m not in completed]
    if p1_pending:
        log.info(f"Phase 1 (2014-2024): {len(p1_pending)} months to fetch")
        all_bars, progress = fetch_range_and_save(PHASE1_START, END, progress)
        save_all_tfs(all_bars, "PHASE1")
        log.info("Phase 1 COMPLETE — M2 now trains on 2014-2026 data!")
    else:
        log.info("Phase 1 already complete")
        # Still need bars in memory for phase 2 merge
        all_bars = {
            '1h':  list(progress.get('bars_1h', [])),
            '15m': list(progress.get('bars_15m', [])),
            '5m':  list(progress.get('bars_5m', [])),
        }

    # Phase 2: 2004-2013 (slower, overnight)
    p2_pending = [m for m in _iter_months(PHASE2_START, PHASE1_START) if m not in completed]
    if p2_pending:
        log.info(f"Phase 2 (2004-2013): {len(p2_pending)} months pending (slow server)")
        all_bars, progress = fetch_range_and_save(PHASE2_START, PHASE1_START, progress)
        save_all_tfs(all_bars, "PHASE2")
    else:
        log.info("Phase 2 already complete")

    total_1h = len(progress.get('bars_1h', []))
    log.info(f"DONE — {total_1h:,} total 1h bars downloaded")

    import shutil
    shutil.rmtree(str(CACHE_DIR), ignore_errors=True)


if __name__ == '__main__':
    main()
