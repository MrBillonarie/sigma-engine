"""
xau_strategies.py - 8 estrategias optimizadas para XAU/USD (Oro).
El oro tiene dinamica distinta a crypto: tendencias largas, pocos pero grandes movimientos,
respeta niveles psicologicos, reacciona a volatilidad macro.
Aplicables a todos los activos pero calibradas para el comportamiento del oro.
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

def _gate_short(df, bs, slm, tpm, cd):
    bl = pd.Series(False, index=df.index)
    bl, bs = apply_regime_gate(df, bl, bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)

XAU_STRATEGIES = [
    'safe_haven_surge',        # long: spike de volumen + barra alcista fuerte (demanda refugio)
    'gold_compression_break',  # long: compresion ATR larga -> expansion alcista
    'gold_pullback_trend',     # long: pullback en tendencia alcista madura (dip buy)
    'gold_reversal_hammer',    # long: hammer con volumen en soporte (reversiones del oro)
    'gold_macro_momentum',     # long: HTF bull + nuevos maximos con volumen (macro trend)
    'gold_rsi_recovery',       # long: RSI recupera desde oversold en tendencia bull
    'gold_mean_rev_short',     # short: extension extrema en mercado lateral (gold range trade)
    'gold_breakdown_short',    # short: ruptura de soporte con volumen en tendencia bajista
]

# ============================================================================
# LONG XAU (6 estrategias)
# ============================================================================

def sig_safe_haven_surge(df, p):
    """Long: spike de volumen + vela alcista fuerte = demanda refugio institucional."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]; o = df["open"]; v = df["volume"]
    # Vela grande alcista: cuerpo > X% del ATR
    body = (c - o).abs()
    big_bull = (c > o) & (body > df["atr"] * p.get("body_atr", 0.8))
    # Volumen institucional: muy por encima del promedio
    vol_surge = v > df["vol_ma"] * p.get("vol_surge", 2.5)
    # HTF confirma direction (el oro sube cuando hay miedo a nivel macro)
    htf_ok = df["htf_bull"] | df["htf_range"]
    bs = big_bull & vol_surge & htf_ok & (df["rsi_w"] > p.get("rsi_w_thr", 42))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_gold_compression_break(df, p):
    """Long: el oro comprime durante semanas -> explota hacia arriba (patron tipico del oro)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    # Compresion prolongada: ATR bajo por muchas barras
    lb_compress = int(p.get("compress_bars", 40))
    lb_expand   = int(p.get("expand_bars", 3))
    atr_q = df["atr"].rolling(lb_compress).quantile(p.get("atr_q", 0.15))
    long_compress = df["atr"].rolling(lb_compress // 2).mean() < atr_q
    # Expansion: ATR se dispara + cierre sobre maximo reciente
    expanding   = df["atr"] > df["atr"].rolling(lb_expand).mean().shift(1) * p.get("expand_mult", 1.5)
    new_high    = df["close"] > df["high"].rolling(lb_compress).max().shift(1)
    bs = long_compress.shift(lb_expand).fillna(False) & expanding & new_high & \
         (df["close"] > df["ema50"]) & (df["rsi_w"] > p.get("rsi_w_thr", 45))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_gold_pullback_trend(df, p):
    """Long: pullback en tendencia alcista del oro — el oro siempre rebota en su EMA21."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    c = df["close"]
    # Tendencia alcista madura: precio sobre EMA200, EMA21 subiendo
    uptrend = (c > df["ema200"]) & (df["ema21"] > df["ema21"].shift(5)) & \
              (df["ema50"] > df["ema50"].shift(5))
    # Pullback: precio toca EMA21 desde arriba
    touched_ema21 = (df["low"] <= df["ema21"] * p.get("ema_tol", 1.003)) & \
                    (df["low"] >= df["ema21"] * (2 - p.get("ema_tol", 1.003)))
    # Vela de reversal despues del toque
    reversal = (c > c.shift(1)) & (df["rsi14"] < p.get("rsi_lo", 50))
    bs = uptrend & touched_ema21.shift(1).fillna(False) & reversal & \
         (df["rsi_w"] > p.get("rsi_w_thr", 52))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_gold_reversal_hammer(df, p):
    """Long: hammer con volumen en zona de soporte — el oro respeta niveles clave."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    o = df["open"]; c = df["close"]; h = df["high"]; l = df["low"]
    # Hammer: mecha inferior larga, cuerpo pequeno arriba
    body_size  = (c - o).abs()
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    is_hammer  = (lower_wick > body_size * p.get("wick_body", 2.0)) & \
                 (upper_wick < body_size * 0.5) & (lower_wick > df["atr"] * 0.5)
    # En zona de soporte (cerca de minimo de N dias)
    near_support = df["low"] < df["low"].rolling(int(p.get("support_lb", 20))).min().shift(1) * 1.01
    # Volumen por encima del promedio
    vol_ok = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.3)
    bs = is_hammer & (near_support | (df["rsi14"] < p.get("rsi_lo", 38))) & vol_ok & \
         (df["rsi_w"] > p.get("rsi_w_thr", 40))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_gold_macro_momentum(df, p):
    """Long: HTF bullish + precio rompiendo nuevos maximos con volumen (trend macro del oro)."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("high_lb", 50))
    c = df["close"]
    new_high   = c > df["high"].rolling(lb).max().shift(1)
    htf_bull   = df["htf_bull"]
    # Momentum: macd positivo y subiendo
    macd_bull  = (df["macd"] > 0) & (df["macd"] > df["macd_s"]) & (df["macd_h"] > df["macd_h"].shift(1))
    vol_confirm = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.4)
    bs = new_high & htf_bull & macd_bull & vol_confirm & \
         (df["rsi_w"] > p.get("rsi_w_thr", 55))
    return _gate_long(df, bs, slm, tpm, cd)

def sig_gold_rsi_recovery(df, p):
    """Long: RSI recupera desde oversold en tendencia bull — rebote tecnico del oro."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    rsi = df["rsi14"]
    # RSI baja a zona oversold luego cruza hacia arriba
    was_oversold = rsi.shift(3).rolling(5).min() < p.get("rsi_oversold", 38)
    recovering   = (rsi > p.get("rsi_recover", 42)) & (rsi > rsi.shift(1)) & (rsi > rsi.shift(2))
    # Precio sobre EMA200 (solo en tendencia bull)
    bs = was_oversold & recovering & (df["close"] > df["ema200"]) & \
         (df["rsi_w"] > p.get("rsi_w_thr", 50)) & (df["htf_bull"] | df["htf_range"])
    return _gate_long(df, bs, slm, tpm, cd)

# ============================================================================
# SHORT XAU (2 estrategias)
# ============================================================================

def sig_gold_mean_rev_short(df, p):
    """Short: extension extrema en mercado lateral — el oro revierte en ranging markets."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    period = int(p.get("mr_period", 30))
    c = df["close"]
    sma = c.rolling(period).mean(); std = c.rolling(period).std()
    zscore = (c - sma) / (std + 1e-9)
    # Sobreextendido hacia arriba + mercado lateral + RSI sobrecomprado
    overextended = zscore > p.get("zscore_hi", 1.8)
    ranging = df["regime_range"]
    bs = overextended & ranging & (df["rsi14"] > p.get("rsi_hi", 68)) & \
         (df["macd_h"] < 0) & (df["rsi_w"] < p.get("rsi_w_thr", 55))
    return _gate_short(df, bs, slm, tpm, cd)

def sig_gold_breakdown_short(df, p):
    """Short: ruptura de soporte con volumen en tendencia bajista del oro."""
    slm = p["sl_mult"]; tpm = p["tp_mult"]; cd = p["cooldown"]
    lb = int(p.get("support_lb", 20))
    support = df["low"].rolling(lb).min().shift(1)
    # Breakout hacia abajo del soporte
    breakdown = (df["close"] < support) & (df["close"].shift(1) >= support.shift(1))
    vol_confirm = df["volume"] > df["vol_ma"] * p.get("vol_mult", 1.5)
    htf_bear = df["htf_bear"] | (df["rsi_w"] < p.get("rsi_w_thr", 45))
    bs = breakdown & vol_confirm & htf_bear & \
         (df["macd_h"] < 0) & (df["close"] < df["ema200"])
    return _gate_short(df, bs, slm, tpm, cd)
