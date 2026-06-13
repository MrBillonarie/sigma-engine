"""
commodity_strategies.py — Librería hedge-fund para commodities.
v2: 38 estrategias (19L+19S) + routing por familia de activo.

Routing:
  XAU, XAG, PL  -> precious_metals  (universal + DXY/haven/sessions)
  WTI, NG        -> energy           (universal + seasonal/NG/supply_shock)
  HG             -> industrial       (universal + copper_cycle/COT_proxy/supply_shock)

Escala automáticamente: cada familia ve ~28 estrategias relevantes, no las 38.
Con más CPU -> pipeline.py aumenta MAX_PARALLEL, sin tocar este archivo.
"""
import pandas as pd
import numpy as np

apply_regime_gate = None
_apply_cd = None

def _bind(ap_mod):
    global apply_regime_gate, _apply_cd
    apply_regime_gate = ap_mod.apply_regime_gate
    _apply_cd         = ap_mod._apply_cd

def _gl(df, bs, slm, tpm, cd):
    bl = pd.Series(False, index=df.index)
    bl, bs = apply_regime_gate(df, bl, bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)

def _gs(df, bs, slm, tpm, cd):
    bl = pd.Series(False, index=df.index)
    bl, bs = apply_regime_gate(df, bl, bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)

def _season_month(df):
    idx = df.index
    if hasattr(idx, 'month'):
        return pd.Series(idx.month, index=idx)
    return pd.to_datetime(idx).month

def _session_hour(df):
    idx = pd.to_datetime(df.index, utc=True)
    return pd.Series(idx.hour, index=df.index)

def _macro(df, col):
    return df[col] if col in df.columns else pd.Series(np.nan, index=df.index)

COMMODITY_STRATEGIES = []

# =============================================================================
#  GRUPO 1: TURTLE / DONCHIAN — el clasico de commodities
# =============================================================================

def sig_turtle_breakout_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("donchian_lb", 40))
    lb2 = int(p.get("expand_lb", 5))
    prev_high = df["high"].shift(1).rolling(lb).max()
    breakout  = df["close"] > prev_high
    recent_low = df["low"].rolling(lb // 2).min().shift(1)
    not_recent_loss = df["close"] > recent_low * p.get("loss_buffer", 0.98)
    atr_expand = df["atr"] > df["atr"].rolling(lb).mean() * p.get("atr_mult", 0.9)
    bs = breakout & not_recent_loss & atr_expand & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("turtle_breakout_long")

def sig_turtle_breakout_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("donchian_lb", 40))
    prev_low  = df["low"].shift(1).rolling(lb).min()
    breakdown = df["close"] < prev_low
    atr_expand = df["atr"] > df["atr"].rolling(lb).mean() * p.get("atr_mult", 0.9)
    bs = breakdown & atr_expand & (df["htf_bear"].fillna(False) | (df["rsi_w"] < p.get("rsi_w_thr", 45)))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("turtle_breakout_short")

# =============================================================================
#  GRUPO 2: Z-SCORE MEAN REVERSION — commodities revierten al costo de produccion
# =============================================================================

def sig_zscore_revert_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    win = int(p.get("zscore_win", 30)); thr = p.get("zscore_thr", -2.0)
    c = df["close"]
    z = (c - c.rolling(win).mean()) / (c.rolling(win).std() + 1e-9)
    cross_up = (z.shift(1) < thr) & (z >= thr)
    bull_bar = (df["close"] > df["open"]) & (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.0))
    bs = cross_up & bull_bar & (df["regime_range"].fillna(False) | (df["rsi14"] < p.get("rsi_lo", 35)))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("zscore_revert_long")

def sig_zscore_revert_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    win = int(p.get("zscore_win", 30)); thr = p.get("zscore_thr", 2.0)
    c = df["close"]
    z = (c - c.rolling(win).mean()) / (c.rolling(win).std() + 1e-9)
    cross_dn = (z.shift(1) > thr) & (z <= thr)
    bear_bar = (df["close"] < df["open"]) & (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.0))
    bs = cross_dn & bear_bar & (df["regime_range"].fillna(False) | (df["rsi14"] > p.get("rsi_hi", 65)))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("zscore_revert_short")

# =============================================================================
#  GRUPO 3: SESSION BREAKOUT — commodities reaccionan a aperturas de mercado
# =============================================================================

def sig_ny_session_breakout_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    hour = _session_hour(df)
    in_ny = hour.isin(range(int(p.get("ny_open_h", 13)), int(p.get("ny_close_h", 16))))
    pre_high = df["high"].shift(1).rolling(int(p.get("pre_bars", 6))).max()
    breakout = (df["close"] > pre_high) & in_ny
    atr_ok = df["atr"] > df["atr"].rolling(20).mean() * p.get("atr_mult", 0.85)
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)
    htf_ok = df["htf_bull"].fillna(False) | df["htf_range"].fillna(False)
    bs = breakout & atr_ok & vol_ok & htf_ok
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("ny_session_breakout_long")

def sig_ny_session_breakout_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    hour = _session_hour(df)
    in_ny = hour.isin(range(int(p.get("ny_open_h", 13)), int(p.get("ny_close_h", 16))))
    pre_low = df["low"].shift(1).rolling(int(p.get("pre_bars", 6))).min()
    breakdown = (df["close"] < pre_low) & in_ny
    atr_ok = df["atr"] > df["atr"].rolling(20).mean() * p.get("atr_mult", 0.85)
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)
    bs = breakdown & atr_ok & vol_ok & (df["htf_bear"].fillna(False) | (df["rsi_w"] < p.get("rsi_w_thr", 48)))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("ny_session_breakout_short")

def sig_london_open_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    hour = _session_hour(df)
    in_ldn = hour.isin(range(int(p.get("ldn_open_h", 7)), int(p.get("ldn_close_h", 10))))
    asian_high = df["high"].shift(1).rolling(int(p.get("asian_bars", 8))).max()
    breakout = (df["close"] > asian_high) & in_ldn
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.15)
    bs = breakout & vol_ok & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("london_open_long")

def sig_london_open_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    hour = _session_hour(df)
    in_ldn = hour.isin(range(int(p.get("ldn_open_h", 7)), int(p.get("ldn_close_h", 10))))
    asian_low = df["low"].shift(1).rolling(int(p.get("asian_bars", 8))).min()
    breakdown = (df["close"] < asian_low) & in_ldn
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.15)
    bs = breakdown & vol_ok & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("london_open_short")

# =============================================================================
#  GRUPO 4: MACRO MOMENTUM — fondos macro siguen tendencias largas
# =============================================================================

def sig_macro_momentum_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    lb = int(p.get("roc_lb", 60)); lb2 = int(p.get("roc_lb2", 20))
    roc_long  = (c / c.shift(lb) - 1) * 100
    roc_short = (c / c.shift(lb2) - 1) * 100
    bull = (roc_long > p.get("roc_lo", 2.0)) & (roc_short > p.get("roc_so", 0.5))
    structural = df["close"] > df["ema200"]
    bs = bull & structural & (df["rsi14"] < p.get("rsi_hi", 70)) & \
         (df["rsi14"] > p.get("rsi_lo", 45)) & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("macro_momentum_long")

def sig_macro_momentum_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    lb = int(p.get("roc_lb", 60)); lb2 = int(p.get("roc_lb2", 20))
    roc_long  = (c / c.shift(lb) - 1) * 100
    roc_short = (c / c.shift(lb2) - 1) * 100
    bear = (roc_long < p.get("roc_lo", -2.0)) & (roc_short < p.get("roc_so", -0.5))
    structural = df["close"] < df["ema200"]
    bs = bear & structural & (df["rsi14"] > p.get("rsi_lo", 30)) & \
         (df["rsi14"] < p.get("rsi_hi", 55)) & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("macro_momentum_short")

# =============================================================================
#  GRUPO 5: KELTNER CHANNEL — mejor que BB para commodities trending
# =============================================================================

def sig_keltner_trend_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    ema_p = int(p.get("kc_ema", 20)); mult = p.get("kc_mult", 1.5)
    mid   = df["close"].ewm(span=ema_p).mean()
    upper = mid + mult * df["atr"]
    kc_break = df["close"] > upper
    had_pullback = df["low"].shift(1).rolling(5).min() < mid.shift(1)
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1)
    bs = kc_break & had_pullback & vol_ok & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("keltner_trend_long")

def sig_keltner_trend_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    ema_p = int(p.get("kc_ema", 20)); mult = p.get("kc_mult", 1.5)
    mid   = df["close"].ewm(span=ema_p).mean()
    lower = mid - mult * df["atr"]
    kc_break = df["close"] < lower
    had_pullback = df["high"].shift(1).rolling(5).max() > mid.shift(1)
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1)
    bs = kc_break & had_pullback & vol_ok & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("keltner_trend_short")

# =============================================================================
#  GRUPO 6: ESTACIONALIDAD — commodities tienen ciclos anuales reales
# =============================================================================

def sig_energy_seasonal_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    month = _season_month(df)
    s = int(p.get("bull_month_start", 5)); e = int(p.get("bull_month_end", 9))
    in_season = (month >= s) & (month <= e) if s <= e else (month >= s) | (month <= e)
    momentum = (df["close"] > df["ema50"]) & (df["ema50"] > df["ema50"].shift(5))
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.0)
    bs = in_season & momentum & vol_ok & (df["rsi14"] < p.get("rsi_hi", 65))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("energy_seasonal_long")

def sig_energy_seasonal_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    month = _season_month(df)
    s = int(p.get("bear_month_start", 10)); e = int(p.get("bear_month_end", 3))
    in_season = (month >= s) & (month <= e) if s <= e else (month >= s) | (month <= e)
    momentum_bear = (df["close"] < df["ema50"]) & (df["ema50"] < df["ema50"].shift(5))
    bs = in_season & momentum_bear & (df["rsi14"] > p.get("rsi_lo", 35)) & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("energy_seasonal_short")

# =============================================================================
#  GRUPO 7: DXY DIVERGENCE — metales preciosos inversos al dolar
# =============================================================================

def sig_dxy_weakness_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    dxy = _macro(df, "dxy")
    if dxy.isna().all():
        bull = (df["close"] > df["ema50"]) & (df["ema21"] > df["ema21"].shift(3))
    else:
        dxy_falling = dxy < dxy.rolling(int(p.get("dxy_lb", 20))).mean()
        dxy_accel   = dxy < dxy.shift(int(p.get("dxy_shift", 5)))
        bull = dxy_falling & dxy_accel
    price_bull = (df["close"] > df["ema50"]) & (df["rsi14"] < p.get("rsi_hi", 65))
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1)
    bs = bull & price_bull & vol_ok & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("dxy_weakness_long")

def sig_dxy_strength_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    dxy = _macro(df, "dxy")
    if dxy.isna().all():
        bear = (df["close"] < df["ema50"]) & (df["ema21"] < df["ema21"].shift(3))
    else:
        dxy_rising = dxy > dxy.rolling(int(p.get("dxy_lb", 20))).mean()
        dxy_accel  = dxy > dxy.shift(int(p.get("dxy_shift", 5)))
        bear = dxy_rising & dxy_accel
    price_bear = (df["close"] < df["ema50"]) & (df["rsi14"] > p.get("rsi_lo", 35))
    bs = bear & price_bear & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("dxy_strength_short")

# =============================================================================
#  GRUPO 8: COPPER CYCLE — HG lidera el ciclo economico global
# =============================================================================

def sig_copper_cycle_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    lb = int(p.get("cycle_lb", 60)); lb2 = int(p.get("cycle_lb2", 20))
    roc = (c / c.shift(lb) - 1) * 100
    roc_improving  = (roc > roc.shift(lb2)) & (roc > p.get("roc_thr", -5))
    above_support  = c > c.rolling(lb).min() * p.get("support_mult", 1.02)
    bs = roc_improving & above_support & (df["rsi14"] > p.get("rsi_lo", 40)) & \
         (df["ema21"] > df["ema21"].shift(int(p.get("ema_shift", 5))))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("copper_cycle_long")

def sig_copper_cycle_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    lb = int(p.get("cycle_lb", 60)); lb2 = int(p.get("cycle_lb2", 20))
    roc = (c / c.shift(lb) - 1) * 100
    roc_deteriorating = (roc < roc.shift(lb2)) & (roc < p.get("roc_thr", 5))
    below_resistance  = c < c.rolling(lb).max() * p.get("res_mult", 0.98)
    bs = roc_deteriorating & below_resistance & (df["rsi14"] < p.get("rsi_hi", 60)) & \
         (df["ema21"] < df["ema21"].shift(int(p.get("ema_shift", 5)))) & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("copper_cycle_short")

# =============================================================================
#  GRUPO 9: ATR COMPRESSION — muy efectivo en energia
# =============================================================================

def sig_atr_compression_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("compress_lb", 30)); lb2 = int(p.get("expand_lb", 5))
    atr_low   = df["atr"].rolling(lb).quantile(p.get("atr_q", 0.20))
    compressed = df["atr"].rolling(lb // 3).mean() < atr_low
    expanding  = df["atr"] > df["atr"].shift(1) * p.get("atr_jump", 1.4)
    new_high   = df["close"] > df["high"].shift(1).rolling(lb2).max()
    rsi_ok     = df["rsi14"] < p.get("rsi_hi", 72)
    bs = compressed.shift(lb2).fillna(False) & expanding & new_high & rsi_ok
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("atr_compression_long")

def sig_atr_compression_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("compress_lb", 30)); lb2 = int(p.get("expand_lb", 5))
    atr_low    = df["atr"].rolling(lb).quantile(p.get("atr_q", 0.20))
    compressed = df["atr"].rolling(lb // 3).mean() < atr_low
    expanding  = df["atr"] > df["atr"].shift(1) * p.get("atr_jump", 1.4)
    new_low    = df["close"] < df["low"].shift(1).rolling(lb2).min()
    rsi_ok     = df["rsi14"] > p.get("rsi_lo", 28)
    bs = compressed.shift(lb2).fillna(False) & expanding & new_low & rsi_ok
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("atr_compression_short")

# =============================================================================
#  GRUPO 10: SAFE HAVEN / RISK-OFF — exclusivo metales preciosos
# =============================================================================

def sig_safe_haven_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]; o = df["open"]
    body_pct   = (c - o) / (df["atr"] + 1e-9)
    strong_bull = body_pct > p.get("body_atr", 1.0)
    vol_spike  = df["volume"] > df["vol_ma"] * p.get("vol_spike", 2.0)
    near_ema   = df["low"].rolling(3).min() <= df["ema21"] * p.get("ema_tol", 1.015)
    bs = strong_bull & vol_spike & (near_ema | (df["rsi14"] < p.get("rsi_lo", 52))) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("safe_haven_long")

def sig_risk_on_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]; o = df["open"]
    body_bear  = (o - c) / (df["atr"] + 1e-9)
    strong_bear = body_bear > p.get("body_atr", 0.8)
    vol_spike  = df["volume"] > df["vol_ma"] * p.get("vol_spike", 1.8)
    overextended = df["close"] > df["ema21"] * p.get("ema_ext", 1.005)
    bs = strong_bear & vol_spike & overextended & df["htf_bear"].fillna(False) & \
         (df["rsi14"] > p.get("rsi_hi", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("risk_on_short")

# =============================================================================
#  GRUPO 11: PRICE STRUCTURE — acumulacion/distribucion institucional
# =============================================================================

def sig_higher_low_structure_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb  = int(p.get("swing_lb", 10))
    local_low = df["low"].rolling(lb, center=True).min() == df["low"]
    lows = df["low"].where(local_low)
    low1 = lows.ffill(); low2 = lows.shift(lb).ffill()
    higher_low = (low1 > low2) & local_low
    trend_up   = df["close"] > df["ema50"]
    bs = higher_low & trend_up & (df["rsi14"] > p.get("rsi_lo", 40)) & df["htf_bull"].fillna(False)
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("higher_low_structure_long")

def sig_lower_high_structure_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb  = int(p.get("swing_lb", 10))
    local_high = df["high"].rolling(lb, center=True).max() == df["high"]
    highs = df["high"].where(local_high)
    high1 = highs.ffill(); high2 = highs.shift(lb).ffill()
    lower_high = (high1 < high2) & local_high
    trend_dn   = df["close"] < df["ema50"]
    bs = lower_high & trend_dn & (df["rsi14"] < p.get("rsi_hi", 60)) & df["htf_bear"].fillna(False)
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("lower_high_structure_short")

# =============================================================================
#  GRUPO 12: VOLUME CLIMAX — reversiones en extremos de volumen
# =============================================================================

def sig_volume_climax_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    vol_extreme = df["volume"] > df["vol_ma"] * p.get("vol_extreme", 3.0)
    bear_bar    = (df["open"] - df["close"]) > df["atr"] * p.get("bar_atr", 0.8)
    next_bull   = df["close"] > df["open"]
    rsi_low     = df["rsi14"] < p.get("rsi_lo", 32)
    bs = vol_extreme.shift(1) & bear_bar.shift(1) & next_bull & rsi_low
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("volume_climax_long")

def sig_volume_climax_short(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    vol_extreme = df["volume"] > df["vol_ma"] * p.get("vol_extreme", 3.0)
    bull_bar    = (df["close"] - df["open"]) > df["atr"] * p.get("bar_atr", 0.8)
    next_bear   = df["close"] < df["open"]
    rsi_high    = df["rsi14"] > p.get("rsi_hi", 68)
    bs = vol_extreme.shift(1) & bull_bar.shift(1) & next_bear & rsi_high
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("volume_climax_short")

# =============================================================================
#  GRUPO 13: BOLLINGER SQUEEZE — compresion antes de expansion
# =============================================================================

def sig_bollinger_squeeze_long(df, p):
    """Long: BBands comprimidas N barras -> precio rompe banda superior."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb   = int(p.get("bb_period", 20)); mult = p.get("bb_mult", 2.0)
    sma  = df["close"].rolling(lb).mean()
    std  = df["close"].rolling(lb).std()
    upper = sma + mult * std; lower = sma - mult * std
    bb_width  = (upper - lower) / (sma + 1e-9)
    squeeze_lb = int(p.get("squeeze_lb", 60))
    squeezed  = bb_width < bb_width.rolling(squeeze_lb).quantile(p.get("squeeze_q", 0.20))
    breakout  = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1))
    vol_ok    = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.3)
    bs = squeezed.shift(1).fillna(False) & breakout & vol_ok & (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("bollinger_squeeze_long")

def sig_bollinger_squeeze_short(df, p):
    """Short: BBands comprimidas -> precio rompe banda inferior."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb   = int(p.get("bb_period", 20)); mult = p.get("bb_mult", 2.0)
    sma  = df["close"].rolling(lb).mean()
    std  = df["close"].rolling(lb).std()
    upper = sma + mult * std; lower = sma - mult * std
    bb_width  = (upper - lower) / (sma + 1e-9)
    squeeze_lb = int(p.get("squeeze_lb", 60))
    squeezed  = bb_width < bb_width.rolling(squeeze_lb).quantile(p.get("squeeze_q", 0.20))
    breakdown = (df["close"] < lower) & (df["close"].shift(1) >= lower.shift(1))
    vol_ok    = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.3)
    bs = squeezed.shift(1).fillna(False) & breakdown & vol_ok & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("bollinger_squeeze_short")

# =============================================================================
#  GRUPO 14: VWAP DEVIATION — entrada institucional en desvios de VWAP
# =============================================================================

def sig_vwap_deviation_long(df, p):
    """Long: precio muy por debajo de VWAP rolling -> zona de compra institucional."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("vwap_period", 24))
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical * df["volume"]).rolling(lb).sum() / (df["volume"].rolling(lb).sum() + 1e-9)
    dev  = (df["close"] - vwap) / (vwap + 1e-9)
    oversold_dev = dev < -p.get("dev_thr", 0.012)
    rsi_lo  = df["rsi14"] < p.get("rsi_lo", 40)
    turning = df["close"] > df["close"].shift(1)
    bs = oversold_dev & rsi_lo & turning & (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("vwap_deviation_long")

def sig_vwap_deviation_short(df, p):
    """Short: precio muy por encima de VWAP rolling -> zona de venta institucional."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("vwap_period", 24))
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical * df["volume"]).rolling(lb).sum() / (df["volume"].rolling(lb).sum() + 1e-9)
    dev  = (df["close"] - vwap) / (vwap + 1e-9)
    overbought_dev = dev > p.get("dev_thr", 0.012)
    rsi_hi  = df["rsi14"] > p.get("rsi_hi", 60)
    turning = df["close"] < df["close"].shift(1)
    bs = overbought_dev & rsi_hi & turning & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("vwap_deviation_short")

# =============================================================================
#  GRUPO 15: FIBONACCI RETRACEMENT — niveles que institucionales respetan
# =============================================================================

def sig_fibonacci_retracement_long(df, p):
    """Long: pullback al nivel Fibonacci 61.8% en tendencia alcista -> rebote."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb  = int(p.get("swing_lb", 50))
    fib = p.get("fib_level", 0.618)
    swing_high = df["high"].rolling(lb).max()
    swing_low  = df["low"].rolling(lb).min()
    fib_support = swing_high - (swing_high - swing_low) * fib
    at_fib  = (df["low"] <= fib_support * 1.005) & (df["low"] >= fib_support * 0.995)
    bounce  = df["close"] > fib_support
    uptrend = df["close"] > df["ema200"]
    bs = at_fib.shift(1).fillna(False) & bounce & uptrend & \
         (df["rsi14"] < p.get("rsi_max", 55)) & (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("fibonacci_retracement_long")

def sig_fibonacci_retracement_short(df, p):
    """Short: rebote al 61.8% en tendencia bajista -> rechazo y continuacion."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb  = int(p.get("swing_lb", 50))
    fib = p.get("fib_level", 0.618)
    swing_high = df["high"].rolling(lb).max()
    swing_low  = df["low"].rolling(lb).min()
    fib_resist = swing_low + (swing_high - swing_low) * fib
    at_fib    = (df["high"] >= fib_resist * 0.995) & (df["high"] <= fib_resist * 1.005)
    rejection = df["close"] < fib_resist
    downtrend = df["close"] < df["ema200"]
    bs = at_fib.shift(1).fillna(False) & rejection & downtrend & \
         (df["rsi14"] > p.get("rsi_min", 45)) & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("fibonacci_retracement_short")

# =============================================================================
#  GRUPO 16: SUPPLY SHOCK — reaccion a reportes de inventarios (EIA, WASDE, etc.)
# =============================================================================

def sig_supply_shock_long(df, p):
    """Long: vela bajista gigante con volumen extremo -> selling exhaustion."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    bar_range  = df["high"] - df["low"]
    big_bar    = bar_range > df["atr"] * p.get("range_mult", 2.5)
    shock_bar  = (df["close"].shift(1) < df["open"].shift(1)) & big_bar.shift(1)
    vol_spike  = df["volume"].shift(1) > df["vol_ma"].shift(1) * p.get("vol_mult", 2.5)
    shock_mid  = (df["high"].shift(1) + df["low"].shift(1)) / 2
    recovery   = df["close"] > shock_mid
    bs = shock_bar & vol_spike & recovery & (df["rsi_w"] > p.get("rsi_w_thr", 38))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("supply_shock_long")

def sig_supply_shock_short(df, p):
    """Short: vela alcista gigante con volumen extremo -> buying exhaustion."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    bar_range  = df["high"] - df["low"]
    big_bar    = bar_range > df["atr"] * p.get("range_mult", 2.5)
    shock_bar  = (df["close"].shift(1) > df["open"].shift(1)) & big_bar.shift(1)
    vol_spike  = df["volume"].shift(1) > df["vol_ma"].shift(1) * p.get("vol_mult", 2.5)
    shock_mid  = (df["high"].shift(1) + df["low"].shift(1)) / 2
    failure    = df["close"] < shock_mid
    bs = shock_bar & vol_spike & failure & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("supply_shock_short")

# =============================================================================
#  GRUPO 17: NG SEASONAL GRANULAR — gas natural tiene ventanas muy especificas
# =============================================================================

def sig_ng_seasonal_long(df, p):
    """Long NG: temporada de calefaccion Oct-Feb + momentum positivo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    month = _season_month(df)
    s = int(p.get("bull_month_start", 10)); e = int(p.get("bull_month_end", 2))
    in_season = (month >= s) | (month <= e) if s > e else (month >= s) & (month <= e)
    trend_ok  = df["close"] > df["ema50"]
    rsi_ok    = (df["rsi14"] > p.get("rsi_lo", 35)) & (df["rsi14"] > df["rsi14"].shift(2))
    bs = in_season & trend_ok & rsi_ok & (df["rsi_w"] > p.get("rsi_w_thr", 42))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("ng_seasonal_long")

def sig_ng_seasonal_short(df, p):
    """Short NG: temporada de inyeccion Abr-Sep + momentum negativo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    month = _season_month(df)
    s = int(p.get("bear_month_start", 4)); e = int(p.get("bear_month_end", 9))
    in_season = (month >= s) & (month <= e)
    trend_dn  = (df["close"] < df["ema50"]) | (df["ema50"] < df["ema50"].shift(5))
    rsi_ok    = (df["rsi14"] < p.get("rsi_hi", 65)) & (df["rsi14"] < df["rsi14"].shift(2))
    bs = in_season & trend_dn & rsi_ok & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("ng_seasonal_short")

# =============================================================================
#  GRUPO 18: COT PROXY — posicionamiento institucional sin datos CFTC
# =============================================================================

def sig_cot_proxy_long(df, p):
    """Long: volumen creciente en zona de minimos = comerciales acumulando (proxy COT)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("oi_period", 20))
    vol_trend  = df["volume"].rolling(lb).mean()
    oi_rising  = vol_trend > vol_trend.shift(5)
    near_low   = df["close"] < df["low"].rolling(lb).min().shift(1) * p.get("low_tol", 1.03)
    rsi_ok     = df["rsi14"] < p.get("rsi_lo", 40)
    recovering = (df["close"] > df["close"].shift(1)) & (df["close"] > df["close"].shift(2))
    bs = oi_rising & near_low & rsi_ok & recovering & (df["rsi_w"] > p.get("rsi_w_thr", 38))
    return _gl(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("cot_proxy_long")

def sig_cot_proxy_short(df, p):
    """Short: volumen declinante en zona de maximos = institucionales distribuyendo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("oi_period", 20))
    vol_trend   = df["volume"].rolling(lb).mean()
    oi_declining = vol_trend < vol_trend.shift(5)
    near_high   = df["close"] > df["high"].rolling(lb).max().shift(1) * p.get("high_tol", 0.97)
    rsi_ok      = df["rsi14"] > p.get("rsi_hi", 60)
    declining   = (df["close"] < df["close"].shift(1)) & (df["close"] < df["close"].shift(2))
    bs = oi_declining & near_high & rsi_ok & declining & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gs(df, bs, slm, tpm, cd)

COMMODITY_STRATEGIES.append("cot_proxy_short")

# =============================================================================
#  ROUTING POR FAMILIA DE ACTIVO
#  Con mas CPU: solo aumentar MAX_PARALLEL en pipeline.py. No tocar aqui.
# =============================================================================

ASSET_FAMILY = {
    'XAU': 'precious_metals',
    'XAG': 'precious_metals',
    'PL':  'precious_metals',
    'WTI': 'energy',
    'NG':  'energy',
    'HG':  'industrial',
}

# Estrategias universales — aplican a todos los commodities
_UNIVERSAL = [
    'turtle_breakout_long',        'turtle_breakout_short',
    'zscore_revert_long',          'zscore_revert_short',
    'macro_momentum_long',         'macro_momentum_short',
    'keltner_trend_long',          'keltner_trend_short',
    'atr_compression_long',        'atr_compression_short',
    'bollinger_squeeze_long',      'bollinger_squeeze_short',
    'vwap_deviation_long',         'vwap_deviation_short',
    'fibonacci_retracement_long',  'fibonacci_retracement_short',
    'volume_climax_long',          'volume_climax_short',
    'higher_low_structure_long',   'lower_high_structure_short',
]

# Extra por familia
_PM_EXTRA = [
    'safe_haven_long',        'risk_on_short',
    'dxy_weakness_long',      'dxy_strength_short',
    'ny_session_breakout_long', 'ny_session_breakout_short',
    'london_open_long',       'london_open_short',
]

_ENERGY_EXTRA = [
    'energy_seasonal_long',   'energy_seasonal_short',
    'ng_seasonal_long',       'ng_seasonal_short',
    'supply_shock_long',      'supply_shock_short',
    'ny_session_breakout_long', 'ny_session_breakout_short',
]

_INDUSTRIAL_EXTRA = [
    'copper_cycle_long',      'copper_cycle_short',
    'cot_proxy_long',         'cot_proxy_short',
    'supply_shock_long',      'supply_shock_short',
    'ny_session_breakout_long', 'ny_session_breakout_short',
]

STRATEGIES_BY_FAMILY = {
    'precious_metals': list(dict.fromkeys(_UNIVERSAL + _PM_EXTRA)),
    'energy':          list(dict.fromkeys(_UNIVERSAL + _ENERGY_EXTRA)),
    'industrial':      list(dict.fromkeys(_UNIVERSAL + _INDUSTRIAL_EXTRA)),
}


def get_strategies_for(asset_code):
    """Retorna lista de estrategias optimizada para el activo.
    XAU/XAG/PL -> 28 strats  |  WTI/NG -> 28  |  HG -> 28
    Desconocido -> todas las 38.
    """
    family = ASSET_FAMILY.get(asset_code.upper())
    if family:
        return STRATEGIES_BY_FAMILY[family]
    return COMMODITY_STRATEGIES


_n_l = sum(1 for s in COMMODITY_STRATEGIES if not s.endswith('_short'))
_n_s = sum(1 for s in COMMODITY_STRATEGIES if s.endswith('_short'))
print(f"[commodity_strategies] v2 | {len(COMMODITY_STRATEGIES)} estrategias: {_n_l}L / {_n_s}S | routing: {list(ASSET_FAMILY.keys())}")
