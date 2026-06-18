"""
SIGMA ENGINE — Core Data Layer
Descarga, cachea y sirve datos OHLCV multi-timeframe.
Cache en disco para no re-descargar en cada run.
"""

import ccxt
import pandas as pd
import numpy as np
import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "results" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)


def _cache_key(symbol, tf, days):
    key = f"{symbol}_{tf}_{days}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _load_cache(symbol, tf, days):
    path = CACHE_DIR / f"{_cache_key(symbol, tf, days)}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
        print(f"  [CACHE] {tf} cargado desde disco ({len(df)} velas)")
        return df
    return None


def _save_cache(df, symbol, tf, days):
    path = CACHE_DIR / f"{_cache_key(symbol, tf, days)}.csv"
    df.to_csv(path)


def fetch_ohlcv(symbol=None, tf="15m", days=None, use_cache=True):
    """
    Descarga datos OHLCV de Binance Futures con cache diario.
    Prefiere archivos max descargados en models/data_{tf}_max.csv.
    """
    symbol = symbol or CONFIG["identity"]["symbol"]
    tf_cfg = CONFIG["timeframes"].get(tf, {})
    days   = days or tf_cfg.get("days_history", 180)

    # Preferir max CSV si disponible y tiene suficientes dias
    models_dir = Path(__file__).parent.parent.parent / "models"
    max_path = models_dir / f"data_{tf}_max.csv"
    if max_path.exists():
        df_max = pd.read_csv(max_path, index_col=0)
        df_max.index.name = "timestamp"
        # Convertir indice a DatetimeIndex timezone-naive (rapido)
        try:
            df_max.index = pd.to_datetime(df_max.index)
            if hasattr(df_max.index, 'tz') and df_max.index.tz is not None:
                df_max.index = df_max.index.tz_localize(None)
        except Exception:
            pass
        df_max = df_max[~df_max.index.duplicated(keep='last')].sort_index()
        if not isinstance(df_max.index, pd.DatetimeIndex):
            pass  # no es valido, continuar con API
        else:
            max_days = (df_max.index[-1] - df_max.index[0]).days
            stale_days = (pd.Timestamp.now() - df_max.index[-1]).days
            if max_days >= days * 0.9 and stale_days <= 2:
                print(f"  [CACHE] {tf} cargado desde disco ({len(df_max)} velas)")
                return df_max
            elif stale_days > 2:
                print(f"  [STALE] {tf} cache desactualizado ({stale_days}d) — forzando descarga")

    if use_cache:
        cached = _load_cache(symbol, tf, days)
        if cached is not None:
            return cached

    exchanges_to_try = [
        ("Binance Futures", lambda: ccxt.binance({
            "timeout": 30000,
            "options": {"defaultType": "future"}
        }), symbol),
        ("Binance Spot", lambda: ccxt.binance({"timeout": 30000}), symbol),
        ("Bybit", lambda: ccxt.bybit({
            "timeout": 30000,
            "options": {"defaultType": "linear"}
        }), "BTC/USDT:USDT"),
    ]

    since_str = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

    for name, make_ex, sym in exchanges_to_try:
        try:
            print(f"  [DATA] Descargando {sym} {tf} desde {name}...")
            ex = make_ex()
            since = ex.parse8601(since_str)
            all_ohlcv = []
            while True:
                ohlcv = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
                if not ohlcv:
                    break
                all_ohlcv += ohlcv
                since = ohlcv[-1][0] + 1
                if len(ohlcv) < 1000:
                    break
            if not all_ohlcv:
                continue
            df = pd.DataFrame(all_ohlcv,
                              columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            df = df.astype(float)
            df = df[~df.index.duplicated(keep="first")]
            df.sort_index(inplace=True)
            print(f"  [DATA] {len(df)} velas ({df.index[0].date()} a {df.index[-1].date()})")
            _, df = validate_ohlcv(df, fix=True, verbose=False)
            if use_cache:
                _save_cache(df, symbol, tf, days)
            return df
        except Exception as e:
            print(f"  [DATA] {name} fallo: {type(e).__name__}")
            continue

    raise RuntimeError(f"No se pudo descargar {symbol} {tf}. Verificar conexion.")


def fetch_multi_tf(tfs=None, symbol=None, use_cache=True):
    """
    Descarga multiples timeframes. Retorna dict {tf: DataFrame}.
    """
    if tfs is None:
        tfs = ["15m", "1h", "4h", "1d"]
    result = {}
    for tf in tfs:
        result[tf] = fetch_ohlcv(symbol=symbol, tf=tf, use_cache=use_cache)
    return result


def merge_htf(df_base, df_htf, cols, direction="backward"):
    """
    Hace merge_asof de columnas HTF al DataFrame base.
    """
    merged = pd.merge_asof(
        df_base.reset_index(),
        df_htf[cols].reset_index(),
        on="timestamp",
        direction=direction
    ).set_index("timestamp")
    for col in cols:
        if col in merged.columns and merged[col].dtype == object:
            merged[col] = merged[col].fillna(False)
        elif col in merged.columns:
            merged[col] = merged[col].fillna(False)
    return merged


def validate_ohlcv(df, fix=True, verbose=True):
    """
    Valida la calidad de un DataFrame OHLCV. Detecta y opcionalmente corrige:
      - Barras duplicadas
      - Gaps en la serie temporal (barras faltantes)
      - OHLC inconsistente (high < low, precio fuera de rango)
      - Volumen = 0
      - Retornos extremos (>20% en 1 barra = datos corruptos)

    fix=True: corrige problemas menores in-place (duplicados, gaps con ffill).
    Retorna dict con el reporte de calidad.
    """
    report = {"ok": True, "issues": [], "fixed": []}

    def warn(msg):
        report["issues"].append(msg)
        report["ok"] = False
        if verbose:
            print(f"  [WARN] {msg}")

    def fixed(msg):
        report["fixed"].append(msg)
        if verbose:
            print(f"  [FIX]  {msg}")

    if df is None or df.empty:
        warn("DataFrame vacío")
        return report

    # 1. Duplicados
    n_dup = df.index.duplicated().sum()
    if n_dup > 0:
        warn(f"{n_dup} timestamps duplicados")
        if fix:
            df = df[~df.index.duplicated(keep="first")]
            fixed(f"Eliminados {n_dup} duplicados")

    # 2. Gaps temporales
    if len(df) > 1:
        diffs  = df.index.to_series().diff().dropna()
        median = diffs.median()
        gaps   = (diffs > median * 2.5).sum()
        if gaps > 0:
            warn(f"{gaps} gaps en la serie temporal (barras faltantes)")
            if fix and gaps <= len(df) * 0.02:  # solo corregir si < 2% de los datos
                df = df.resample(median).asfreq()
                df = df.ffill()
                fixed(f"Gaps rellenados con forward-fill")

    # 3. OHLC inconsistente
    bad_hl  = (df["high"] < df["low"]).sum()
    bad_ho  = (df["high"] < df["open"]).sum()
    bad_hc  = (df["high"] < df["close"]).sum()
    bad_lo  = (df["low"]  > df["open"]).sum()
    bad_lc  = (df["low"]  > df["close"]).sum()
    ohlc_errors = bad_hl + bad_ho + bad_hc + bad_lo + bad_lc
    if ohlc_errors > 0:
        warn(f"{ohlc_errors} barras con OHLC inconsistente "
             f"(H<L:{bad_hl}, H<O:{bad_ho}, H<C:{bad_hc}, L>O:{bad_lo}, L>C:{bad_lc})")

    # 4. Volumen = 0
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > 0:
        warn(f"{zero_vol} barras con volumen = 0 ({zero_vol/len(df)*100:.1f}%)")

    # 5. Retornos extremos (>20% en 1 barra)
    ret = df["close"].pct_change().abs()
    extreme = (ret > 0.20).sum()
    if extreme > 0:
        warn(f"{extreme} barras con retorno >20% en 1 barra (datos posiblemente corruptos)")

    # 6. Precios negativos o cero
    zero_px = ((df[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum()
    if zero_px > 0:
        warn(f"{zero_px} barras con precio <= 0")

    if report["ok"] and verbose:
        print(f"  [OK] OHLCV validado: {len(df)} barras, sin problemas detectados")

    report["rows"]         = len(df)
    report["zero_volume"]  = int(zero_vol)
    report["ohlc_errors"]  = int(ohlc_errors)
    report["gaps"]         = int(gaps) if len(df) > 1 else 0

    return report, df if fix else df


def get_data_info(df):
    """Resumen rapido de un DataFrame OHLCV."""
    days = (df.index[-1] - df.index[0]).days
    return {
        "bars":      len(df),
        "days":      days,
        "months":    round(days / 30.44, 1),
        "from":      df.index[0].strftime("%Y-%m-%d"),
        "to":        df.index[-1].strftime("%Y-%m-%d"),
        "nan_pct":   df.isnull().sum().sum() / (len(df) * len(df.columns)) * 100,
    }
