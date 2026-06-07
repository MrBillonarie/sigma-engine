"""
alpha_2026_06_07.py - 12 estrategias alpha — 6 LONG + 6 SHORT
Fuentes de edge distintas a las existentes:
  - Liquidation sweeps (spikes cripto que revierten)
  - Session opens London/NY (institucionales entran)
  - Multi-TF momentum (3 horizontes alineados)
  - Funding rate contrarian (extremos = posicion sobreextendida)
  - Bollinger squeeze + expansion (volatilidad comprimida liberada)
  - Break of Structure (ICT: mercado muestra intención direccional)
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


ALPHA_LONGS = [
    'liquidation_reversal_long',
    'session_momentum_long',
    'multi_tf_bull',
    'funding_extreme_long',
    'squeeze_expand_long',
    'bos_long',
]
ALPHA_SHORTS = [
    'liquidation_reversal_short',
    'session_momentum_short',
    'multi_tf_bear',
    'funding_extreme_short',
    'squeeze_expand_short',
    'bos_short',
]
ALPHA_STRATEGIES = ALPHA_LONGS + ALPHA_SHORTS


# ============================================================================
# GRUPO 1: Liquidation Sweeps
# Los mercados cripto tienen sweeps de liquidacion de stops seguidos de
# reversiones bruscas — edge unico de los perpetual futures.
# ============================================================================

def sig_liquidation_reversal_long(df, p):
    """Spike bajista (sweep de stops longs) seguido de recuperacion rapida."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr = df['atr']
    spike_mult  = p.get('spike_mult', 1.8)
    confirm_pct = p.get('confirm_pct', 0.35)

    candle_range = h - l
    # Spike bajista: rango grande + cierre en la mitad inferior (bear trap)
    spike_down = (candle_range > atr * spike_mult) & (c < (h + l) / 2)
    # Confirmacion: siguiente vela sube al menos confirm_pct del spike previo
    next_confirm = (c - o) > candle_range.shift(1) * confirm_pct
    bs = spike_down.shift(1) & next_confirm & (c > o) & (df['rsi_w'] > p.get('rsi_w_thr', 38))
    return _gate_long(df, bs, slm, tpm, cd)


def sig_liquidation_reversal_short(df, p):
    """Spike alcista (sweep de stops shorts) seguido de caida rapida."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr = df['atr']
    spike_mult  = p.get('spike_mult', 1.8)
    confirm_pct = p.get('confirm_pct', 0.35)

    candle_range = h - l
    # Spike alcista: rango grande + cierre en la mitad superior (bull trap)
    spike_up = (candle_range > atr * spike_mult) & (c > (h + l) / 2)
    # Confirmacion: siguiente vela cae al menos confirm_pct del spike previo
    next_confirm = (o - c) > candle_range.shift(1) * confirm_pct
    bs = spike_up.shift(1) & next_confirm & (c < o) & (df['rsi_w'] < p.get('rsi_w_thr', 52))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# GRUPO 2: Session Opens (London 08:00 UTC, NY 13:00 UTC)
# Las primeras velas de cada sesion tienen mayor volumen institucional.
# La primera vela define la direccion del movimiento de esa sesion.
# ============================================================================

def sig_session_momentum_long(df, p):
    """Primera vela alcista de apertura de sesion con ruptura de high previo."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr = df['atr']
    vol_mult = p.get('vol_mult', 1.3)

    try:
        hour = df.index.hour
        opens = p.get('session_hours', [8, 13])
        is_open = pd.Series(False, index=df.index)
        for h_open in opens:
            is_open = is_open | (hour == h_open)
        # Vela alcista en apertura + ruptura del high de la vela anterior + volumen
        bs = is_open & (c > o) & (c > h.shift(1)) & \
             (df['volume'] > df['vol_ma'] * vol_mult) & \
             (df['rsi_w'] > p.get('rsi_w_thr', 40)) & \
             (c > df['ema50'])
    except Exception:
        bs = pd.Series(False, index=df.index)
    return _gate_long(df, bs, slm, tpm, cd)


def sig_session_momentum_short(df, p):
    """Primera vela bajista de apertura de sesion con ruptura de low previo."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr = df['atr']
    vol_mult = p.get('vol_mult', 1.3)

    try:
        hour = df.index.hour
        opens = p.get('session_hours', [8, 13])
        is_open = pd.Series(False, index=df.index)
        for h_open in opens:
            is_open = is_open | (hour == h_open)
        # Vela bajista en apertura + ruptura del low anterior + volumen
        bs = is_open & (c < o) & (c < l.shift(1)) & \
             (df['volume'] > df['vol_ma'] * vol_mult) & \
             (df['rsi_w'] < p.get('rsi_w_thr', 52)) & \
             (c < df['ema50'])
    except Exception:
        bs = pd.Series(False, index=df.index)
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# GRUPO 3: Multi-TF Momentum
# Momentum alineado en 3 horizontes: corto, medio y largo.
# Cuando los 3 apuntan igual la probabilidad aumenta.
# ============================================================================

def sig_multi_tf_bull(df, p):
    """Momentum alcista confirmado en 3 horizontes temporales simultaneamente."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c = df['close']
    thr = p.get('mom_threshold', 0.003)
    short_lb  = int(p.get('short_lb', 3))
    mid_lb    = int(p.get('mid_lb', 12))
    long_lb   = int(p.get('long_lb', 48))

    mom_s = (c - c.shift(short_lb)) / (c.shift(short_lb) + 1e-9)
    mom_m = (c - c.shift(mid_lb))   / (c.shift(mid_lb)   + 1e-9)
    mom_l = (c - c.shift(long_lb))  / (c.shift(long_lb)  + 1e-9)

    bs = (mom_s > thr) & (mom_m > thr) & (mom_l > thr) & \
         (df['volume'] > df['vol_ma'] * p.get('vol_mult', 1.2)) & \
         (df['rsi_w'] > p.get('rsi_w_thr', 45)) & \
         (~(mom_s.shift(1) > thr) | (mom_s > mom_s.shift(1)))  # acelerando
    return _gate_long(df, bs, slm, tpm, cd)


def sig_multi_tf_bear(df, p):
    """Momentum bajista confirmado en 3 horizontes temporales simultaneamente."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c = df['close']
    thr = p.get('mom_threshold', 0.003)
    short_lb  = int(p.get('short_lb', 3))
    mid_lb    = int(p.get('mid_lb', 12))
    long_lb   = int(p.get('long_lb', 48))

    mom_s = (c - c.shift(short_lb)) / (c.shift(short_lb) + 1e-9)
    mom_m = (c - c.shift(mid_lb))   / (c.shift(mid_lb)   + 1e-9)
    mom_l = (c - c.shift(long_lb))  / (c.shift(long_lb)  + 1e-9)

    bs = (mom_s < -thr) & (mom_m < -thr) & (mom_l < -thr) & \
         (df['volume'] > df['vol_ma'] * p.get('vol_mult', 1.2)) & \
         (df['rsi_w'] < p.get('rsi_w_thr', 48))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# GRUPO 4: Funding Rate Contrarian
# Funding extremadamente positivo = demasiados longs apalancados → señal SHORT.
# Funding extremadamente negativo = demasiados shorts apalancados → señal LONG.
# ============================================================================

def sig_funding_extreme_long(df, p):
    """Funding muy negativo = shorts sobreextendidos → señal contrarian LONG."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    threshold = p.get('threshold', 0.85)

    if 'fr_percentile' in df.columns:
        # Funding muy negativo (percentil bajo = shorts dominan)
        ext_short = df['fr_percentile'] < (1 - threshold)
        dur = ext_short.groupby((ext_short != ext_short.shift()).cumsum()).cumcount()
        min_dur = int(p.get('min_dur', 2))
        confirm = (df['close'] > df['close'].shift(1)) & (df['rsi14'] < 50)
        bs = (dur >= min_dur) & confirm & (df['rsi_w'] > p.get('rsi_w_thr', 38))
    else:
        # Fallback: usar funding_rate directo si disponible
        if 'funding_rate' in df.columns:
            fr = df['funding_rate']
            bs = (fr < fr.rolling(200).quantile(0.15)) & \
                 (df['close'] > df['close'].shift(1)) & \
                 (df['rsi_w'] > p.get('rsi_w_thr', 38))
        else:
            return _gate_long(df, pd.Series(False, index=df.index), slm, tpm, cd)
    return _gate_long(df, bs, slm, tpm, cd)


def sig_funding_extreme_short(df, p):
    """Funding muy positivo = longs sobreextendidos → señal contrarian SHORT."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    threshold = p.get('threshold', 0.85)

    if 'fr_percentile' in df.columns:
        ext_long = df['fr_percentile'] > threshold
        dur = ext_long.groupby((ext_long != ext_long.shift()).cumsum()).cumcount()
        min_dur = int(p.get('min_dur', 2))
        confirm = (df['close'] < df['close'].shift(1)) & (df['rsi14'] > 50)
        bs = (dur >= min_dur) & confirm & (df['rsi_w'] < p.get('rsi_w_thr', 52))
    else:
        if 'funding_rate' in df.columns:
            fr = df['funding_rate']
            bs = (fr > fr.rolling(200).quantile(0.85)) & \
                 (df['close'] < df['close'].shift(1)) & \
                 (df['rsi_w'] < p.get('rsi_w_thr', 52))
        else:
            return _gate_short(df, pd.Series(False, index=df.index), slm, tpm, cd)
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# GRUPO 5: Bollinger Squeeze + Expansion
# Volatilidad comprimida durante N barras y luego expande en una direccion.
# El squeeze predice el movimiento; la direccion de la expansion define la entrada.
# ============================================================================

def sig_squeeze_expand_long(df, p):
    """Bollinger squeeze seguido de expansion alcista (baja volatilidad -> breakout UP)."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c = df['close']
    period   = int(p.get('bb_period', 20))
    k        = p.get('bb_k', 2.0)
    sq_bars  = int(p.get('squeeze_bars', 5))
    vol_mult = p.get('vol_mult', 1.4)

    sma = c.rolling(period).mean()
    std = c.rolling(period).std()
    bb_width = (2 * k * std) / (sma + 1e-9)  # ancho relativo

    # Squeeze: ancho < percentil 20 de los ultimos 100 periodos
    squeeze_thr = bb_width.rolling(100).quantile(0.20)
    in_squeeze = bb_width < squeeze_thr
    squeeze_duration = in_squeeze.groupby((in_squeeze != in_squeeze.shift()).cumsum()).cumcount()

    # Expansion alcista: salio del squeeze y vela alcista con volumen
    was_squeezing = squeeze_duration.shift(1) >= sq_bars
    expanding_up  = (~in_squeeze) & was_squeezing & (c > sma) & (c > c.shift(1))

    bs = expanding_up & (df['volume'] > df['vol_ma'] * vol_mult) & \
         (df['rsi_w'] > p.get('rsi_w_thr', 42))
    return _gate_long(df, bs, slm, tpm, cd)


def sig_squeeze_expand_short(df, p):
    """Bollinger squeeze seguido de expansion bajista (baja volatilidad -> breakout DOWN)."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c = df['close']
    period   = int(p.get('bb_period', 20))
    k        = p.get('bb_k', 2.0)
    sq_bars  = int(p.get('squeeze_bars', 5))
    vol_mult = p.get('vol_mult', 1.4)

    sma = c.rolling(period).mean()
    std = c.rolling(period).std()
    bb_width = (2 * k * std) / (sma + 1e-9)

    squeeze_thr = bb_width.rolling(100).quantile(0.20)
    in_squeeze = bb_width < squeeze_thr
    squeeze_duration = in_squeeze.groupby((in_squeeze != in_squeeze.shift()).cumsum()).cumcount()

    was_squeezing = squeeze_duration.shift(1) >= sq_bars
    expanding_dn  = (~in_squeeze) & was_squeezing & (c < sma) & (c < c.shift(1))

    bs = expanding_dn & (df['volume'] > df['vol_ma'] * vol_mult) & \
         (df['rsi_w'] < p.get('rsi_w_thr', 52))
    return _gate_short(df, bs, slm, tpm, cd)


# ============================================================================
# GRUPO 6: Break of Structure (ICT / Smart Money)
# El mercado rompe el ultimo swing high (alcista) o swing low (bajista).
# Es la señal de que el balance de poder cambio.
# ============================================================================

def sig_bos_long(df, p):
    """Break of Structure alcista: precio rompe el ultimo swing high con volumen."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, h = df['close'], df['high']
    lookback = int(p.get('lookback', 20))
    vol_mult = p.get('vol_mult', 1.2)

    # Swing high: maximo de las ultimas N velas
    swing_high = h.rolling(lookback).max().shift(1)
    # BOS: cierre por encima del swing high anterior con volumen
    bos_up = (c > swing_high) & (c.shift(1) <= swing_high.shift(1))

    bs = bos_up & (df['volume'] > df['vol_ma'] * vol_mult) & \
         (df['rsi14'] > 50) & (df['rsi_w'] > p.get('rsi_w_thr', 42))
    return _gate_long(df, bs, slm, tpm, cd)


def sig_bos_short(df, p):
    """Break of Structure bajista: precio rompe el ultimo swing low con volumen."""
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    c, l = df['close'], df['low']
    lookback = int(p.get('lookback', 20))
    vol_mult = p.get('vol_mult', 1.2)

    # Swing low: minimo de las ultimas N velas
    swing_low = l.rolling(lookback).min().shift(1)
    # BOS bajista: cierre por debajo del swing low anterior con volumen
    bos_dn = (c < swing_low) & (c.shift(1) >= swing_low.shift(1))

    bs = bos_dn & (df['volume'] > df['vol_ma'] * vol_mult) & \
         (df['rsi14'] < 50) & (df['rsi_w'] < p.get('rsi_w_thr', 52))
    return _gate_short(df, bs, slm, tpm, cd)
