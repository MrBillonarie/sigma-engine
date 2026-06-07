"""
SIGMA ENGINE — Futures Market Data Layer
Descarga datos exclusivos de futuros desde Binance REST API (sin auth).

Datos disponibles:
  - Open Interest historico (1h/4h/1d) — confirma si el movimiento es real
  - Funding Rate historico (cada 8h) — detecta posicionamiento extremo
  - Taker Buy/Sell Volume real — reemplaza el CVD derivado de OHLCV
  - Long/Short Account Ratio retail — contraindicador de posicionamiento

Uso:
  from core.data_futures import fetch_all_futures_data
  futures = fetch_all_futures_data(period="1h", days=180)
  df = build_features(df_base, htf_dict=htfs, futures_dict=futures)
"""

import requests
import pandas as pd
import numpy as np
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_DIR  = Path(__file__).parent.parent / "results" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    _CFG = json.load(f)

SYMBOL   = _CFG["identity"]["symbol"].replace("/", "")   # BTCUSDT
BASE_URL = "https://fapi.binance.com"

_PERIOD_MS = {
    "5m":  5*60*1000,  "15m": 15*60*1000, "30m": 30*60*1000,
    "1h":  3600*1000,  "2h":  2*3600*1000, "4h": 4*3600*1000,
    "6h":  6*3600*1000,"8h":  8*3600*1000,"12h":12*3600*1000,
    "1d":  24*3600*1000,
}


# ─── CACHE HELPERS ────────────────────────────────────────────────────────────
def _cache_key(tag, period, days):
    raw = f"{tag}_{SYMBOL}_{period}_{days}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d')}"
    return "fut_" + hashlib.md5(raw.encode()).hexdigest()[:12]


def _load_cache(tag, period, days):
    path = CACHE_DIR / f"{_cache_key(tag, period, days)}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
        print(f"  [CACHE] {tag} {period} cargado ({len(df)} entradas)")
        return df
    return None


def _save_cache(df, tag, period, days):
    path = CACHE_DIR / f"{_cache_key(tag, period, days)}.csv"
    df.to_csv(path)


# ─── PAGINADOR GENERICO ────────────────────────────────────────────────────────
def _fetch_paginated(endpoint, base_params, days, ts_field="timestamp", max_per_req=500):
    """
    Pagina requests usando startTime/endTime.
    Binance devuelve max 500 entries por request en la mayoria de endpoints.
    """
    period     = base_params.get("period", "1h")
    period_ms  = _PERIOD_MS.get(period, 3600*1000)
    end_ts     = int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp() * 1000)
    start_ts   = int((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).timestamp() * 1000)

    all_rows   = []
    cursor     = start_ts
    retries    = 3

    while cursor < end_ts:
        batch_end = min(cursor + max_per_req * period_ms, end_ts)
        params    = {**base_params, "startTime": cursor, "endTime": batch_end, "limit": max_per_req}

        for attempt in range(retries):
            try:
                resp = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt == retries - 1:
                    print(f"  [WARN] {endpoint} fallo tras {retries} intentos: {e}")
                    return all_rows
                time.sleep(1)

        if not data:
            break

        if not isinstance(data, list):
            print(f"  [WARN] {endpoint} respuesta inesperada (error API): {data}")
            return all_rows

        all_rows.extend(data)

        # Avanzar al siguiente batch
        last = data[-1]
        last_ts = int(last[ts_field]) if isinstance(last, dict) else int(last[0])
        cursor  = last_ts + period_ms

        if len(data) < max_per_req:
            break

        time.sleep(0.12)   # respetar rate limit de Binance

    return all_rows


# ─── OPEN INTEREST ────────────────────────────────────────────────────────────
def fetch_open_interest(period="1h", days=180, use_cache=True):
    """
    Historial de Open Interest desde Binance Futures Data API.

    Columnas: open_interest (BTC), oi_value (USDT)

    OI subiendo + precio subiendo = tendencia real con nueva posiciones.
    OI bajando  + precio subiendo = short squeeze, sostenibilidad baja.
    """
    if use_cache:
        c = _load_cache("oi", period, days)
        if c is not None:
            return c

    print(f"  [DATA] Descargando Open Interest {SYMBOL} {period} ({days}d)...")
    rows = _fetch_paginated(
        "/futures/data/openInterestHist",
        {"symbol": SYMBOL, "period": period},
        days=days
    )

    if not rows:
        print("  [WARN] Sin datos de OI disponibles")
        return None

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.rename(columns={
        "sumOpenInterest":      "open_interest",
        "sumOpenInterestValue": "oi_value",
    })
    df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce")
    df["oi_value"]      = pd.to_numeric(df["oi_value"],      errors="coerce")
    df = df[["open_interest", "oi_value"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"  [DATA] OI: {len(df)} registros ({df.index[0].date()} → {df.index[-1].date()})")
    if use_cache:
        _save_cache(df, "oi", period, days)
    return df


# ─── FUNDING RATE ─────────────────────────────────────────────────────────────
def fetch_funding_rate(days=365, use_cache=True):
    """
    Historial de Funding Rate de Binance Futures (cada 8 horas).

    Columna: funding_rate (float, tipicamente 0.0001 = 0.01%)

    Funding > 0.05%:  longs pagando mucho → mercado sobrecomprado → contraindicador
    Funding < -0.03%: shorts pagando      → mercado sobrevendido  → contraindicador
    """
    period = "8h"
    if use_cache:
        c = _load_cache("funding", period, days)
        if c is not None:
            return c

    print(f"  [DATA] Descargando Funding Rate {SYMBOL} ({days}d)...")

    end_ts    = int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp() * 1000)
    start_ts  = int((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).timestamp() * 1000)
    period_ms = 8 * 3600 * 1000
    all_rows  = []
    cursor    = start_ts

    while cursor < end_ts:
        try:
            resp = requests.get(
                f"{BASE_URL}/fapi/v1/fundingRate",
                params={"symbol": SYMBOL, "startTime": cursor, "limit": 1000},
                timeout=20
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [WARN] Funding rate fallo: {e}")
            break

        if not data:
            break

        if not isinstance(data, list):
            print(f"  [WARN] Funding rate respuesta inesperada (error API): {data}")
            break

        all_rows.extend(data)
        cursor = int(data[-1]["fundingTime"]) + period_ms

        if len(data) < 100:
            break

        time.sleep(0.12)

    if not all_rows:
        print("  [WARN] Sin datos de funding rate")
        return None

    df = pd.DataFrame(all_rows)
    df["timestamp"]    = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df.set_index("timestamp", inplace=True)
    df = df[["funding_rate"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"  [DATA] Funding: {len(df)} registros ({df.index[0].date()} → {df.index[-1].date()})")
    if use_cache:
        _save_cache(df, "funding", period, days)
    return df


# ─── TAKER BUY/SELL VOLUME ────────────────────────────────────────────────────
def fetch_taker_volume(period="1h", days=180, use_cache=True):
    """
    Volumen real de takers (quien inicia la orden) desde Binance.
    Mucho mas preciso que el CVD derivado de OHLCV.

    Columnas: taker_ratio (buy/sell), taker_buy_vol, taker_sell_vol
    """
    if use_cache:
        c = _load_cache("taker", period, days)
        if c is not None:
            return c

    print(f"  [DATA] Descargando Taker Volume {SYMBOL} {period} ({days}d)...")
    rows = _fetch_paginated(
        "/futures/data/takerbuyLongShortRatio",
        {"symbol": SYMBOL, "period": period},
        days=days
    )

    if not rows:
        print("  [WARN] Sin datos de taker volume")
        return None

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df.set_index("timestamp", inplace=True)
    rename_map = {
        "buySellRatio": "taker_ratio",
        "buyVol":       "taker_buy_vol",
        "sellVol":      "taker_sell_vol",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    for col in ["taker_ratio", "taker_buy_vol", "taker_sell_vol"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    keep = [c for c in ["taker_ratio", "taker_buy_vol", "taker_sell_vol"] if c in df.columns]
    df = df[keep].sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"  [DATA] Taker: {len(df)} registros ({df.index[0].date()} → {df.index[-1].date()})")
    if use_cache:
        _save_cache(df, "taker", period, days)
    return df


# ─── LONG / SHORT RATIO ───────────────────────────────────────────────────────
def fetch_ls_ratio(period="1h", days=90, use_cache=True):
    """
    Ratio de cuentas Long vs Short de retail (Global Account Ratio).
    Contraindicador: extremo long retail = señal bajista institucional.

    Columnas: ls_ratio (long/short), ls_long (% long), ls_short (% short)
    """
    if use_cache:
        c = _load_cache("ls", period, days)
        if c is not None:
            return c

    print(f"  [DATA] Descargando L/S Ratio {SYMBOL} {period} ({days}d)...")
    rows = _fetch_paginated(
        "/futures/data/globalLongShortAccountRatio",
        {"symbol": SYMBOL, "period": period},
        days=days
    )

    if not rows:
        print("  [WARN] Sin datos de L/S ratio")
        return None

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df.set_index("timestamp", inplace=True)
    rename_map = {
        "longShortRatio": "ls_ratio",
        "longAccount":    "ls_long",
        "shortAccount":   "ls_short",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    for col in ["ls_ratio", "ls_long", "ls_short"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    keep = [c for c in ["ls_ratio", "ls_long", "ls_short"] if c in df.columns]
    df = df[keep].sort_index()
    df = df[~df.index.duplicated(keep="last")]

    print(f"  [DATA] L/S: {len(df)} registros ({df.index[0].date()} → {df.index[-1].date()})")
    if use_cache:
        _save_cache(df, "ls", period, days)
    return df


# ─── BUNDLE: TODOS LOS DATOS FUTURES ─────────────────────────────────────────
def fetch_all_futures_data(period="1h", days=180, use_cache=True):
    """
    Descarga los 4 datasets de futuros en un solo llamado.
    Cualquier fallo individual retorna None para ese dataset (graceful degradation).

    Retorna dict listo para pasar a build_features(futures_dict=...).
    """
    print(f"\n  [FUTURES DATA] Descargando datos de mercado de futuros...")
    result = {
        "oi":      fetch_open_interest(period=period, days=days, use_cache=use_cache),
        "funding": fetch_funding_rate(days=days, use_cache=use_cache),
        "taker":   fetch_taker_volume(period=period, days=days, use_cache=use_cache),
        "ls":      fetch_ls_ratio(period=period, days=min(days, 90), use_cache=use_cache),
    }
    available = [k for k, v in result.items() if v is not None]
    print(f"  [FUTURES DATA] Disponibles: {available}")
    return result
