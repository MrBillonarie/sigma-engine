#!/usr/bin/env python3
"""
fetch_dukascopy_commodity.py
Generaliza fetch_dukascopy_xau.py / fetch_dukascopy_xag.py para los commodities
de Motor 2 que solo tienen ~2 anos de historia 15m via yfinance (limite de 60 dias
por descarga -- nunca pueden recuperar el pasado). Descarga ticks historicos
gratis de Dukascopy y los agrega a 5m/15m/1h/4h, igual que se hizo para XAU/XAG.

Codigos Dukascopy confirmados en vivo (2026-06-26, ver catalogo oficial via
proyecto dukascopy-node) -- no son adivinanza, se probaron contra el datafeed real:
  HG  (cobre)        -> COPPERCMDUSD  (ticks desde 2012-03-02)
  WTI (crudo liviano) -> LIGHTCMDUSD  (ticks desde 2013-01-01)
  NG  (gas natural)   -> GASCMDUSD    (ticks desde 2012-09-02)
  PL  (platino)       -> XPTCMDUSD    (ticks desde 2021-11-01 -- historia mas corta)

Uso: python fetch_dukascopy_commodity.py HG
     python fetch_dukascopy_commodity.py WTI
     python fetch_dukascopy_commodity.py NG
     python fetch_dukascopy_commodity.py PL
"""
import os, sys, lzma, json, time, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import requests
import pandas as pd
import numpy as np

OUTPUT_DIR = Path('/opt/sigma/models')
CACHE_DIR  = Path('/tmp/duka_cache')
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer':    'https://www.dukascopy.com/',
}
MAX_WORKERS = 4  # politeness -- bajamos de 6 a 4 tras ver 503s con trafico concurrente

# sym SIGMA -> (codigo dukascopy, decimal_factor, fecha desde la que hay ticks
# disponibles en Dukascopy -- backfill a la maxima profundidad real, no a un
# minimo arbitrario. Es un backfill de una sola vez corriendo en background sin
# competir por CPU (medido: 1.7% CPU, el costo es solo tiempo de espera, no
# recursos) -- mas historia siempre ayuda a Optuna/walk-forward (cubre mas
# regimenes de mercado), no hay razon para cortarlo corto.
# PL es la excepcion real: Dukascopy solo tiene ticks de PL desde 2021-11-01
# (limite duro de la fuente, no una eleccion nuestra).
INSTRUMENTS = {
    'HG':  ('COPPERCMDUSD', 10000.0, datetime(2012, 3, 2,  tzinfo=timezone.utc)),
    'WTI': ('LIGHTCMDUSD',  1000.0,  datetime(2013, 1, 1,  tzinfo=timezone.utc)),
    'NG':  ('GASCMDUSD',    10000.0, datetime(2012, 9, 2,  tzinfo=timezone.utc)),
    'PL':  ('XPTCMDUSD',    1000.0,  datetime(2021, 11, 1, tzinfo=timezone.utc)),
}

_local = threading.local()

def get_session():
    if not hasattr(_local, 'session'):
        from requests.adapters import HTTPAdapter
        s = requests.Session()
        s.headers.update(HEADERS)
        s.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _local.session = s
    return _local.session


def duka_url(duka_sym, year, month0, day, hour):
    return f"https://datafeed.dukascopy.com/datafeed/{duka_sym}/{year}/{month0:02d}/{day:02d}/{hour:02d}h_ticks.bi5"


def fetch_hour_ticks(duka_sym, price_div, year, month0, day, hour, retries=2):
    cache_key = f"{duka_sym}_{year}_{month0:02d}_{day:02d}_{hour:02d}.pkl"
    cache_path = CACHE_DIR / cache_key

    if cache_path.exists():
        sz = cache_path.stat().st_size
        if sz == 0:
            return None
        try:
            return pd.read_pickle(str(cache_path))
        except Exception:
            cache_path.unlink(missing_ok=True)

    url = duka_url(duka_sym, year, month0, day, hour)
    sess = get_session()
    for attempt in range(retries + 1):
        try:
            time.sleep(0.25)
            r = sess.get(url, timeout=20)
            if r.status_code in (404, 204) or len(r.content) == 0:
                cache_path.write_bytes(b'')
                return None
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(2 + attempt * 2)
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
                'mid':    (data['ask'].astype(float) + data['bid'].astype(float)) / (2 * price_div),
                'volume': data['av'].astype(float) + data['bv'].astype(float),
            })
            df.to_pickle(str(cache_path))
            return df

        except requests.RequestException:
            if attempt < retries:
                time.sleep(2 + attempt * 2)
            else:
                return None
    return None


def ticks_to_ohlcv(df_ticks, freq):
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


def iter_months(start_dt, end_dt):
    current = start_dt
    while current < end_dt:
        yield current.strftime('%Y-%m')
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)


def fetch_month(duka_sym, price_div, year, month1):
    if month1 == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month1 + 1, 1, tzinfo=timezone.utc)
    start = datetime(year, month1, 1, tzinfo=timezone.utc)

    hours = list(iter_hours(start, end))
    bars = {'1h': [], '15m': [], '5m': []}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_hour_ticks, duka_sym, price_div, y, m0, d, h): (y, m0, d, h)
                   for y, m0, d, h in hours}
        for fut in as_completed(futures):
            try:
                ticks = fut.result()
                if ticks is None or len(ticks) == 0:
                    continue
                for tf, freq in (('1h', '1h'), ('15m', '15min'), ('5m', '5min')):
                    df_tf = ticks_to_ohlcv(ticks, freq)
                    for _, row in df_tf.iterrows():
                        bars[tf].append({'timestamp': row['ts'], 'open': row['open'],
                                         'high': row['high'], 'low': row['low'],
                                         'close': row['close'], 'volume': row['volume']})
            except Exception:
                pass

    return bars


def merge_and_save(sym, tf, duka_bars, label=""):
    if not duka_bars:
        return 0

    df_duka = pd.DataFrame(duka_bars)
    df_duka['timestamp'] = pd.to_datetime(df_duka['timestamp'], utc=True)
    df_duka = df_duka.set_index('timestamp').sort_index()
    df_duka = df_duka[['open', 'high', 'low', 'close', 'volume']]

    csv_path = OUTPUT_DIR / f'data_{sym}_{tf}_max.csv'
    if csv_path.exists():
        df_yf = pd.read_csv(str(csv_path), index_col=0, parse_dates=True)
        if hasattr(df_yf.index, 'tz') and df_yf.index.tz is not None:
            df_yf.index = df_yf.index.tz_convert('UTC')
        else:
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize('UTC', ambiguous='infer', nonexistent='shift_forward')
        df_yf = df_yf[['open', 'high', 'low', 'close', 'volume']]
        # Dukascopy para historia, yfinance para lo reciente (ultimos ~60d que ya
        # vienen del fetcher normal y son mas frescos/confiables para el presente)
        cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=45)
        df_merged = pd.concat([df_duka[df_duka.index < cutoff],
                                df_yf[df_yf.index >= cutoff]]).sort_index()
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')]
    else:
        df_merged = df_duka

    df_merged.index = df_merged.index.tz_localize(None)
    df_merged.index.name = 'timestamp'
    df_merged.to_csv(str(csv_path))
    log.info(f"  [{label}] {sym} {tf}: {len(df_merged):,} bars ({str(df_merged.index[0])[:10]} -> {str(df_merged.index[-1])[:10]})")
    return len(df_merged)


def save_all_tfs(sym, all_bars_dict, label=""):
    merge_and_save(sym, '5m',  all_bars_dict['5m'],  label)
    merge_and_save(sym, '15m', all_bars_dict['15m'], label)
    merge_and_save(sym, '1h',  all_bars_dict['1h'],  label)
    try:
        df_1h = pd.read_csv(str(OUTPUT_DIR / f'data_{sym}_1h_max.csv'), index_col=0, parse_dates=True)
        df_4h = df_1h.resample('4h', label='left', closed='left').agg(
            open=('open', 'first'), high=('high', 'max'),
            low=('low', 'min'), close=('close', 'last'),
            volume=('volume', 'sum')
        ).dropna(subset=['close'])
        df_4h.index.name = 'timestamp'
        df_4h.to_csv(str(OUTPUT_DIR / f'data_{sym}_4h_max.csv'))
        log.info(f"  [{label}] {sym} 4h: {len(df_4h):,} bars (resampled from 1h)")
    except Exception as e:
        log.warning(f"  4h resample error: {e}")


def main():
    if len(sys.argv) < 2 or sys.argv[1].upper() not in INSTRUMENTS:
        print(f"Uso: {sys.argv[0]} <{'|'.join(INSTRUMENTS.keys())}>")
        sys.exit(1)
    sym = sys.argv[1].upper()
    duka_sym, price_div, history_start = INSTRUMENTS[sym]

    # lock simple por simbolo -- evita que dos invocaciones (ej. una manual en
    # paralelo + la del wrapper secuencial llegando mas tarde a este mismo
    # simbolo) corran a la vez y se pisen escribiendo el mismo progress.json/CSV
    lock_path = Path(f'/tmp/duka_lock_{sym}')
    if lock_path.exists():
        print(f"{sym}: ya hay un proceso corriendo (lock {lock_path} existe) -- saliendo sin hacer nada")
        sys.exit(0)
    lock_path.write_text(str(os.getpid()))
    import atexit
    atexit.register(lambda: lock_path.unlink(missing_ok=True))

    global log, PROGRESS_F
    LOG_PATH = Path(f'/opt/sigma/results/reports/dukascopy_fetch_{sym}.log')
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S',
        handlers=[logging.FileHandler(str(LOG_PATH), mode='a'), logging.StreamHandler(sys.stdout)]
    )
    log = logging.getLogger()
    PROGRESS_F = Path(f'/tmp/dukascopy_progress_{sym}.json')

    log.info("=" * 60)
    log.info(f"DUKASCOPY {sym} ({duka_sym}) FETCHER -- 5m/15m/1h/4h desde {history_start.date()}")

    END = datetime.now(timezone.utc).replace(day=1) - timedelta(days=45)
    END = END.replace(hour=0, minute=0, second=0, microsecond=0)

    progress = {'completed_months': [], 'bars_1h': [], 'bars_15m': [], 'bars_5m': []}
    if PROGRESS_F.exists():
        try:
            progress = json.loads(PROGRESS_F.read_text())
        except Exception:
            pass
    completed = set(progress['completed_months'])
    log.info(f"Resume: {len(completed)} meses ya completados")

    pending = [m for m in iter_months(history_start, END) if m not in completed]
    log.info(f"{len(pending)} meses por descargar (de {history_start.date()} a {END.date()})")

    all_bars = {
        '1h':  list(progress.get('bars_1h', [])),
        '15m': list(progress.get('bars_15m', [])),
        '5m':  list(progress.get('bars_5m', [])),
    }

    current = history_start
    save_every = 12  # guarda a disco cada 12 meses procesados (no solo al final)
    months_since_save = 0
    while current < END:
        month_key = current.strftime('%Y-%m')
        if month_key not in completed:
            t0 = time.time()
            bars = fetch_month(duka_sym, price_div, current.year, current.month)
            all_bars['1h'].extend(bars['1h'])
            all_bars['15m'].extend(bars['15m'])
            all_bars['5m'].extend(bars['5m'])
            completed.add(month_key)
            elapsed = time.time() - t0
            log.info(f"  {month_key}: {len(bars['1h'])}x1h / {len(bars['15m'])}x15m / {len(bars['5m'])}x5m en {elapsed:.0f}s")
            progress['completed_months'] = list(completed)
            progress['bars_1h']  = all_bars['1h']
            progress['bars_15m'] = all_bars['15m']
            progress['bars_5m']  = all_bars['5m']
            PROGRESS_F.write_text(json.dumps(progress, default=str))
            months_since_save += 1
            if months_since_save >= save_every:
                save_all_tfs(sym, all_bars, "PARTIAL")
                months_since_save = 0

        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)

    save_all_tfs(sym, all_bars, "FINAL")
    log.info(f"DONE {sym} -- {len(all_bars['1h']):,} barras 1h totales descargadas")

    import shutil
    shutil.rmtree(str(CACHE_DIR), ignore_errors=True)


if __name__ == '__main__':
    main()
