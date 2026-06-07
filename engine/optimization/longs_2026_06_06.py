"""
longs_2026_06_06.py - 32 nuevas estrategias LONG para alcanzar paridad 63L = 63S.
Mirrors naturales de los 32 shorts de shorts_2026_05_15.py + estrategias propias.
Patron identico al de shorts_2026_05_15: reciben df (post add_features) y params dict.
"""
import pandas as pd
import numpy as np

apply_regime_gate = None
_apply_cd = None

def _bind(ap_mod):
    global apply_regime_gate, _apply_cd
    apply_regime_gate = ap_mod.apply_regime_gate
    _apply_cd         = ap_mod._apply_cd

def _gate_long(df, bs, slm, tpm, cd):
    bl = pd.Series(False, index=df.index)
    bl, bs = apply_regime_gate(df, bl, bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)

NEW_LONGS_2026_06_06 = [
    'supertrend_long', 'atr_channel_long', 'chandelier_long',
    'keltner_breakout_long', 'donchian_breakout', 'bb_squeeze_long',
    'ema_ribbon_bull', 'ema_triple_bull', 'wma_momentum_long',
    'hull_ma_cross', 'linear_reg_breakup', 'roc_positive_long',
    'rsi_trend_long', 'dmi_bull_long', 'macd_divergence_bull',
    'bullish_rsi_divergence', 'stoch_cross_long', 'adx_trend_long',
    'three_candles_long', 'inside_bar_long', 'wedge_breakout_long',
    'consecutive_wick_bottom', 'open_close_cross_long', 'heikin_ashi_bull',
    'volume_climax_bottom', 'vwap_reclaim_long', 'zscore_cheap_long',
    'bb_bandwidth_long', 'range_compression_long', 'trend_exhaustion_rev',
    'pivot_level_support', 'momentum_burst_long',
]

# ============================================================================
# GRUPO A: Volatilidad / Canal (6)
# ============================================================================

def sig_supertrend_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("st_period", 10)); mult = p.get("st_mult", 3.0)
    c = df["close"]; h = df["high"]; l = df["low"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_st = tr.rolling(period).mean()
    mid = (h + l) / 2
    basic_up = mid - mult * atr_st
    basic_dn = mid + mult * atr_st
    sup = basic_up.copy(); res = basic_dn.copy()
    for i in range(1, len(sup)):
        sup.iloc[i] = max(basic_up.iloc[i], sup.iloc[i-1]) if c.iloc[i-1] > sup.iloc[i-1] else basic_up.iloc[i]
        res.iloc[i] = min(basic_dn.iloc[i], res.iloc[i-1]) if c.iloc[i-1] < res.iloc[i-1] else basic_dn.iloc[i]
    trend = pd.Series(1, index=df.index)
    for i in range(1, len(trend)):
        if trend.iloc[i-1] == -1 and c.iloc[i] > res.iloc[i]:
            trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and c.iloc[i] < sup.iloc[i]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]
    bs = (trend == 1) & (trend.shift(1) == -1) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_atr_channel_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("atr_period", 20)); mult = p.get("atr_mult", 1.5)
    sma = df["close"].rolling(period).mean()
    upper = sma + mult * df["atr"]
    bs = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_chandelier_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("ch_period", 22)); mult = p.get("ch_mult", 3.0)
    highest_high = df["high"].rolling(period).max()
    chandelier = highest_high - mult * df["atr"]
    bs = (df["close"] > chandelier) & (df["close"].shift(1) <= chandelier.shift(1)) & \
         (df["macd_h"] > 0) & (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_keltner_breakout_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("kc_period", 20)); mult = p.get("kc_mult", 2.0)
    ema_kc = df["close"].ewm(span=period, adjust=False).mean()
    upper_kc = ema_kc + mult * df["atr"]
    bs = (df["close"] > upper_kc) & (df["close"].shift(2) < upper_kc.shift(2)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.3)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_donchian_breakout(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("dc_period", 20))
    highest = df["high"].rolling(period).max().shift(1)
    bs = (df["close"] > highest) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48)) & (df["macd_h"] > 0)
    return _gate_long(df, bs, slm, tpm, cd)

def sig_bb_squeeze_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bb_period", 20))
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2*std; lower = sma - 2*std
    bandwidth = (upper - lower) / (sma + 1e-9)
    squeezed = bandwidth < bandwidth.rolling(50).quantile(0.25)
    expanding = bandwidth > bandwidth.shift(1)
    bs = squeezed.shift(2).fillna(False) & expanding & \
         (df["close"] > sma) & (df["macd_h"] > 0) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# GRUPO B: Momentum EMA (6)
# ============================================================================

def sig_ema_ribbon_bull(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    e8  = c.ewm(span=8,  adjust=False).mean()
    e13 = c.ewm(span=13, adjust=False).mean()
    e21 = c.ewm(span=21, adjust=False).mean()
    e34 = c.ewm(span=34, adjust=False).mean()
    aligned = (e8 > e13) & (e13 > e21) & (e21 > e34)
    bs = aligned & ~aligned.shift(3).fillna(False) & \
         (c > df["ema200"]) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_ema_triple_bull(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    cross_21  = (c > df["ema21"]) & (c.shift(1) <= df["ema21"].shift(1))
    above_all = (df["ema21"] > df["ema50"]) & (df["ema50"] > df["ema200"])
    bs = cross_21 & above_all & (df["rsi_w"] > p.get("rsi_w_thr", 50)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_wma_momentum_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("wma_period", 20))
    weights = np.arange(1, period + 1)
    wma = df["close"].rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    wma_slope = wma - wma.shift(3)
    bs = (wma_slope > 0) & (wma_slope.shift(3) <= 0) & \
         (df["close"] > wma) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_hull_ma_cross(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    n = int(p.get("hull_period", 16))
    c = df["close"]
    wma_n = c.rolling(n).apply(lambda x: np.dot(x, np.arange(1,n+1))/np.arange(1,n+1).sum(), raw=True)
    wma_h = c.rolling(max(n//2,2)).apply(lambda x: np.dot(x, np.arange(1,len(x)+1))/np.arange(1,len(x)+1).sum(), raw=True)
    raw_hma = 2*wma_h - wma_n
    sq_n = max(int(np.sqrt(n)), 2)
    hma = raw_hma.rolling(sq_n).apply(lambda x: np.dot(x, np.arange(1,sq_n+1))/np.arange(1,sq_n+1).sum(), raw=True)
    bs = (hma > hma.shift(1)) & (hma.shift(1) <= hma.shift(2)) & \
         (df["close"] > df["ema200"]) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_linear_reg_breakup(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("lr_period", 30)); dev_mult = p.get("lr_dev", 1.5)
    c = df["close"]
    def lr_upper(x):
        y = np.array(x); xi = np.arange(len(y))
        m, b = np.polyfit(xi, y, 1)
        pred = m * xi + b
        return pred[-1] + dev_mult * np.std(y - pred)
    upper_lr = c.rolling(period).apply(lr_upper, raw=True)
    bs = (c > upper_lr) & (c.shift(1) <= upper_lr.shift(1)) & \
         (df["macd_h"] > 0) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_roc_positive_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("roc_period", 10))
    c = df["close"]
    roc = (c - c.shift(period)) / (c.shift(period) + 1e-9) * 100
    bs = (roc > p.get("roc_thr", 1.0)) & (roc > roc.shift(2)) & \
         (df["close"] > df["ema200"]) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# GRUPO C: Osciladores (6)
# ============================================================================

def sig_rsi_trend_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    rsi = df["rsi14"]
    rsi_entry = (rsi > p.get("rsi_lo", 52)) & (rsi < p.get("rsi_hi", 72)) & \
                ~((rsi.shift(1) > p.get("rsi_lo", 52)) & (rsi.shift(1) < p.get("rsi_hi", 72)))
    bs = rsi_entry & (df["close"] > df["ema50"]) & \
         (df["macd_h"] > 0) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_dmi_bull_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("dmi_period", 14)); adx_thr = p.get("adx_thr", 20.0)
    h = df["high"]; l = df["low"]; c = df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(period).mean()
    up_move = h - h.shift(1); dn_move = l.shift(1) - l
    pdm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    ndm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    pdi = 100 * pdm.rolling(period).mean() / (atr14 + 1e-9)
    ndi = 100 * ndm.rolling(period).mean() / (atr14 + 1e-9)
    dx  = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    adx = dx.rolling(period).mean()
    cross = (pdi > ndi) & (pdi.shift(1) <= ndi.shift(1))
    bs = cross & (adx > adx_thr) & (adx > adx.shift(1)) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_macd_divergence_bull(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    window = int(p.get("div_window", 20))
    c = df["close"]; macd = df["macd"]
    price_ll = c < c.rolling(window).min().shift(1)
    macd_hl  = macd > macd.rolling(window).min().shift(1)
    bs = price_ll & macd_hl & (df["rsi14"] < p.get("rsi_lo", 45)) & (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_bullish_rsi_divergence(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("lookback", 14))
    c = df["close"]; rsi = df["rsi14"]
    price_ll = (c < c.shift(lb)) & (c.shift(lb) < c.shift(lb*2))
    rsi_hl   = (rsi > rsi.shift(lb)) & (rsi.shift(lb) < 40)
    bs = price_ll & rsi_hl & (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_stoch_cross_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    k_period = int(p.get("stoch_k", 14)); d_period = int(p.get("stoch_d", 3))
    h = df["high"]; l = df["low"]; c = df["close"]
    k = 100 * (c - l.rolling(k_period).min()) / (h.rolling(k_period).max() - l.rolling(k_period).min() + 1e-9)
    d = k.rolling(d_period).mean()
    bs = (k > d) & (k.shift(1) <= d.shift(1)) & (k.shift(1) < p.get("stoch_lo", 25)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_adx_trend_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("adx_period", 14)); adx_thr = p.get("adx_thr", 25.0)
    h = df["high"]; l = df["low"]; c = df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(period).mean()
    up_move = h - h.shift(1); dn_move = l.shift(1) - l
    pdm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    ndm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    pdi = 100 * pdm.rolling(period).mean() / (atr14 + 1e-9)
    ndi = 100 * ndm.rolling(period).mean() / (atr14 + 1e-9)
    adx = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)).rolling(period).mean()
    trending = (adx > adx_thr) & (pdi > ndi) & (adx > adx.shift(2))
    bs = trending & ~trending.shift(2).fillna(False) & \
         (c > df["ema21"]) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# GRUPO D: Price action (6)
# ============================================================================

def sig_three_candles_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]; o = df["open"]; v = df["volume"]
    bull1 = c.shift(2) > o.shift(2)
    bull2 = c.shift(1) > o.shift(1)
    bull3 = c > o
    vol_inc = (v > v.shift(1)) & (v.shift(1) > v.shift(2))
    bs = bull1 & bull2 & bull3 & vol_inc & \
         (df["close"] > df["ema21"]) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_inside_bar_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    inside = (df["high"].shift(1) >= df["high"]) & (df["low"].shift(1) <= df["low"])
    breakout_up = df["close"] > df["high"].shift(1)
    bs = inside.shift(1).fillna(False) & breakout_up & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_wedge_breakout_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("wedge_lb", 10))
    h = df["high"]; l = df["low"]; c = df["close"]
    hh_slope = h.diff(lb); ll_slope = l.diff(lb)
    wedge_dn = (hh_slope < 0) & (ll_slope < 0) & (ll_slope > hh_slope)
    breakout = c > h.rolling(lb).max().shift(1)
    bs = wedge_dn.shift(1).fillna(False) & breakout & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.3)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_consecutive_wick_bottom(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    n = int(p.get("wick_n", 3))
    o = df["open"]; c = df["close"]; l = df["low"]
    body_lo = pd.concat([o, c], axis=1).min(axis=1)
    wick_ratio = (body_lo - l) / (df["high"] - l + 1e-9)
    has_wick = wick_ratio > p.get("wick_ratio", 0.4)
    consec = has_wick.rolling(n).sum() >= n
    bs = consec & ~consec.shift(n).fillna(False) & \
         (df["close"] > df["close"].shift(n)) & (df["rsi_w"] > p.get("rsi_w_thr", 42))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_open_close_cross_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    n = int(p.get("consec_n", 4))
    bull = df["close"] > df["open"]
    all_bull = bull.rolling(n).sum() == n
    entry = all_bull & ~all_bull.shift(1).fillna(False)
    bs = entry & (df["close"] > df["ema21"]) & (df["rsi_w"] > p.get("rsi_w_thr", 50))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_heikin_ashi_bull(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    o = df["open"]; c = df["close"]; h = df["high"]; l = df["low"]
    ha_close = (o + h + l + c) / 4
    ha_open  = ((o + c) / 2).ewm(alpha=0.5, adjust=False).mean()
    ha_green = ha_close > ha_open
    ha_no_lower = (pd.concat([ha_open, ha_close], axis=1).min(axis=1) - l) < df["atr"] * p.get("wick_thr", 0.2)
    bull_seq = ha_green & ha_no_lower
    n = int(p.get("seq_n", 3))
    bs = (bull_seq.rolling(n).sum() == n) & \
         ~(bull_seq.shift(n).rolling(n).sum() == n).fillna(False) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# GRUPO E: Volumen / Precio relativo (4)
# ============================================================================

def sig_volume_climax_bottom(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    vol_spike = df["volume"] > df["vol_ma"] * p.get("vol_mult", 3.0)
    c = df["close"]; o = df["open"]; h = df["high"]; l = df["low"]
    reversal = ((c - l) / (h - l + 1e-9)) > p.get("body_pct", 0.65)
    bearish_bar = c.shift(1) < o.shift(1)
    bs = vol_spike & reversal & bearish_bar & (df["rsi14"] < p.get("rsi_lo", 35)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_vwap_reclaim_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]; v = df["volume"]
    window = int(p.get("vwap_window", 20))
    typical = (df["high"] + df["low"] + c) / 3
    vwap = (typical * v).rolling(window).sum() / (v.rolling(window).sum() + 1e-9)
    bs = (c > vwap) & (c.shift(1) <= vwap.shift(1)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.2)) & \
         (df["macd_h"] > 0) & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_zscore_cheap_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("zscore_period", 50))
    c = df["close"]
    sma = c.rolling(period).mean(); std = c.rolling(period).std()
    zscore = (c - sma) / (std + 1e-9)
    bs = (zscore < p.get("zscore_lo", -1.5)) & (zscore > zscore.shift(1)) & \
         (zscore.shift(1) < p.get("zscore_lo", -1.5)) & (df["rsi_w"] > p.get("rsi_w_thr", 38))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_bb_bandwidth_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bb_period", 20))
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2*std
    bw = (4*std) / (sma + 1e-9)
    squeezed = bw < bw.rolling(50).quantile(p.get("squeeze_q", 0.20))
    break_up = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1))
    bs = squeezed.shift(2).fillna(False) & break_up & (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# GRUPO F: Compresion / Agotamiento (4)
# ============================================================================

def sig_range_compression_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("compression_lb", 30))
    atr_q = df["atr"].rolling(lb).quantile(p.get("atr_q", 0.20))
    compressed = df["atr"] < atr_q
    expansion = df["atr"] > df["atr"].shift(1) * p.get("atr_expand", 1.3)
    bs = compressed.shift(2).fillna(False) & expansion & \
         (df["close"] > df["open"]) & (df["close"] > df["ema50"]) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_trend_exhaustion_rev(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("exhaust_lb", 10))
    c = df["close"]; rsi = df["rsi14"]
    price_falling  = c < c.shift(lb)
    vol_falling    = df["volume"].rolling(lb).mean() < df["volume"].rolling(lb*2).mean()
    atr_falling    = df["atr"] < df["atr"].shift(lb)
    rsi_recovering = rsi > rsi.shift(lb)
    green_bar      = c > df["open"]
    bs = price_falling & vol_falling & atr_falling & rsi_recovering & green_bar & \
         (rsi < p.get("rsi_lo", 48)) & (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_pivot_level_support(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("pivot_lb", 20)); tol = p.get("pivot_tol", 1.005)
    pivot_lo = df["low"].rolling(lb).min().shift(1)
    near_pivot = (df["low"] <= pivot_lo * tol) & (df["low"] >= pivot_lo * (2 - tol))
    bs = near_pivot & (df["close"] > df["open"]) & (df["rsi14"] < p.get("rsi_lo", 42)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_momentum_burst_long(df, p):
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("burst_lb", 5))
    c = df["close"]
    sma_fast = c.rolling(lb).mean()
    momentum = (c - c.shift(lb)) / (c.shift(lb) + 1e-9) * 100
    bs = (c > sma_fast) & (momentum > p.get("momentum_thr", 1.5)) & \
         (momentum.shift(lb) < p.get("momentum_thr", 1.5)) & \
         (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.4)) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 48))
    return _gate_long(df, bs, slm, tpm, cd)
