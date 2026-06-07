"""
shorts_2026_05_15.py - 32 nuevas funciones sig_*_short para alcanzar paridad
58 LONG = 58 SHORT (regla de Satoshi Nakamoto: librería balanceada).

Todas usan el mismo patrón de las sig_* originales:
  - reciben df (ya con add_features aplicado) y params dict
  - devuelven (sig_series, sl_series, tp_series) via _apply_cd
  - bl = pd.Series(False, index=df.index)  (solo SHORT)
  - bl, bs = apply_regime_gate(df, bl, bs)   (filtro de régimen)

Las nuevas estrategias son inversiones naturales de sus contrapartes long.
"""
import pandas as pd
import numpy as np

# Will be injected by asset_pipeline import-side
apply_regime_gate = None
_apply_cd = None


def _bind(ap_mod):
    """Receives the parent module to bind regime_gate + apply_cd helpers."""
    global apply_regime_gate, _apply_cd
    apply_regime_gate = ap_mod.apply_regime_gate
    _apply_cd = ap_mod._apply_cd


def _gate_short(df, bs, slm, tpm, cd):
    """Helper: applies regime gate (short-only) + cooldown."""
    bl = pd.Series(False, index=df.index)
    bl, bs = apply_regime_gate(df, bl, bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)


# ============================================================================
# 1-5: Reversiones técnicas básicas
# ============================================================================

def sig_aroon_cross_bear(df, p):
    """Short: Aroon Down cruza por encima de Aroon Up."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("aroon_period", 14))
    high_idx = df["high"].rolling(period + 1).apply(lambda x: period - x.argmax(), raw=True)
    low_idx  = df["low"].rolling(period + 1).apply(lambda x: period - x.argmin(), raw=True)
    aroon_up   = 100 * (period - high_idx) / period
    aroon_down = 100 * (period - low_idx) / period
    cross_dn = (aroon_down > aroon_up) & (aroon_down.shift(1) <= aroon_up.shift(1))
    bs = cross_dn & (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_bb_bandwidth_short(df, p):
    """Short: BB bandwidth expandiéndose + precio en upper band rechazado."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bb_period", 20))
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2 * std
    bandwidth = (4 * std) / (sma + 1e-9)
    bw_expanding = bandwidth > bandwidth.shift(1)
    touched_upper = df["high"] >= upper * 0.998
    bs = (touched_upper.shift(1).fillna(False) & (df["close"] < df["open"]) &
          bw_expanding & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_break_of_structure_down(df, p):
    """Short: precio rompe swing low de N barras + volumen + tendencia bajista."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bos_period", 20))
    swing_low = df["low"].rolling(period).min().shift(1)
    bs = ((df["close"] < swing_low) &
          (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.4)) &
          (df["rsi_w"] < p.get("rsi_w_thr", 52)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_chaikin_mf_short(df, p):
    """Short: Chaikin Money Flow negativo cruza desde positivo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("cmf_period", 20))
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"] + 1e-9)
    mfv = mfm * df["volume"]
    cmf = mfv.rolling(period).sum() / (df["volume"].rolling(period).sum() + 1e-9)
    cross_neg = (cmf < 0) & (cmf.shift(1) >= 0)
    bs = cross_neg & (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_consecutive_wick_top(df, p):
    """Short: 3 velas seguidas con upper wick > body * 1.5 (rechazo arriba)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    body = (df["close"] - df["open"]).abs()
    has_wick = upper_wick > body * 1.5
    triple_wick = has_wick & has_wick.shift(1).fillna(False) & has_wick.shift(2).fillna(False)
    bs = triple_wick & (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# 6-10: Elder impulse + EMAs
# ============================================================================

def sig_elder_impulse_bear(df, p):
    """Short: Elder Impulse rojo (EMA13 baja + MACD hist baja)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    ema13 = df["close"].ewm(span=13, adjust=False).mean()
    ema13_down = ema13 < ema13.shift(1)
    macdh_down = df["macd_h"] < df["macd_h"].shift(1)
    bs = (ema13_down & macdh_down & (df["close"] < df["ema200"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_ema_ribbon_bear(df, p):
    """Short: ribbon de EMAs cortas debajo de las largas + precio debajo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    ema8  = df["close"].ewm(span=8,  adjust=False).mean()
    ema13 = df["close"].ewm(span=13, adjust=False).mean()
    ema21 = df["ema21"]
    ema50 = df["ema50"]
    ribbon_bear = (ema8 < ema13) & (ema13 < ema21) & (ema21 < ema50)
    bs = ribbon_bear & (df["close"] < ema8) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_ema_triple_bear(df, p):
    """Short: 3 EMAs apiladas en orden bajista (rápida<media<lenta)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    e_fast = df["close"].ewm(span=int(p.get("ema_fast", 9)),  adjust=False).mean()
    e_med  = df["close"].ewm(span=int(p.get("ema_med", 21)), adjust=False).mean()
    e_slow = df["close"].ewm(span=int(p.get("ema_slow", 50)), adjust=False).mean()
    stacked = (e_fast < e_med) & (e_med < e_slow)
    bs = (stacked & (df["close"] < e_fast) &
          (df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.1)) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_htf_divergence_bear(df, p):
    """Short: divergencia bear precio vs rsi_w (precio sube, rsi_w cae)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("htf_lookback", 14))
    price_up = df["close"] > df["close"].shift(lb)
    rsiw_down = df["rsi_w"] < df["rsi_w"].shift(lb)
    bs = (price_up & rsiw_down & (df["rsi14"] > p.get("rsi_min", 60)) &
          (df["close"] < df["ema200"]))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_ichimoku_bear(df, p):
    """Short: cloud bear (tenkan<kijun + precio<cloud)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    tenkan_p = int(p.get("tenkan", 9))
    kijun_p  = int(p.get("kijun", 26))
    senkou_p = int(p.get("senkou", 52))
    h = df["high"]; l = df["low"]
    tenkan = (h.rolling(tenkan_p).max() + l.rolling(tenkan_p).min()) / 2
    kijun  = (h.rolling(kijun_p).max()  + l.rolling(kijun_p).min())  / 2
    span_a = ((tenkan + kijun) / 2).shift(kijun_p)
    span_b = ((h.rolling(senkou_p).max() + l.rolling(senkou_p).min()) / 2).shift(kijun_p)
    cross_bear = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))
    below_cloud = df["close"] < pd.concat([span_a, span_b], axis=1).min(axis=1)
    bs = cross_bear & below_cloud & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# 11-15: MACD + linear reg + lower lows + mean rev + MFI
# ============================================================================

def sig_linear_reg_break_down(df, p):
    """Short: pendiente de regresión lineal negativa + ruptura bajista."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("lr_period", 14))
    def _slope(y):
        n = len(y)
        x = np.arange(n)
        return np.polyfit(x, y, 1)[0] if n == period else 0
    slope = df["close"].rolling(period).apply(_slope, raw=True)
    bs = ((slope < 0) & (slope.shift(1) >= 0) &
          (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_lower_lows_short(df, p):
    """Short: 3+ mínimos descendentes consecutivos + breakdown."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    ll1 = df["low"] < df["low"].shift(1)
    ll2 = df["low"].shift(1) < df["low"].shift(2)
    ll3 = df["low"].shift(2) < df["low"].shift(3)
    bs = ll1 & ll2 & ll3 & (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_macd_divergence_bear(df, p):
    """Short: divergencia bajista — precio sube + macd_h baja."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("div_lookback", 10))
    price_up = df["close"] > df["close"].shift(lb)
    macd_down = df["macd_h"] < df["macd_h"].shift(lb)
    bs = (price_up & macd_down & (df["macd_h"] < 0) &
          (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 52)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_mean_rev_short(df, p):
    """Short: precio en sobrecompra (rsi14>70) + cierre cerca de upper BB."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bb_period", 20))
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2 * std
    bs = ((df["rsi14"] > p.get("rsi_ob", 70)) & (df["close"] > upper * 0.99) &
          (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 55)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_mfi_overbought_short(df, p):
    """Short: MFI cruza desde >=75 hacia abajo (institucional sale)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("mfi_period", 14))
    tp_price = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp_price * df["volume"]
    pos_mf = mf.where(tp_price > tp_price.shift(1), 0).rolling(period).sum()
    neg_mf = mf.where(tp_price < tp_price.shift(1), 0).rolling(period).sum()
    mfi = 100 - (100 / (1 + pos_mf / (neg_mf + 1e-9)))
    thr = p.get("mfi_thr", 75)
    cross_dn = (mfi < thr) & (mfi.shift(1) >= thr)
    bs = cross_dn & (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# 16-20: Micro momentum + OBV + open/close + pin bar + pivot
# ============================================================================

def sig_micro_momentum_short(df, p):
    """Short TF bajo: 3 cierres en rojo + macd_h en bajada."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    red1 = df["close"] < df["open"]
    red2 = df["close"].shift(1) < df["open"].shift(1)
    red3 = df["close"].shift(2) < df["open"].shift(2)
    macd_dn = df["macd_h"] < df["macd_h"].shift(1)
    bs = red1 & red2 & red3 & macd_dn & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_obv_divergence_bear(df, p):
    """Short: OBV divergence bear — precio sube, OBV cae."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    obv_step = (df["close"].diff() > 0).astype(int) - (df["close"].diff() < 0).astype(int)
    obv = (obv_step * df["volume"]).cumsum()
    lb = int(p.get("div_lookback", 10))
    price_up = df["close"] > df["close"].shift(lb)
    obv_down = obv < obv.shift(lb)
    bs = (price_up & obv_down & (df["close"] < df["ema200"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_open_close_cross_short(df, p):
    """Short: open cruza desde > close hacia <= close (cierre rojo desde gap up)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    prev_open_above = df["open"].shift(1) > df["close"].shift(1)
    now_red = df["close"] < df["open"]
    gap_down = df["open"] < df["close"].shift(1)
    bs = (prev_open_above & now_red & gap_down &
          (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_pin_bar_short(df, p):
    """Short: pin bar bajista (mecha superior >= 2x body, cuerpo pequeño)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    body = (df["close"] - df["open"]).abs()
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    range_full = df["high"] - df["low"] + 1e-9
    pin_bear = ((upper_wick >= body * 2) & (body / range_full < 0.35) &
                (upper_wick > lower_wick * 1.5))
    bs = (pin_bear & (df["close"] < df["ema50"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 55)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_pivot_rejection(df, p):
    """Short: precio testea pivot resistencia y rechaza."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("pivot_lookback", 5))
    pivot_high = df["high"].rolling(period).max()
    near_pivot = df["high"] >= pivot_high * 0.998
    rejected = (df["close"] < pivot_high * 0.995) & (df["close"] < df["open"])
    bs = (near_pivot.shift(1).fillna(False) & rejected &
          (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 52)))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# 21-25: PSAR + range scalp + session + squeeze + tema
# ============================================================================

def sig_psar_flip_down(df, p):
    """Short: precio cae bajo banda PSAR-like (ema21 + atr*mult)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    band = df["ema21"] + p.get("psar_mult", 2.0) * df["atr"]
    flip_dn = (df["close"] < band) & (df["close"].shift(1) >= band.shift(1))
    bs = (flip_dn & (df["close"] < df["ema200"]) &
          (df["volume"] > df["vol_ma"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_range_scalp_short(df, p):
    """Short: precio toca upper boundary del rango + rechaza."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("range_lookback", 20))
    range_top = df["high"].rolling(period).max()
    range_bot = df["low"].rolling(period).min()
    range_width = (range_top - range_bot) / range_bot
    in_range = range_width < p.get("max_range_pct", 0.05)
    near_top = df["close"] > (range_top - (range_top - range_bot) * 0.1)
    bs = (in_range & near_top & (df["close"] < df["open"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 55)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_session_open_short(df, p):
    """Short: primera barra de la sesión asia/europa con apertura bajista."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    hours = df.index.hour
    sess_hour = int(p.get("session_hour", 0))
    is_session_open = pd.Series(hours == sess_hour, index=df.index)
    gap_down = df["open"] < df["close"].shift(1)
    bs = (is_session_open & gap_down & (df["close"] < df["open"]) &
          (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_squeeze_pro_short(df, p):
    """Short: BB squeeze release con momentum bajista."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("bb_period", 20))
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    kc_atr = df["atr"]
    kc_upper = sma + 1.5 * kc_atr
    kc_lower = sma - 1.5 * kc_atr
    in_squeeze = (lower > kc_lower) & (upper < kc_upper)
    release = in_squeeze.shift(1).fillna(False) & ~in_squeeze
    bs = (release & (df["close"] < df["open"]) & (df["macd_h"] < 0) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_tema_cross_down(df, p):
    """Short: TEMA rápida cruza por debajo de TEMA lenta."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    def _tema(s, span):
        e1 = s.ewm(span=span, adjust=False).mean()
        e2 = e1.ewm(span=span, adjust=False).mean()
        e3 = e2.ewm(span=span, adjust=False).mean()
        return 3 * e1 - 3 * e2 + e3
    fast = _tema(df["close"], int(p.get("tema_fast", 9)))
    slow = _tema(df["close"], int(p.get("tema_slow", 21)))
    cross_dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    bs = cross_dn & (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# 26-32: Tick + TMA + trend + volatility + volume + vwap + wma
# ============================================================================

def sig_tick_follow_short(df, p):
    """Short TF bajo: 4 ticks seguidos en rojo + volumen creciente."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    red = df["close"] < df["open"]
    cnt = red.rolling(int(p.get("tick_count", 4))).sum()
    vol_up = df["volume"] > df["volume"].shift(1)
    bs = ((cnt == int(p.get("tick_count", 4))) & vol_up &
          (df["close"] < df["ema50"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_tma_bands_short(df, p):
    """Short: precio toca banda superior TMA + rechaza en downtrend."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("tma_period", 14))
    am = p.get("atr_mult", 1.5)
    sma_v = df["close"].rolling(period).mean()
    tma_v = sma_v.rolling(period).mean()
    upper = tma_v + df["atr"] * am
    touched = df["high"] >= upper * 0.999
    rejected = df["close"] < upper
    bs = (touched & rejected & (df["close"] < df["ema200"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 52)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_trend_strength_short(df, p):
    """Short: ADX fuerte (>25) + tendencia bajista (-DI > +DI)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("adx_period", 14))
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = up.where((up > dn) & (up > 0), 0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0)
    atr = df["atr"]
    plus_di  = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    bs = ((adx > p.get("adx_thr", 25)) & (minus_di > plus_di) &
          (df["close"] < df["ema200"]) & (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_volatility_breakdown(df, p):
    """Short: ATR expandiéndose + ruptura abajo con MACD bajista."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("vb_period", 14))
    atr_avg = df["atr"].rolling(period).mean()
    vol_expanding = df["atr"] > atr_avg * p.get("vol_expand", 1.3)
    range_low = df["low"].rolling(period).min().shift(1)
    breakdown = (df["close"] < range_low) & vol_expanding
    bs = (breakdown & (df["macd_h"] < 0) & (df["close"] < df["ema200"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_volume_exhaustion_top(df, p):
    """Short: vol declinando + vela bajista en zona alta (sin compras nuevas)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("ve_period", 5))
    vol_declining = df["volume"].rolling(period).mean() < df["volume"].rolling(period * 2).mean()
    bearish_candle = df["close"] < df["open"]
    bs = (vol_declining & bearish_candle & (df["close"] > df["ema50"]) &
          (df["rsi14"] > p.get("rsi_min", 55)) & (df["rsi_w"] < p.get("rsi_w_thr", 55)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_vwap_rejection(df, p):
    """Short: precio testea VWAP desde abajo + rechaza."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    tp_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (tp_price * df["volume"]).cumsum()
    cum_v = df["volume"].cumsum()
    vwap = cum_pv / (cum_v + 1e-9)
    near_vwap = (df["high"] >= vwap * 0.998) & (df["close"] < vwap)
    bs = (near_vwap & (df["close"] < df["open"]) & (df["close"] < df["ema200"]) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


def sig_wma_momentum_short(df, p):
    """Short: WMA bajando + cierre bajo WMA + macd_h negativo."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("wma_period", 14))
    weights = np.arange(1, period + 1)
    wma = df["close"].rolling(period).apply(lambda x: (x * weights).sum() / weights.sum(), raw=True)
    wma_down = wma < wma.shift(1)
    bs = (wma_down & (df["close"] < wma) & (df["macd_h"] < 0) &
          (df["rsi_w"] < p.get("rsi_w_thr", 50)))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# Registro: lista de nombres exportados (sin prefijo "sig_")
# ============================================================================
NEW_SHORTS_2026_05_15 = [
    "aroon_cross_bear",
    "bb_bandwidth_short",
    "break_of_structure_down",
    "chaikin_mf_short",
    "consecutive_wick_top",
    "elder_impulse_bear",
    "ema_ribbon_bear",
    "ema_triple_bear",
    "htf_divergence_bear",
    "ichimoku_bear",
    "linear_reg_break_down",
    "lower_lows_short",
    "macd_divergence_bear",
    "mean_rev_short",
    "mfi_overbought_short",
    "micro_momentum_short",
    "obv_divergence_bear",
    "open_close_cross_short",
    "pin_bar_short",
    "pivot_rejection",
    "psar_flip_down",
    "range_scalp_short",
    "session_open_short",
    "squeeze_pro_short",
    "tema_cross_down",
    "tick_follow_short",
    "tma_bands_short",
    "trend_strength_short",
    "volatility_breakdown",
    "volume_exhaustion_top",
    "vwap_rejection",
    "wma_momentum_short",
]
