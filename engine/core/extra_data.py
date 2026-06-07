"""
SIGMA ENGINE — Datos Extras Gratuitos
Descarga y cachea datos adicionales de Binance Futures (gratis):

  1. Funding Rate historico (2019 → hoy) — señal crypto unica
  2. Open Interest (ultimos 30 dias) — confirmacion de tendencia
  3. Long/Short Ratio (ultimos 30 dias) — sentimiento del mercado
  4. Taker Buy/Sell Volume (ultimos 30 dias) — agresividad compradores/vendedores

Por que importan:
  Funding Rate: cuando es extremo (+ve o -ve), el mercado esta crowded →
                señal de reversal de alta probabilidad
  Open Interest: subiendo + precio subiendo = tendencia real (no fake breakout)
  L/S Ratio: >1.5 todos en longs = inminente short squeeze (contrarian)
  Taker Volume: mas buy takers = institucionales comprando agresivamente
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
import requests
import pandas as pd
import numpy as np
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

OUTPUT_DIR = Path(__file__).parent.parent.parent
MODELS_DIR = OUTPUT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTCUSDT"


# ─── FUNDING RATE COMPLETO (2019 → HOY) ─────────────────────────────────────
def fetch_funding_rate_full():
    """
    Descarga el historial completo de funding rate desde 2019.
    Se actualiza cada 8h automaticamente.
    """
    out_path = MODELS_DIR / "data_funding_full.csv"

    # Si existe, solo actualizar desde el ultimo registro
    start_ts = None
    if out_path.exists():
        df_exist = pd.read_csv(out_path, index_col=0, parse_dates=True)
        if len(df_exist) > 0:
            last_ts = df_exist.index[-1]
            # Solo descargar si han pasado mas de 8h
            if (datetime.now(timezone.utc).replace(tzinfo=None) - last_ts).total_seconds() < 8*3600:
                print(f"  [FUNDING] Datos actualizados (ultimo: {last_ts})")
                return df_exist
            start_ts = int(last_ts.timestamp() * 1000) + 1
            print(f"  [FUNDING] Actualizando desde {last_ts.strftime('%Y-%m-%d')}...")
        frames = [df_exist]
    else:
        print("  [FUNDING] Descargando historial completo desde 2019...")
        frames = []

    ex = ccxt.binance({'timeout': 30000, 'options': {'defaultType': 'future'}})
    if start_ts is None:
        start_ts = ex.parse8601('2019-09-01T00:00:00Z')

    all_records = []
    consecutive_empty = 0

    while True:
        try:
            batch = ex.fetch_funding_rate_history('BTC/USDT', since=start_ts, limit=1000)
            if not batch:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                time.sleep(1)
                continue

            consecutive_empty = 0
            all_records.extend(batch)
            start_ts = batch[-1]['timestamp'] + 1

            if len(batch) < 1000:
                break

            time.sleep(0.2)  # rate limit
        except Exception as e:
            print(f"  [FUNDING] Error: {e}")
            break

    if not all_records and not frames:
        print("  [FUNDING] Sin datos")
        return None

    if all_records:
        df_new = pd.DataFrame(all_records)
        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'], unit='ms')
        df_new.set_index('timestamp', inplace=True)
        df_new = df_new[['fundingRate']].rename(columns={'fundingRate': 'funding_rate'})
        df_new['funding_rate'] = df_new['funding_rate'].astype(float)
        frames.append(df_new)

    df_all = pd.concat(frames)
    df_all = df_all[~df_all.index.duplicated(keep='last')].sort_index()

    df_all.to_csv(out_path)
    days = (df_all.index[-1] - df_all.index[0]).days
    print(f"  [FUNDING] {len(df_all):,} registros | {days}d ({days/365:.1f}y) | "
          f"{df_all.index[0].strftime('%Y-%m-%d')} -> {df_all.index[-1].strftime('%Y-%m-%d')}")
    return df_all


# ─── OPEN INTEREST (ultimos 30 dias, multiples TF) ──────────────────────────
def fetch_open_interest(period="4h", limit=500):
    """Descarga Open Interest historico (Binance solo tiene ~30 dias)."""
    out_path = MODELS_DIR / f"data_oi_{period}.csv"

    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": SYMBOL, "period": period, "limit": limit}

    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['oi'] = df['openInterest'].astype(float)
        df['oi_usd'] = df['openInterestValue'].astype(float)
        df = df[['oi', 'oi_usd']].sort_index()

        # Combinar con existente si hay
        if out_path.exists():
            df_exist = pd.read_csv(out_path, index_col=0, parse_dates=True)
            df = pd.concat([df_exist, df])
            df = df[~df.index.duplicated(keep='last')].sort_index()

        df.to_csv(out_path)
        print(f"  [OI {period}] {len(df):,} registros | "
              f"{df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')}")
        return df
    except Exception as e:
        print(f"  [OI] Error: {e}")
        return None


# ─── LONG/SHORT RATIO ───────────────────────────────────────────────────────
def fetch_long_short_ratio(period="4h", limit=500):
    """Ratio de cuentas long vs short (sentimiento de mercado)."""
    out_path = MODELS_DIR / f"data_ls_ratio_{period}.csv"

    url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
    params = {"symbol": SYMBOL, "period": period, "limit": limit}

    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['ls_ratio'] = df['longShortRatio'].astype(float)
        df['long_pct'] = df['longAccount'].astype(float)
        df['short_pct'] = df['shortAccount'].astype(float)
        df = df[['ls_ratio','long_pct','short_pct']].sort_index()

        if out_path.exists():
            df_exist = pd.read_csv(out_path, index_col=0, parse_dates=True)
            df = pd.concat([df_exist, df])
            df = df[~df.index.duplicated(keep='last')].sort_index()

        df.to_csv(out_path)
        print(f"  [L/S {period}] {len(df):,} registros | "
              f"{df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')}")
        return df
    except Exception as e:
        print(f"  [L/S] Error: {e}")
        return None


# ─── TAKER BUY/SELL VOLUME ──────────────────────────────────────────────────
def fetch_taker_volume(period="4h", limit=500):
    """Volumen de compradores/vendedores agresivos (takers)."""
    out_path = MODELS_DIR / f"data_taker_{period}.csv"

    url = "https://fapi.binance.com/futures/data/takerlongshortRatio"
    params = {"symbol": SYMBOL, "period": period, "limit": limit}

    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['taker_buy_vol'] = df['buyVol'].astype(float)
        df['taker_sell_vol'] = df['sellVol'].astype(float)
        df['taker_ratio'] = df['buySellRatio'].astype(float)
        df = df[['taker_buy_vol','taker_sell_vol','taker_ratio']].sort_index()

        if out_path.exists():
            df_exist = pd.read_csv(out_path, index_col=0, parse_dates=True)
            df = pd.concat([df_exist, df])
            df = df[~df.index.duplicated(keep='last')].sort_index()

        df.to_csv(out_path)
        print(f"  [TAKER {period}] {len(df):,} registros | "
              f"{df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')}")
        return df
    except Exception as e:
        print(f"  [TAKER] Error: {e}")
        return None


# ─── DESCARGA COMPLETA ───────────────────────────────────────────────────────
def fetch_all_extra():
    """Descarga todos los datos extras disponibles."""
    print(f"\n{'='*60}")
    print("  SIGMA — DATOS EXTRA (Binance Futures, gratis)")
    print(f"{'='*60}\n")

    results = {}

    # Funding rate (lo mas valioso — historia completa desde 2019)
    print("[1/4] Funding Rate historico (2019 -> hoy)...")
    df_fr = fetch_funding_rate_full()
    if df_fr is not None:
        results['funding'] = len(df_fr)
        # Calcular estadisticas utiles
        fr_mean = df_fr['funding_rate'].mean()
        fr_std  = df_fr['funding_rate'].std()
        fr_p90  = df_fr['funding_rate'].quantile(0.90)
        fr_p10  = df_fr['funding_rate'].quantile(0.10)
        print(f"  Media: {fr_mean:.5f} | Std: {fr_std:.5f}")
        print(f"  Extremo bullish (p90): {fr_p90:.5f} | Extremo bearish (p10): {fr_p10:.5f}")

    # Open Interest (multiples TFs)
    print("\n[2/4] Open Interest...")
    for tf in ["1h", "4h", "1d"]:
        fetch_open_interest(period=tf)

    # Long/Short ratio
    print("\n[3/4] Long/Short Ratio...")
    for tf in ["1h", "4h"]:
        fetch_long_short_ratio(period=tf)

    # Taker volume
    print("\n[4/4] Taker Buy/Sell Volume...")
    for tf in ["1h", "4h"]:
        fetch_taker_volume(period=tf)

    print(f"\n{'='*60}")
    print("  Todos los datos extra descargados")
    print(f"  Guardados en: {MODELS_DIR}")
    print(f"{'='*60}")

    return results


# ─── MERGE CON DATAFRAME PRINCIPAL ──────────────────────────────────────────
def enrich_df(df, tf="1h"):
    """
    Agrega todos los datos extras disponibles al DataFrame principal.
    Uso: df_enriquecido = enrich_df(df_base, tf='1h')
    """
    enriched = df.copy()

    # Funding rate → alinear a las barras del TF
    fr_path = MODELS_DIR / "data_funding_full.csv"
    if fr_path.exists():
        df_fr = pd.read_csv(fr_path, index_col=0, parse_dates=True)
        df_fr = df_fr[~df_fr.index.duplicated(keep='last')].sort_index()

        enriched = pd.merge_asof(
            enriched.reset_index(),
            df_fr.reset_index(),
            on='timestamp',
            direction='backward'
        ).set_index('timestamp')

        # Features derivadas del funding rate
        fr = enriched['funding_rate'].fillna(0)
        fr_roll = fr.rolling(500, min_periods=50)
        enriched['fr_z_score']       = (fr - fr_roll.mean()) / fr_roll.std().clip(lower=1e-8)
        enriched['fr_extreme_long']  = fr > fr.rolling(500,min_periods=50).quantile(0.90)
        enriched['fr_extreme_short'] = fr < fr.rolling(500,min_periods=50).quantile(0.10)
        enriched['fr_neutral']       = (~enriched['fr_extreme_long']) & (~enriched['fr_extreme_short'])

    # Open Interest
    oi_path = MODELS_DIR / f"data_oi_{tf}.csv"
    if oi_path.exists():
        df_oi = pd.read_csv(oi_path, index_col=0, parse_dates=True)
        enriched = pd.merge_asof(
            enriched.reset_index(),
            df_oi.reset_index(),
            on='timestamp', direction='backward'
        ).set_index('timestamp')
        if 'oi' in enriched.columns:
            enriched['oi_change'] = enriched['oi'].pct_change(4)
            enriched['oi_rising'] = enriched['oi_change'] > 0.02  # OI sube >2%

    # Long/Short ratio
    ls_path = MODELS_DIR / f"data_ls_ratio_{tf}.csv"
    if ls_path.exists():
        df_ls = pd.read_csv(ls_path, index_col=0, parse_dates=True)
        enriched = pd.merge_asof(
            enriched.reset_index(),
            df_ls.reset_index(),
            on='timestamp', direction='backward'
        ).set_index('timestamp')
        if 'ls_ratio' in enriched.columns:
            enriched['ls_crowded_long']  = enriched['ls_ratio'] > 1.5
            enriched['ls_crowded_short'] = enriched['ls_ratio'] < 0.7

    return enriched


if __name__ == "__main__":
    fetch_all_extra()
