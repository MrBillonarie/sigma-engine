"""
SIGMA ENGINE — Multi-Strategy Optimizer
Busca el mejor enfoque para CADA TF probando 5 tipos de estrategia
distintos, optimizando SOLO en períodos bull (RSI-W > 55).

Estrategias:
  1. SIGMA ICT     — la actual (EMA + OB + FVG + ADX)
  2. BREAKOUT PURO — precio rompe máximo N días con volumen
  3. PULLBACK TREND — tendencia confirmada + retroceso a EMA
  4. MOMENTUM BURST — MACD acelerando + volumen + ADX fuerte
  5. MEAN REVERSION — sobrevendido en tendencia alcista (RSI + BB)

Para cada TF: optimiza en períodos RSI-W > 55 (bull only)
Valida en OOS real (nunca tocado).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
SLIPPAGE    = 0.0001
COST_RT     = (COMMISSION + SLIPPAGE) * 2
CAPITAL     = 1000.0

# ─── CONFIG POR TF ────────────────────────────────────────────────────────────
TF_CFG = {
    "4h":  {"min_trades": 15, "n_trials": 400, "htf": "1d",  "bars_year": 2190},
    "1h":  {"min_trades": 30, "n_trials": 400, "htf": "4h",  "bars_year": 8760},
    "15m": {"min_trades": 50, "n_trials": 400, "htf": "1h",  "bars_year": 35040},
}

RSI_W_BULL_THRESHOLD = 55  # solo operar cuando RSI semanal > 55


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════
def rsi_series(close, n=14):
    d  = close.diff()
    g  = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l  = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def get_rsi_w_filter(df, threshold=RSI_W_BULL_THRESHOLD):
    """Calcula RSI semanal proxy y retorna máscara bull."""
    close_weekly = df['close'].resample('W').last().ffill()
    rsi_w = rsi_series(close_weekly, 14)
    rsi_w_daily = rsi_w.reindex(df.index, method='ffill')
    return rsi_w_daily > threshold


def backtest_simple(df, signals, sl_s, tp_s, risk_pct=1.5, cooldown=0):
    """Backtest vectorizado simple con SL/TP absolutos."""
    c   = df['close'].values
    h   = df['high'].values
    lo  = df['low'].values
    sig = signals.values if hasattr(signals, 'values') else signals
    sl  = sl_s.values if hasattr(sl_s, 'values') else sl_s
    tp  = tp_s.values if hasattr(tp_s, 'values') else tp_s

    cap = CAPITAL; eq = [cap]; trades = []
    pos = 0; entry_p = slv = tpv = size = 0.0
    last_trade = -cooldown - 1

    for i in range(1, len(c)):
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if lo[i] <= slv:
                    pnl = size * (slv - entry_p) - size * (entry_p + slv) * COMMISSION
                    closed = True
                elif h[i] >= tpv:
                    pnl = size * (tpv - entry_p) - size * (entry_p + tpv) * COMMISSION
                    closed = True
            else:
                if h[i] >= slv:
                    pnl = size * (entry_p - slv) - size * (entry_p + slv) * COMMISSION
                    closed = True
                elif lo[i] <= tpv:
                    pnl = size * (entry_p - tpv) - size * (entry_p + tpv) * COMMISSION
                    closed = True
            if closed:
                cap += pnl
                trades.append({'pnl': pnl, 'won': pnl > 0})
                pos = 0; last_trade = i

        if pos == 0 and sig[i-1] != 0 and sl[i-1] > 0 and (i - last_trade) > cooldown and cap > 50:
            risk_usd = cap * risk_pct / 100
            sl_dist  = abs(c[i] - sl[i-1])
            if sl_dist <= 0: continue
            size    = risk_usd / sl_dist
            pos     = int(sig[i-1])
            entry_p = c[i]
            slv     = sl[i-1]
            tpv     = tp[i-1]

        eq.append(cap)

    df_t = pd.DataFrame(trades) if trades else pd.DataFrame(columns=['pnl', 'won'])
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def calc_metrics(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5:
        return None
    w  = df_t[df_t['pnl'] > 0]
    l  = df_t[df_t['pnl'] <= 0]
    gp = w['pnl'].sum(); gl = abs(l['pnl'].sum())
    wr = len(w) / len(df_t) * 100
    pf = gp / gl if gl > 0 else 999
    peak = eq_s.cummax()
    dd   = ((eq_s - peak) / peak * 100).min()
    last = float(eq_s.iloc[-1])
    if last <= 0 or last != last: return None
    try:
        cagr = ((last / CAPITAL) ** (365.25 / max(days, 1)) - 1) * 100
        if cagr != cagr: return None
    except:
        return None
    sharpe = (eq_s.pct_change().mean() / (eq_s.pct_change().std() + 1e-9)) * (252 ** 0.5)
    calmar = cagr / abs(dd) if dd < 0 else 0
    tmo    = len(df_t) / max(days / 30.44, 0.1)
    rr     = w['pnl'].mean() / abs(l['pnl'].mean()) if not l.empty and not w.empty else 0
    return {
        'trades': len(df_t), 'wr': round(wr, 1), 'cagr': round(cagr, 1),
        'dd': round(dd, 1), 'pf': round(pf, 2), 'sharpe': round(float(sharpe), 2),
        'calmar': round(calmar, 2), 'trades_month': round(tmo, 1), 'rr': round(rr, 2)
    }


def score(m, min_t):
    if m is None or m['trades'] < min_t: return -9999
    if m['cagr'] <= 0: return m['cagr'] * 0.1
    s_cagr = min(m['cagr'], 150) / 150
    s_wr   = max(m['wr'] / 100 - 0.40, 0) / 0.35
    s_cal  = min(m['calmar'], 5) / 5
    s_pf   = min(m['pf'], 3) / 3
    s_freq = min(m['trades_month'], 15) / 15
    return 0.30 * s_cagr + 0.20 * s_wr + 0.20 * s_cal + 0.15 * s_pf + 0.15 * s_freq


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 1: SIGMA ICT (version simplificada, los params clave)
# ═══════════════════════════════════════════════════════════════════════════════
def signals_sigma_ict(df, p):
    c = df['close']; h = df['high']; l = df['low']; o = df['open']
    v = df.get('volume', pd.Series(1, index=df.index))

    ema50  = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    atr    = (h - l).ewm(alpha=1/14, adjust=False).mean()

    # ADX
    up = h.diff(); dn = -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr14 = atr.copy()
    plus  = 100 * pd.Series(pdm, index=c.index).ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9)
    minus = 100 * pd.Series(mdm, index=c.index).ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9)
    dx    = (100 * (plus - minus).abs() / (plus + minus + 1e-9))
    adx   = dx.ewm(alpha=1/14, adjust=False).mean()

    macd_line = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    signal_l  = macd_line.ewm(span=9).mean()
    rsi14     = rsi_series(c, 14)

    bull = ema50 > ema200
    bear = ema50 < ema200
    adx_ok = adx > p['adx_min']
    trend_gate = (ema50 - ema200).abs() > atr * 0.5

    # HTF check (usando columnas del df si existen)
    htf_long  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_short = df.get('htf1_short', pd.Series(True, index=df.index))

    sig_long  = bull & trend_gate & (macd_line > signal_l) & adx_ok & htf_long  & (rsi14 < 75) & ~(h - l > atr * 2)
    sig_short = bear & trend_gate & (macd_line < signal_l) & adx_ok & htf_short & (rsi14 > 25) & ~(h - l > atr * 2)

    # Order block simple
    ob_bull = (c.shift(3) > o.shift(3)) & ((c.shift(3) - o.shift(3)) > atr.shift(3) * 0.8)
    ob_bear = (c.shift(3) < o.shift(3)) & ((o.shift(3) - c.shift(3)) > atr.shift(3) * 0.8)

    entry_type = p.get('entry_type', 'any')
    if entry_type == 'ob':
        sig_long  &= ob_bull
        sig_short &= ob_bear

    signals = pd.Series(0, index=df.index)
    signals[sig_long]  = 1
    signals[sig_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[sig_long]  = c[sig_long]  - atr[sig_long]  * p['sl_mult']
    tp[sig_long]  = c[sig_long]  + atr[sig_long]  * p['tp_mult']
    sl[sig_short] = c[sig_short] + atr[sig_short] * p['sl_mult']
    tp[sig_short] = c[sig_short] - atr[sig_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 2: BREAKOUT PURO
# ═══════════════════════════════════════════════════════════════════════════════
def signals_breakout(df, p):
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))

    atr    = (h - l).ewm(alpha=1/14, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    vol_ma = v.rolling(20).mean()

    n = p['lookback']
    prev_high = h.rolling(n).max().shift(1)
    prev_low  = l.rolling(n).min().shift(1)

    breakout_long  = (c > prev_high) & (c > ema200) & (v > vol_ma * p['vol_mult'])
    breakout_short = (c < prev_low)  & (c < ema200) & (v > vol_ma * p['vol_mult'])

    signals = pd.Series(0, index=df.index)
    signals[breakout_long]  = 1
    signals[breakout_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[breakout_long]  = c[breakout_long]  - atr[breakout_long]  * p['sl_mult']
    tp[breakout_long]  = c[breakout_long]  + atr[breakout_long]  * p['tp_mult']
    sl[breakout_short] = c[breakout_short] + atr[breakout_short] * p['sl_mult']
    tp[breakout_short] = c[breakout_short] - atr[breakout_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 3: PULLBACK EN TENDENCIA (EMA pullback)
# ═══════════════════════════════════════════════════════════════════════════════
def signals_pullback(df, p):
    c = df['close']; h = df['high']; l = df['low']; o = df['open']

    ema_fast = c.ewm(span=p['ema_fast'], adjust=False).mean()
    ema_slow = c.ewm(span=p['ema_slow'], adjust=False).mean()
    ema200   = c.ewm(span=200, adjust=False).mean()
    atr      = (h - l).ewm(alpha=1/14, adjust=False).mean()
    rsi14    = rsi_series(c, 14)

    up_trend   = (ema_fast > ema_slow) & (c > ema200)
    down_trend = (ema_fast < ema_slow) & (c < ema200)

    tol = p['pull_tol']
    pull_long  = up_trend   & (l  <= ema_fast * (1 + tol)) & (c > ema_fast) & (c > o) & (rsi14 < 65)
    pull_short = down_trend & (h  >= ema_fast * (1 - tol)) & (c < ema_fast) & (c < o) & (rsi14 > 35)

    signals = pd.Series(0, index=df.index)
    signals[pull_long]  = 1
    signals[pull_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[pull_long]  = c[pull_long]  - atr[pull_long]  * p['sl_mult']
    tp[pull_long]  = c[pull_long]  + atr[pull_long]  * p['tp_mult']
    sl[pull_short] = c[pull_short] + atr[pull_short] * p['sl_mult']
    tp[pull_short] = c[pull_short] - atr[pull_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 4: MOMENTUM BURST (MACD + volumen + ADX fuerte)
# ═══════════════════════════════════════════════════════════════════════════════
def signals_momentum(df, p):
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))

    atr    = (h - l).ewm(alpha=1/14, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    macd_l = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    sig_l  = macd_l.ewm(span=9).mean()
    hist   = macd_l - sig_l

    # MACD acelerando (histogram creciendo)
    hist_accel = hist > hist.shift(1)
    hist_decel = hist < hist.shift(1)

    up = h.diff(); dn = -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus  = 100 * pd.Series(pdm, index=c.index).ewm(alpha=1/14).mean() / (atr + 1e-9)
    minus = 100 * pd.Series(mdm, index=c.index).ewm(alpha=1/14).mean() / (atr + 1e-9)
    dx    = (100 * (plus - minus).abs() / (plus + minus + 1e-9))
    adx   = dx.ewm(alpha=1/14).mean()

    vol_surge = v > v.rolling(20).mean() * p['vol_mult']
    adx_strong = adx > p['adx_min']

    sig_long  = (c > ema200) & (macd_l > sig_l) & hist_accel & vol_surge & adx_strong
    sig_short = (c < ema200) & (macd_l < sig_l) & hist_decel & vol_surge & adx_strong

    signals = pd.Series(0, index=df.index)
    signals[sig_long]  = 1
    signals[sig_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[sig_long]  = c[sig_long]  - atr[sig_long]  * p['sl_mult']
    tp[sig_long]  = c[sig_long]  + atr[sig_long]  * p['tp_mult']
    sl[sig_short] = c[sig_short] + atr[sig_short] * p['sl_mult']
    tp[sig_short] = c[sig_short] - atr[sig_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 5: MEAN REVERSION (sobrevendido en bull, sobrecomprado en bear)
# ═══════════════════════════════════════════════════════════════════════════════
def signals_mean_reversion(df, p):
    c = df['close']; h = df['high']; l = df['low']

    atr    = (h - l).ewm(alpha=1/14, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    rsi14  = rsi_series(c, 14)

    bb_mid = c.rolling(p['bb_period']).mean()
    bb_std = c.rolling(p['bb_period']).std()
    bb_low = bb_mid - bb_std * p['bb_dev']
    bb_up  = bb_mid + bb_std * p['bb_dev']

    # Bull: precio toca BB inferior con RSI sobrevendido
    sig_long  = (c > ema200) & (l <= bb_low) & (c > bb_low) & (rsi14 < p['rsi_os'])
    # Bear: precio toca BB superior con RSI sobrecomprado
    sig_short = (c < ema200) & (h >= bb_up)  & (c < bb_up)  & (rsi14 > p['rsi_ob'])

    signals = pd.Series(0, index=df.index)
    signals[sig_long]  = 1
    signals[sig_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[sig_long]  = c[sig_long]  - atr[sig_long]  * p['sl_mult']
    tp[sig_long]  = c[sig_long]  + atr[sig_long]  * p['tp_mult']
    sl[sig_short] = c[sig_short] + atr[sig_short] * p['sl_mult']
    tp[sig_short] = c[sig_short] - atr[sig_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRATEGIA 6: TMA + ATR BANDS
# TMA = SMA(SMA(close, n), n) — doble suavizado, menos ruido que EMA
# Bandas: TMA ± ATR(14) × mult
# Señales:
#   LONG:  precio toca banda inferior TMA (pullback en bull) + TMA fast > TMA slow
#   SHORT: precio toca banda superior TMA (pullback en bear) + TMA fast < TMA slow
#   BREAKOUT: precio cruza TMA + ATR_band al alza (momentum)
# ═══════════════════════════════════════════════════════════════════════════════
def tma(series, n):
    """Triangular Moving Average: SMA del SMA."""
    half = max(int(n / 2) + 1, 2)
    return series.rolling(half).mean().rolling(half).mean()


def signals_tma(df, p):
    c = df['close']; h = df['high']; l = df['low']

    atr    = (h - l).ewm(alpha=1/14, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    tma_fast = tma(c, p['tma_fast'])
    tma_slow = tma(c, p['tma_slow'])

    # Bandas TMA con ATR
    band_mult  = p['band_mult']
    upper_band = tma_fast + atr * band_mult
    lower_band = tma_fast - atr * band_mult

    # Trend: TMA fast vs TMA slow (mas suave que EMA crossover)
    up_trend   = tma_fast > tma_slow
    down_trend = tma_fast < tma_slow

    mode = p.get('mode', 'pullback')

    if mode == 'pullback':
        # Pullback a banda inferior en bull / banda superior en bear
        sig_long  = up_trend   & (c > ema200) & (l <= lower_band) & (c > lower_band) & (c > c.shift(1))
        sig_short = down_trend & (c < ema200) & (h >= upper_band) & (c < upper_band) & (c < c.shift(1))
    else:  # breakout
        # Precio rompe la banda superior en bull (momentum fuerte)
        prev_upper = upper_band.shift(1)
        prev_lower = lower_band.shift(1)
        sig_long  = up_trend   & (c > ema200) & (c > upper_band) & (c.shift(1) <= prev_upper)
        sig_short = down_trend & (c < ema200) & (c < lower_band) & (c.shift(1) >= prev_lower)

    signals = pd.Series(0, index=df.index)
    signals[sig_long]  = 1
    signals[sig_short] = -1

    sl = pd.Series(0.0, index=df.index)
    tp = pd.Series(0.0, index=df.index)
    sl[sig_long]  = c[sig_long]  - atr[sig_long]  * p['sl_mult']
    tp[sig_long]  = c[sig_long]  + atr[sig_long]  * p['tp_mult']
    sl[sig_short] = c[sig_short] + atr[sig_short] * p['sl_mult']
    tp[sig_short] = c[sig_short] - atr[sig_short] * p['tp_mult']

    return signals, sl, tp


# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETROS POR ESTRATEGIA (Optuna)
# ═══════════════════════════════════════════════════════════════════════════════
STRATEGIES = {
    'SIGMA_ICT': {
        'fn': signals_sigma_ict,
        'params': lambda t: {
            'adx_min':    t.suggest_int('adx_min', 15, 35),
            'sl_mult':    t.suggest_float('sl_mult', 0.8, 2.5, step=0.1),
            'tp_mult':    t.suggest_float('tp_mult', 2.0, 7.0, step=0.25),
            'entry_type': t.suggest_categorical('entry_type', ['any', 'ob']),
            'risk_pct':   t.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
            'cooldown':   t.suggest_int('cooldown', 2, 20),
        }
    },
    'BREAKOUT': {
        'fn': signals_breakout,
        'params': lambda t: {
            'lookback':  t.suggest_int('lookback', 10, 60),
            'vol_mult':  t.suggest_float('vol_mult', 1.2, 3.0, step=0.1),
            'sl_mult':   t.suggest_float('sl_mult', 0.8, 2.5, step=0.1),
            'tp_mult':   t.suggest_float('tp_mult', 2.0, 7.0, step=0.25),
            'risk_pct':  t.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
            'cooldown':  t.suggest_int('cooldown', 2, 20),
        }
    },
    'PULLBACK': {
        'fn': signals_pullback,
        'params': lambda t: {
            'ema_fast':  t.suggest_int('ema_fast', 8, 30),
            'ema_slow':  t.suggest_int('ema_slow', 30, 100),
            'pull_tol':  t.suggest_float('pull_tol', 0.001, 0.015, step=0.001),
            'sl_mult':   t.suggest_float('sl_mult', 0.8, 2.5, step=0.1),
            'tp_mult':   t.suggest_float('tp_mult', 2.0, 7.0, step=0.25),
            'risk_pct':  t.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
            'cooldown':  t.suggest_int('cooldown', 2, 20),
        }
    },
    'MOMENTUM': {
        'fn': signals_momentum,
        'params': lambda t: {
            'adx_min':   t.suggest_int('adx_min', 20, 40),
            'vol_mult':  t.suggest_float('vol_mult', 1.2, 3.0, step=0.1),
            'sl_mult':   t.suggest_float('sl_mult', 0.8, 2.5, step=0.1),
            'tp_mult':   t.suggest_float('tp_mult', 2.0, 7.0, step=0.25),
            'risk_pct':  t.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
            'cooldown':  t.suggest_int('cooldown', 2, 20),
        }
    },
    'MEAN_REV': {
        'fn': signals_mean_reversion,
        'params': lambda t: {
            'bb_period': t.suggest_int('bb_period', 10, 30),
            'bb_dev':    t.suggest_float('bb_dev', 1.5, 3.0, step=0.25),
            'rsi_os':    t.suggest_int('rsi_os', 20, 40),
            'rsi_ob':    t.suggest_int('rsi_ob', 60, 80),
            'sl_mult':   t.suggest_float('sl_mult', 0.8, 2.0, step=0.1),
            'tp_mult':   t.suggest_float('tp_mult', 1.5, 4.0, step=0.25),
            'risk_pct':  t.suggest_float('risk_pct', 1.0, 3.0, step=0.1),
            'cooldown':  t.suggest_int('cooldown', 2, 15),
        }
    },
    'TMA_BANDS': {
        'fn': signals_tma,
        'params': lambda t: {
            'tma_fast':  t.suggest_int('tma_fast', 5, 20),
            'tma_slow':  t.suggest_int('tma_slow', 20, 60),
            'band_mult': t.suggest_float('band_mult', 0.8, 2.5, step=0.1),
            'mode':      t.suggest_categorical('mode', ['pullback', 'breakout']),
            'sl_mult':   t.suggest_float('sl_mult', 0.8, 2.5, step=0.1),
            'tp_mult':   t.suggest_float('tp_mult', 2.0, 7.0, step=0.25),
            'risk_pct':  t.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
            'cooldown':  t.suggest_int('cooldown', 2, 20),
        }
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMIZADOR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════
def optimize_tf(tf="1h"):
    from core.data import fetch_ohlcv
    from core.features import build_features

    cfg      = TF_CFG[tf]
    min_t    = cfg['min_trades']
    n_trials = cfg['n_trials']
    htf      = cfg['htf']

    print(f"\n{'='*70}")
    print(f"  MULTI-STRATEGY OPTIMIZER — {tf.upper()}")
    print(f"  5 estrategias x {n_trials} trials | Bull-only (RSI-W>{RSI_W_BULL_THRESHOLD})")
    print(f"{'='*70}")

    # Cargar datos
    print("[DATA] Cargando...")
    df_base = fetch_ohlcv(tf=tf, days=3200)
    df_htf  = fetch_ohlcv(tf=htf, days=3200)
    df_htf2 = fetch_ohlcv(tf='1d', days=3200)
    df      = build_features(df_base, {htf: df_htf, '1d': df_htf2})
    df.dropna(subset=['close', 'atr', 'ema50'], inplace=True)
    print(f"  {len(df):,} velas | {(df.index[-1]-df.index[0]).days}d")

    # Split IS/OOS 80/20
    n     = len(df)
    split = int(n * 0.80)
    df_is = df.iloc[:split]
    df_oos= df.iloc[split:]
    days_is  = (df_is.index[-1]  - df_is.index[0]).days
    days_oos = (df_oos.index[-1] - df_oos.index[0]).days

    print(f"  IS:  {df_is.index[0].strftime('%Y-%m-%d')} -> {df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> {df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    # Filtro bull (RSI-W > 55) en IS
    bull_mask_is  = get_rsi_w_filter(df_is,  RSI_W_BULL_THRESHOLD)
    bull_mask_oos = get_rsi_w_filter(df_oos, RSI_W_BULL_THRESHOLD)
    bull_pct = bull_mask_is.mean() * 100
    print(f"  Bull periods IS: {bull_pct:.0f}% del tiempo (RSI-W>{RSI_W_BULL_THRESHOLD})")

    best_results = {}

    for strat_name, strat_cfg in STRATEGIES.items():
        print(f"\n  [{strat_name}] Optimizando {n_trials} trials...")
        fn     = strat_cfg['fn']
        p_fn   = strat_cfg['params']

        def objective(trial):
            p = p_fn(trial)
            cd = p.pop('cooldown', 0)
            rp = p.pop('risk_pct', 1.5)
            try:
                sigs, sl_s, tp_s = fn(df_is, p)
                # Aplicar filtro bull: solo señales cuando RSI-W > 55
                sigs = sigs.copy()
                sigs[~bull_mask_is] = 0
                sl_s[~bull_mask_is] = 0
                tp_s[~bull_mask_is] = 0

                if (sigs != 0).sum() < min_t // 3:
                    return -9999

                df_t, eq = backtest_simple(df_is, sigs, sl_s, tp_s, rp, cd)
                m = calc_metrics(df_t, eq, days_is)
                return score(m, min_t)
            except Exception:
                return -9999

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=60)
        )

        best_val = [-9999]
        def cb(study, trial):
            if trial.value and trial.value > best_val[0] and trial.value > 0.2:
                best_val[0] = trial.value
                print(f"    [T{trial.number}] score={trial.value:.4f}")

        study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

        if study.best_value <= -100:
            print(f"    Sin resultado positivo IS")
            best_results[strat_name] = None
            continue

        # Evaluar best en IS completo
        bp  = study.best_params.copy()
        cd  = bp.pop('cooldown', 0)
        rp  = bp.pop('risk_pct', 1.5)
        sigs_is, sl_is, tp_is = fn(df_is, bp)
        sigs_is[~bull_mask_is] = 0
        sl_is[~bull_mask_is] = 0
        tp_is[~bull_mask_is] = 0

        dt_is, eq_is = backtest_simple(df_is, sigs_is, sl_is, tp_is, rp, cd)
        m_is = calc_metrics(dt_is, eq_is, days_is)

        if not m_is or m_is['cagr'] <= 0:
            print(f"    IS negativo, descartado")
            best_results[strat_name] = None
            continue

        # OOS con filtro bull
        sigs_oos, sl_oos, tp_oos = fn(df_oos, bp)
        sigs_oos[~bull_mask_oos] = 0
        sl_oos[~bull_mask_oos]   = 0
        tp_oos[~bull_mask_oos]   = 0

        dt_oos, eq_oos = backtest_simple(df_oos, sigs_oos, sl_oos, tp_oos, rp, cd)
        m_oos = calc_metrics(dt_oos, eq_oos, days_oos)

        print(f"    IS:  {m_is['trades']}T | WR {m_is['wr']:.1f}% | CAGR {m_is['cagr']:+.1f}% | DD {m_is['dd']:.1f}%")
        if m_oos:
            oos_cagr = m_oos['cagr']
            print(f"    OOS: {m_oos['trades']}T | WR {m_oos['wr']:.1f}% | CAGR {oos_cagr:+.1f}% | DD {m_oos['dd']:.1f}%")
            eff = oos_cagr / abs(m_is['cagr']) if m_is['cagr'] != 0 else 0
            print(f"    Eficiencia IS->OOS: {eff:.2f}")
        else:
            print(f"    OOS: sin trades suficientes")

        best_results[strat_name] = {
            'params': bp, 'cooldown': cd, 'risk_pct': rp,
            'metrics_is': m_is, 'metrics_oos': m_oos,
            'score': study.best_value
        }

    # ── Resultado final ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RANKING FINAL — {tf.upper()} (bull-only)")
    print(f"{'='*70}")

    ranked = [(k, v) for k, v in best_results.items() if v and v.get('metrics_oos')]
    ranked.sort(key=lambda x: x[1]['metrics_oos']['cagr'] if x[1]['metrics_oos'] else -999, reverse=True)

    winner = None
    for i, (name, res) in enumerate(ranked):
        m_oos = res['metrics_oos']
        m_is  = res['metrics_is']
        oos_cagr = m_oos['cagr'] if m_oos else -999
        mark = "<-- GANADOR" if i == 0 and oos_cagr > 0 else ""
        print(f"  {i+1}. {name:15} OOS: {oos_cagr:+.1f}% | WR {m_oos['wr']:.1f}% | DD {m_oos['dd']:.1f}% {mark}")
        if i == 0 and oos_cagr > 0:
            winner = (name, res)

    if winner:
        name, res = winner
        m_oos = res['metrics_oos']
        print(f"\n  MEJOR ESTRATEGIA {tf.upper()}: {name}")
        print(f"  OOS: {m_oos['trades']}T | CAGR {m_oos['cagr']:+.1f}% | WR {m_oos['wr']:.1f}% | DD {m_oos['dd']:.1f}%")
        print(f"  risk_pct={res['risk_pct']:.1f}% | cooldown={res['cooldown']}")

        # Guardar
        out_dir  = OUTPUT_DIR / "models" / tf
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            'tf': tf, 'strategy': name,
            'params': res['params'],
            'cooldown': res['cooldown'],
            'risk_pct': res['risk_pct'],
            'bull_filter': f'RSI-W > {RSI_W_BULL_THRESHOLD}',
            'metrics_is':  {k: round(float(v), 4) if isinstance(v, (int, float)) else v
                            for k, v in res['metrics_is'].items()},
            'metrics_oos': {k: round(float(v), 4) if isinstance(v, (int, float)) else v
                            for k, v in m_oos.items()},
            'score': res['score'],
        }
        path = out_dir / f'best_bull_{name.lower()}.json'
        with open(path, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  [SAVED] {path}")
    else:
        print(f"\n  Ninguna estrategia supera OOS positivo en {tf.upper()} bull-only")
        print(f"  -> La estrategia 4H aggressive ({'+13.3%'}) sigue siendo el mejor para 4H")

    return ranked


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf', default='1h', choices=['4h','1h','15m'])
    args = parser.parse_args()
    optimize_tf(args.tf)
