"""
SIGMA ENGINE — Core Features Layer
Calcula TODOS los indicadores sobre cualquier timeframe.
Fuente unica de verdad para features — ningun otro modulo los recalcula.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)


# ─── UTILIDADES BASICAS ───────────────────────────────────────────────────────
def ema(s, n):     return s.ewm(span=n, adjust=False).mean()
def sma(s, n):     return s.rolling(n).mean()
def stdev(s, n):   return s.rolling(n).std()

def rsi(close, n=14):
    d  = close.diff()
    g  = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / ls.replace(0, np.nan))

def atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(high, low, close, n=14):
    up   = high.diff(); dn = -low.diff()
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    mdm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = atr(high, low, close, n)
    plus  = 100 * pd.Series(pdm, index=high.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    minus = 100 * pd.Series(mdm, index=high.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    dx    = 100 * (plus - minus).abs() / (plus + minus + 1e-9)
    return dx.ewm(alpha=1/n, adjust=False).mean(), plus, minus

def macd(close, fast=12, slow=26, signal=9):
    m = ema(close, fast) - ema(close, slow)
    s = ema(m, signal)
    return m, s, m - s


# ─── FEATURES PRINCIPALES ────────────────────────────────────────────────────
def build_features(df_base, htf_dict=None):
    """
    Calcula todos los features sobre df_base (cualquier TF).
    htf_dict: {'1h': df_1h, '4h': df_4h, '1d': df_1d}
    Retorna df enriquecido con todos los indicadores.
    """
    df = df_base.copy()
    # Normalize datetime index precision (pandas 2.x: ms vs us mismatch)
    if hasattr(df.index, 'as_unit'):
        df.index = df.index.as_unit('us')
    if htf_dict:
        htf_dict = {k: v.copy() for k, v in htf_dict.items()}
        for k in htf_dict:
            if hasattr(htf_dict[k].index, 'as_unit'):
                htf_dict[k].index = htf_dict[k].index.as_unit('us')
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

    # ── ATR ───────────────────────────────────────────────────────────────────
    df["atr"]      = atr(h, l, c, 14)
    df["atr50"]    = atr(h, l, c, 50)
    df["atr_ratio"]= df["atr"] / df["atr"].rolling(50).mean().replace(0, np.nan)

    # ── EMAs ──────────────────────────────────────────────────────────────────
    for n in [9, 20, 21, 50, 100, 200]:
        df[f"ema{n}"] = ema(c, n)

    df["bull"] = df["ema50"] > df["ema200"]
    df["bear"] = df["ema50"] < df["ema200"]
    df["trend_power"] = (df["ema50"] - df["ema200"]).abs()
    df["trend_gate"]  = df["trend_power"] > df["atr"] * 0.5

    # ── MACD ──────────────────────────────────────────────────────────────────
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)

    # ── RSI ───────────────────────────────────────────────────────────────────
    df["rsi"] = rsi(c, 14)

    # ── ADX ───────────────────────────────────────────────────────────────────
    df["adx"], df["di_plus"], df["di_minus"] = adx(h, l, c, 14)

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    sma20 = sma(c, 20); std20 = stdev(c, 20)
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    df["bb_mid"]   = sma20
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma20

    # ── Hurst exponent (proxy) ────────────────────────────────────────────────
    rn  = h.rolling(50, min_periods=10).max() - l.rolling(50, min_periods=10).min()
    rn2 = h.rolling(25, min_periods=5).max()  - l.rolling(25, min_periods=5).min()
    df["hurst"] = np.where(rn2 > 0,
                           np.log(rn / (rn2 + 1e-9)) / np.log(2.0),
                           0.5)

    # ── OFI (Order Flow Imbalance) ────────────────────────────────────────────
    body   = (c - o).abs() / (h - l + 1e-9)
    buy_v  = v * np.where(c > o, body, 0.0)
    sell_v = v * np.where(c < o, body, 0.0)
    bvs    = pd.Series(buy_v,  index=df.index).rolling(20).sum()
    svs    = pd.Series(sell_v, index=df.index).rolling(20).sum()
    tot    = bvs + svs
    df["ofi"]      = ((bvs - svs) / (tot + 1e-9)).ewm(span=3).mean()
    df["ofi_prev"] = df["ofi"].shift(3)
    df["ofi_accel"]= df["ofi"] - df["ofi_prev"]
    df["ofi_bull"] = df["ofi"] >  CFG["signals"]["ofi_threshold"]
    df["ofi_bear"] = df["ofi"] < -CFG["signals"]["ofi_threshold"]

    # ── CVD (Cumulative Volume Delta) ─────────────────────────────────────────
    delta          = np.where(c > o, v, np.where(c < o, -v, 0))
    df["cvd"]      = pd.Series(delta, index=df.index).cumsum()
    df["cvd_ma"]   = df["cvd"].rolling(20).mean()
    df["cvd_bull"] = df["cvd"] > df["cvd_ma"]
    df["cvd_accel"]= (df["cvd"] - df["cvd"].shift(3)) > (df["cvd"].shift(3) - df["cvd"].shift(6))

    # ── Volume ────────────────────────────────────────────────────────────────
    df["vol_ma"]    = v.rolling(20).mean()
    df["vol_ok"]    = v > df["vol_ma"] * 1.5
    df["vol_expand"]= df["atr_ratio"] > 1.2
    df["vol_comp"]  = df["atr_ratio"] < 0.85
    atr_min = df["atr"].rolling(100, min_periods=10).min()
    atr_max = df["atr"].rolling(100, min_periods=10).max()
    df["vol_pct"]   = ((df["atr"] - atr_min) / (atr_max - atr_min + 1e-9) * 100).clip(0, 100)

    # ── Regime ────────────────────────────────────────────────────────────────
    df["is_spike"]   = (h - l) > df["atr"] * 2.0
    df["is_trending"]= df["adx"] > 25
    df["is_ranging"] = (df["hurst"] < 0.50) & (df["adx"] < 20)
    df["is_volatile"]= (df["vol_pct"] > 75) | (df["atr_ratio"] > 1.4)
    df["regime_bull"]= df["is_trending"] & df["bull"] & (c > df["ema50"])
    df["regime_bear"]= df["is_trending"] & df["bear"] & (c < df["ema50"])
    df["is_trend_up"]  = (df["hurst"] > 0.55) & (df["adx"] > 25) & df["bull"]  & (c > df["ema50"])
    df["is_trend_down"]= (df["hurst"] > 0.55) & (df["adx"] > 25) & df["bear"]  & (c < df["ema50"])

    # ── Fake move / Liquidity sweep ───────────────────────────────────────────
    liq_up   = h > h.rolling(20).max().shift(1)
    liq_down = l < l.rolling(20).min().shift(1)
    df["fake_move"]    = (liq_up & (c < o)) | (liq_down & (c > o))
    df["liq_sweep_up"] = liq_up
    df["liq_sweep_dn"] = liq_down

    # ── Gap protection ────────────────────────────────────────────────────────
    is_gap = (o - c.shift()).abs() > df["atr"] * 2
    bsg    = []
    cnt    = 9999
    for ig in is_gap:
        if ig: cnt = 0
        bsg.append(cnt); cnt += 1
    df["bars_since_gap"] = bsg
    df["gap_ok"] = df["bars_since_gap"] >= CFG["signals"]["gap_protect_bars"]

    # ── Order Blocks ──────────────────────────────────────────────────────────
    ob_lb  = CFG["signals"].get("ob_lookback", 10)
    imp_up = (c.shift(ob_lb-1) > o.shift(ob_lb-1)) & \
             ((c.shift(ob_lb-1) - o.shift(ob_lb-1)) > df["atr"].shift(ob_lb-1) * 0.8)
    imp_dn = (c.shift(ob_lb-1) < o.shift(ob_lb-1)) & \
             ((o.shift(ob_lb-1) - c.shift(ob_lb-1)) > df["atr"].shift(ob_lb-1) * 0.8)
    ob_b   = (c.shift(ob_lb) < o.shift(ob_lb)) & imp_up
    ob_s   = (c.shift(ob_lb) > o.shift(ob_lb)) & imp_dn
    df["in_bull_ob"] = ob_b & (c <= o.shift(ob_lb)) & (c >= c.shift(ob_lb)) & df["bull"]
    df["in_bear_ob"] = ob_s & (c >= o.shift(ob_lb)) & (c <= c.shift(ob_lb)) & df["bear"]

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    fmn = CFG["signals"].get("fvg_min_size", 0.05)
    fvg_b = l > h.shift(2)
    fvg_s = h < l.shift(2)
    df["fvg_bull"]       = fvg_b & ((l - h.shift(2)) / c * 100 >= fmn)
    df["fvg_bear"]       = fvg_s & ((l.shift(2) - h) / c * 100 >= fmn)
    df["fill_bull_fvg"]  = (c <= l.shift(1)) & (c >= h.shift(3)) & df["bull"]
    df["fill_bear_fvg"]  = (c >= h.shift(1)) & (c <= l.shift(3)) & df["bear"]

    # ── Swing structure ───────────────────────────────────────────────────────
    sw_len = 10
    df["swing_h"]      = h.rolling(sw_len).max()
    df["swing_l"]      = l.rolling(sw_len).min()
    df["bos_bull"]     = (c > df["swing_h"].shift(sw_len)) & df["bull"]
    df["bos_bear"]     = (c < df["swing_l"].shift(sw_len)) & df["bear"]
    df["choch"]        = (df["bull"] & (c < df["swing_l"])) | (df["bear"] & (c > df["swing_h"]))
    rng_h = h.rolling(50).max(); rng_l = l.rolling(50).min()
    rng_m = (rng_h + rng_l) / 2
    df["in_premium"]   = (c > rng_m) & df["bear"]
    df["in_discount"]  = (c < rng_m) & df["bull"]

    # ── AVWAP (semanal) ───────────────────────────────────────────────────────
    tp     = (h + l + c) / 3
    df["week"] = df.index.isocalendar().week.values
    an_   = []; avd_  = []; pn_ = avdn_ = 0.0; pw_ = -1
    for i in range(len(df)):
        wk = df["week"].iloc[i]
        if wk != pw_: pn_ = 0.0; avdn_ = 0.0; pw_ = wk
        pn_ += tp.iloc[i] * v.iloc[i]; avdn_ += v.iloc[i]
        an_.append(pn_); avd_.append(avdn_)
    df["avwap"]       = np.array(an_) / np.maximum(np.array(avd_), 1e-9)
    df["above_avwap"] = c > df["avwap"]

    # ── Market temperature ────────────────────────────────────────────────────
    price_chg = (c - c.shift(3)).abs() / (df["atr"] * 3 + 1e-9) * 100
    rsi_heat  = (df["rsi"] - 50).abs() * 2
    df["mkt_temp"] = (
        price_chg.clip(0, 100) * 0.40 +
        df["vol_pct"]           * 0.30 +
        (df["atr_ratio"] * 50).clip(0, 100) * 0.10 +
        rsi_heat.clip(0, 100)   * 0.20
    ).clip(0, 100)
    df["mkt_dead"] = df["mkt_temp"] < 15

    # ── Momentum multi-TF (aproximacion con lags) ─────────────────────────────
    df["mom_ltf"] = (50 + (c - c.shift(3))  / c.shift(3).replace(0, np.nan) * 100 * 30).clip(0, 100)
    df["mom_1h"]  = (50 + (c - c.shift(4))  / c.shift(4).replace(0, np.nan) * 100 * 5).clip(0, 100)
    df["mom_4h"]  = (50 + (c - c.shift(16)) / c.shift(16).replace(0, np.nan) * 100 * 3).clip(0, 100)
    df["mom_all_bull"] = (df["mom_ltf"] > 55) & (df["mom_1h"] > 55) & (df["mom_4h"] > 55)
    df["mom_all_bear"] = (df["mom_ltf"] < 45) & (df["mom_1h"] < 45) & (df["mom_4h"] < 45)

    # ── RSI divergences ───────────────────────────────────────────────────────
    rll = df["rsi"].rolling(14).min(); rhh = df["rsi"].rolling(14).max()
    pll = l.rolling(14).min();         phh = h.rolling(14).max()
    df["bull_div"] = (l <= pll) & (df["rsi"] > rll.shift(1)) & (df["rsi"] > 30)
    df["bear_div"] = (h >= phh) & (df["rsi"] < rhh.shift(1)) & (df["rsi"] < 70)

    # ── Session / DOW ─────────────────────────────────────────────────────────
    df["hour"] = df.index.hour
    df["dow"]  = df.index.dayofweek  # 0=Lun, 6=Dom
    sess = CFG["sessions_utc"]
    df["in_london"]   = (df["hour"] >= sess["london"]["start"])   & (df["hour"] < sess["london"]["end"])
    df["in_new_york"] = (df["hour"] >= sess["new_york"]["start"]) & (df["hour"] < sess["new_york"]["end"])
    df["in_asia"]     = (df["hour"] >= sess["asia"]["start"])     & (df["hour"] < sess["asia"]["end"])
    df["in_session"]  = df["in_london"] | df["in_new_york"]

    # ── HTF features (si se proveen) ─────────────────────────────────────────
    if htf_dict:
        for tf_name, df_htf in htf_dict.items():
            htf = df_htf.copy()
            htf[f"ema50_{tf_name}"]  = ema(htf["close"], 50)
            htf[f"ema200_{tf_name}"] = ema(htf["close"], 200)
            htf[f"htf_long_{tf_name}"]  = htf[f"ema50_{tf_name}"] > htf[f"ema200_{tf_name}"]
            htf[f"htf_short_{tf_name}"] = htf[f"ema50_{tf_name}"] < htf[f"ema200_{tf_name}"]
            cols = [f"htf_long_{tf_name}", f"htf_short_{tf_name}"]
            df = pd.merge_asof(
                df.reset_index(),
                htf[cols].reset_index(),
                on="timestamp",
                direction="backward"
            ).set_index("timestamp")
            for col in cols:
                df[col] = df[col].fillna(False).astype(bool)

        # Aliases estandar para la primera y segunda HTF
        tf_keys = list(htf_dict.keys())
        if len(tf_keys) >= 1:
            k1 = tf_keys[0]
            df["htf1_long"]  = df.get(f"htf_long_{k1}",  pd.Series(True, index=df.index))
            df["htf1_short"] = df.get(f"htf_short_{k1}", pd.Series(False, index=df.index))
        if len(tf_keys) >= 2:
            k2 = tf_keys[1]
            df["htf2_long"] = df.get(f"htf_long_{k2}", pd.Series(True, index=df.index))
        else:
            df["htf2_long"] = pd.Series(True, index=df.index)
    else:
        # Sin HTF — asumir neutral
        for col in ["htf1_long", "htf1_short", "htf2_long"]:
            df[col] = pd.Series(True if "long" in col else False, index=df.index)

    # ── Smart / Elite signals ─────────────────────────────────────────────────
    df["smart_long"]  = (df["bull"] & df["trend_gate"] &
                         (df["macd"] > df["macd_signal"]) &
                         df["htf1_long"] & ~df["is_spike"])
    df["smart_short"] = (df["bear"] & df["trend_gate"] &
                         (df["macd"] < df["macd_signal"]) &
                         df["htf1_short"] & ~df["is_spike"])

    df["tf3_bull"]  = df["bull"]  & df["htf1_long"]  & df["htf2_long"]
    df["tf3_bear"]  = df["bear"]  & df["htf1_short"] & ~df["htf2_long"]

    df["elite_long"]  = (df["smart_long"]  & df["tf3_bull"] &
                         ~df["fake_move"]  & (df["rsi"] < 70))
    df["elite_short"] = (df["smart_short"] & df["tf3_bear"] &
                         ~df["fake_move"]  & (df["rsi"] > 30))

    df["eit_long"]  = (df["elite_long"]  &
                       (df["in_bull_ob"] | df["fill_bull_fvg"] | df["above_avwap"]))
    df["eit_short"] = (df["elite_short"] &
                       (df["in_bear_ob"] | df["fill_bear_fvg"] | ~df["above_avwap"]))

    return df.ffill().bfill()


def features_summary(df):
    """Resumen de features calculados para debugging."""
    bool_cols = [c for c in df.columns if df[c].dtype == bool]
    print(f"  Features: {len(df.columns)} columnas | {len(df)} filas")
    print(f"  NaN: {df.isnull().sum().sum()}")
    print(f"  Elite longs:  {df['eit_long'].sum():,}")
    print(f"  Elite shorts: {df['eit_short'].sum():,}")
    print(f"  Smart longs:  {df['smart_long'].sum():,}")
    print(f"  Smart shorts: {df['smart_short'].sum():,}")
    if "htf1_long" in df.columns:
        print(f"  HTF1 long:    {df['htf1_long'].sum():,}/{len(df):,} ({df['htf1_long'].mean()*100:.0f}%)")
