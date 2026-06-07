"""
SIGMA ENGINE — Crypto-Specific Strategies
Estrategias diseñadas para las caracteristicas unicas de BTC/USDT Futuros.

1. LIQUIDATION CASCADE — sweeps de liquidaciones + reversal
2. FUNDING RATE EXTREME — crowding de posiciones → contrarian
3. SESSION OPEN BREAKOUT — los primeros 30min de London/NY tienen el mejor edge
4. CVD DIVERGENCE — precio y volumen apuntan en distintas direcciones
5. STRUCTURE + OFI — estructura ICT confirmada por flujo de ordenes
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
import random
import numpy as np
import pandas as pd
import json
import warnings
import subprocess
import winsound
from pathlib import Path
from datetime import datetime, timedelta, timezone
warnings.filterwarnings('ignore')

random.seed(77); np.random.seed(77)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004
SLIPPAGE   = 0.0005
COST       = COMMISSION + SLIPPAGE

# ─── DESCARGA FUNDING RATE ────────────────────────────────────────────────────
def fetch_funding_rate(days=180):
    """Descarga historial de funding rate de Binance Futuros."""
    try:
        print("  [FUNDING] Descargando funding rate historico...")
        ex = ccxt.binance({'timeout':30000,'options':{'defaultType':'future'}})
        since = ex.parse8601(
            (datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)).strftime('%Y-%m-%dT00:00:00Z')
        )
        all_fr = []
        while True:
            fr = ex.fetch_funding_rate_history('BTC/USDT', since=since, limit=1000)
            if not fr: break
            all_fr += fr
            since = fr[-1]['timestamp'] + 1
            if len(fr) < 1000: break

        df = pd.DataFrame(all_fr)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['rate'] = df['fundingRate'].astype(float)
        print(f"  [FUNDING] {len(df)} registros (cada 8h)")
        return df[['rate']]
    except Exception as e:
        print(f"  [FUNDING] Error: {e} — usando funding rate simulado")
        return None


def merge_funding(df_15m, df_funding):
    """Agrega funding rate al DataFrame de precios."""
    if df_funding is None or df_funding.empty:
        df_15m['funding_rate'] = 0.0001  # default neutral
        df_15m['funding_extreme_long']  = False
        df_15m['funding_extreme_short'] = False
        return df_15m

    df = pd.merge_asof(
        df_15m.reset_index(),
        df_funding.reset_index(),
        on='timestamp',
        direction='backward'
    ).set_index('timestamp')
    df['funding_rate'] = df['rate'].fillna(0.0001)

    # Extremos: funding en percentil 90+ (longs muy crowded) o 10- (shorts crowded)
    fr_p90 = df['funding_rate'].rolling(500, min_periods=50).quantile(0.90)
    fr_p10 = df['funding_rate'].rolling(500, min_periods=50).quantile(0.10)
    df['funding_extreme_long']  = df['funding_rate'] > fr_p90   # longs crowded → bajista
    df['funding_extreme_short'] = df['funding_rate'] < fr_p10   # shorts crowded → alcista
    return df


# ─── ESTRATEGIA 1: LIQUIDATION CASCADE ───────────────────────────────────────
def sig_liquidation_cascade(df, cfg):
    """
    BTC tiene patrones repetibles de liquidacion:
    1. Precio sube agresivamente (sweep de shorts / liq de stops)
    2. Luego revierte — es la "trampa" clasica

    Setup:
    - Spike grande (vela > 2x ATR) con close cerca del extremo (bull trap o bear trap)
    - Siguiente vela confirma la reversal
    - Entrada en la direccion contraria al spike
    """
    sig = pd.Series(0, index=df.index)
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr  = df['atr']

    spike_size  = cfg.get('spike_mult', 1.8)
    confirm_pct = cfg.get('confirm_pct', 0.3)  # vela siguiente mueve X% del spike

    candle_range = h - l
    is_spike     = candle_range > atr * spike_size

    # Bull trap: spike alcista pero cierra en la parte baja (wick arriba)
    upper_wick   = h - c.clip(lower=o)
    bull_trap    = is_spike & (upper_wick / candle_range > 0.65) & (c > o.shift(1))
    # Bear trap: spike bajista pero cierra en la parte alta
    lower_wick   = c.clip(upper=o) - l
    bear_trap    = is_spike & (lower_wick / candle_range > 0.65) & (c < o.shift(1))

    # Confirmacion: la vela del trap cierra en la direccion del reversal (sin lookahead)
    cur_bull = df['close'] > df['open']
    cur_bear = df['close'] < df['open']

    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))

    # Long: bear trap (barrido de shorts) + cierre alcista en la misma vela
    long_raw  = bear_trap & cur_bull & htf_l
    # Short: bull trap (barrido de longs) + cierre bajista en la misma vela
    short_raw = bull_trap & cur_bear & htf_s

    base = df.get('gap_ok', pd.Series(True, index=df.index))

    cd = cfg.get('cooldown', 6)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]  & base.iloc[i]: sig.iloc[i] = 1;  last = i
        elif short_raw.iloc[i] & base.iloc[i]: sig.iloc[i] = -1; last = i
    return sig


# ─── ESTRATEGIA 2: FUNDING RATE CONTRARIAN ───────────────────────────────────
def sig_funding_contrarian(df, cfg):
    """
    Cuando el funding rate esta en extremos:
    - Funding muy alto (> percentil 90): longs pagan demasiado → mercado sobrecargado de longs
      → alta probabilidad de corrección → SHORT
    - Funding muy negativo (< percentil 10): shorts pagan demasiado → mercado sobrecargado
      → alta probabilidad de rebote → LONG

    Se combina con estructura tecnica para evitar entrar en tendencias fuertes.
    """
    sig = pd.Series(0, index=df.index)

    if 'funding_extreme_long' not in df.columns:
        return sig

    fe_long  = df['funding_extreme_long']   # funding alto → longs crowded → short
    fe_short = df['funding_extreme_short']  # funding bajo → shorts crowded → long

    # Necesita confirmacion tecnica
    rsi  = df.get('rsi', pd.Series(50, index=df.index))
    macd = df.get('macd', pd.Series(0, index=df.index))
    sig_m= df.get('macd_signal', pd.Series(0, index=df.index))
    atr  = df['atr']

    # RSI confirma sobrecompra/sobreventa
    rsi_ob = rsi > cfg.get('rsi_ob', 65)  # sobrecomprado
    rsi_os = rsi < cfg.get('rsi_os', 35)  # sobrevendido

    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))

    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok',    pd.Series(True, index=df.index))

    hour   = df.index.hour
    in_sess= ((hour >= 8) & (hour < 20))

    # Short cuando longs crowded + RSI sobrecomprado
    short_raw = fe_long  & rsi_ob & (macd < sig_m) & base & in_sess
    # Long cuando shorts crowded + RSI sobrevendido
    long_raw  = fe_short & rsi_os & (macd > sig_m) & base & in_sess

    if cfg.get('use_htf', True):
        long_raw  = long_raw  & htf_l
        short_raw = short_raw & htf_s

    cd = cfg.get('cooldown', 12)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]:  sig.iloc[i] = 1;  last = i
        elif short_raw.iloc[i]: sig.iloc[i] = -1; last = i
    return sig


# ─── ESTRATEGIA 3: SESSION OPEN BREAKOUT ─────────────────────────────────────
def sig_session_open_breakout(df, cfg):
    """
    Los primeros 15-45 min de London (08:00 UTC) y NY (13:00 UTC) tienen
    el mayor edge en BTC — es cuando los institucionales entran.

    Setup:
    - Primera vela de la sesion define la direccion
    - Si es alcista y rompe el high previo → LONG
    - Si es bajista y rompe el low previo → SHORT
    - Solo en las primeras N velas de cada sesion
    """
    sig = pd.Series(0, index=df.index)
    c, h, l = df['close'], df['high'], df['low']
    atr = df['atr']

    hour    = df.index.hour
    minutes = df.index.minute
    dow     = df.index.dayofweek

    sess_opens = cfg.get('session_opens', [8, 13])  # London y NY
    window     = cfg.get('window_bars', 3)           # primeras N velas
    min_move   = cfg.get('min_move', 0.5)            # movimiento minimo en ATR

    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))
    day_ok = pd.Series(dow, index=df.index).isin([1,2,3,4])  # Mar-Vie

    for sess_hour in sess_opens:
        # Velas dentro de la ventana de apertura
        in_window = (hour == sess_hour) & (minutes < window * 15)

        # Primera vela de la sesion
        is_first = (hour == sess_hour) & (minutes == 0)

        # Direccion de la primera vela
        first_bull = is_first & (c > c.shift(1)) & ((c-c.shift(1)) > atr*min_move)
        first_bear = is_first & (c < c.shift(1)) & ((c.shift(1)-c) > atr*min_move)

        # Breakout: en las primeras N velas, precio rompe el high/low de la sesion
        sess_high = h.where(in_window).rolling(window, min_periods=1).max()
        sess_low  = l.where(in_window).rolling(window, min_periods=1).min()

        bull_bo = in_window & (c > sess_high.shift(1)) & htf_l & day_ok & \
                  ~df.get('fake_move', pd.Series(False, index=df.index))
        bear_bo = in_window & (c < sess_low.shift(1))  & htf_s & day_ok & \
                  ~df.get('fake_move', pd.Series(False, index=df.index))

        sig[bull_bo] = 1
        sig[bear_bo] = -1

    # Cooldown
    cd = cfg.get('cooldown', 8)
    final = pd.Series(0, index=df.index)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if sig.iloc[i] != 0:
            final.iloc[i] = sig.iloc[i]; last = i
    return final


# ─── ESTRATEGIA 4: CVD DIVERGENCE ────────────────────────────────────────────
def sig_cvd_divergence(df, cfg):
    """
    Cuando el precio hace nuevos maximos pero el CVD (volumen acumulado neto)
    no confirma → divergencia bajista → SHORT (y viceversa).

    Es una forma de detectar distribucion/acumulacion antes de que el precio se mueva.
    """
    sig  = pd.Series(0, index=df.index)
    c, h, l = df['close'], df['high'], df['low']
    cvd  = df.get('cvd', pd.Series(0, index=df.index))
    lb   = cfg.get('lookback', 20)

    price_hh = h >= h.rolling(lb).max().shift(1)
    price_ll = l <= l.rolling(lb).min().shift(1)
    cvd_hh   = cvd >= cvd.rolling(lb).max().shift(1)
    cvd_ll   = cvd <= cvd.rolling(lb).min().shift(1)

    # Divergencia bajista: precio HH pero CVD no confirma
    bear_div = price_hh & ~cvd_hh & (cvd < cvd.shift(lb//2))
    # Divergencia alcista: precio LL pero CVD no confirma
    bull_div = price_ll & ~cvd_ll & (cvd > cvd.shift(lb//2))

    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))
    atr   = df['atr']

    # Confirmacion adicional
    rsi  = df.get('rsi', pd.Series(50, index=df.index))
    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok', pd.Series(True, index=df.index))

    hour   = df.index.hour
    in_sess= ((hour >= 8) & (hour < 20))

    long_raw  = bull_div & htf_l & base & in_sess
    short_raw = bear_div & htf_s & base & in_sess

    if cfg.get('rsi_filter', True):
        long_raw  = long_raw  & (rsi < 45)
        short_raw = short_raw & (rsi > 55)

    cd = cfg.get('cooldown', 10)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]:  sig.iloc[i] = 1;  last = i
        elif short_raw.iloc[i]: sig.iloc[i] = -1; last = i
    return sig


# ─── ESTRATEGIA 5: ESTRUCTURA + OFI ──────────────────────────────────────────
def sig_structure_ofi(df, cfg):
    """
    Combina estructura de mercado (BOS/ChoCH) con Order Flow Imbalance.
    Solo entra cuando AMBOS confirman la misma direccion.
    Alta precision — pocos trades pero de calidad.
    """
    sig  = pd.Series(0, index=df.index)
    sw   = cfg.get('swing_len', 10)
    c, h, l = df['close'], df['high'], df['low']

    swing_h = h.rolling(sw).max().shift(sw)
    swing_l = l.rolling(sw).min().shift(sw)

    bull = df.get('bull', c > c.ewm(50).mean())
    bear = df.get('bear', c < c.ewm(50).mean())

    # BOS
    bos_bull = (c > swing_h) & (c.shift(1) <= swing_h.shift(1)) & bull
    bos_bear = (c < swing_l) & (c.shift(1) >= swing_l.shift(1)) & bear

    # OFI confirma
    ofi      = df.get('ofi', pd.Series(0, index=df.index))
    ofi_thr  = cfg.get('ofi_threshold', 0.4)
    ofi_bull = ofi >  ofi_thr
    ofi_bear = ofi < -ofi_thr

    # HTF
    htf_l  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s  = df.get('htf1_short', pd.Series(False, index=df.index))
    htf2_l = df.get('htf2_long',  pd.Series(True, index=df.index))

    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok', pd.Series(True, index=df.index))

    hour   = df.index.hour; dow = df.index.dayofweek
    in_sess= ((hour >= 8) & (hour < 20))
    dow_ok = pd.Series(dow, index=df.index).isin([1,2,3,4])

    long_raw  = bos_bull & ofi_bull & htf_l & htf2_l & base & in_sess & dow_ok
    short_raw = bos_bear & ofi_bear & htf_s & ~htf2_l & base & in_sess & dow_ok

    cd = cfg.get('cooldown', 8)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]:  sig.iloc[i] = 1;  last = i
        elif short_raw.iloc[i]: sig.iloc[i] = -1; last = i
    return sig


# ─── BACKTEST ─────────────────────────────────────────────────────────────────
def backtest(df, signals, sl_mult, tp_mult, risk=0.5, use_trail=False, trail_mult=2.0):
    cap=CAPITAL; eq=[cap]; pos=0; entry=sl=tp=trail=size=0.0; trades=[]
    for i in range(1,len(df)):
        row=df.iloc[i]; prev=df.iloc[i-1]
        sig=signals.iloc[i-1]; pr=row['close']; atr=prev['atr']
        h_=row['high']; lo=row['low']
        if pos!=0:
            pnl=0.0; closed=False; reason=''
            if use_trail:
                if pos==1: trail=max(trail,h_-atr*trail_mult)
                else:      trail=min(trail,lo+atr*trail_mult)
                exit_p = trail
                if (pos==1 and lo<=trail) or (pos==-1 and h_>=trail):
                    pnl=pos*size*(trail-entry)-size*(entry+trail)*COST; closed=True; reason='Trail'
            else:
                if pos==1:
                    if lo<=sl: pnl=size*(sl-entry)-size*(entry+sl)*COST; closed=True; reason='SL'
                    elif h_>=tp: pnl=size*(tp-entry)-size*(entry+tp)*COST; closed=True; reason='TP'
                else:
                    if h_>=sl: pnl=size*(entry-sl)-size*(entry+sl)*COST; closed=True; reason='SL'
                    elif lo<=tp: pnl=size*(entry-tp)-size*(entry+tp)*COST; closed=True; reason='TP'
            if not closed and sig==-pos:
                pnl=pos*size*(pr-entry)-size*(entry+pr)*COST; closed=True; reason='Sig'
            if closed:
                cap+=pnl; trades.append({'pnl':pnl,'won':pnl>0,'reason':reason}); pos=0
        if pos==0 and sig!=0 and cap>50:
            pos=sig; entry=pr; r_sl=atr*sl_mult
            sl=entry-r_sl if pos==1 else entry+r_sl
            tp=entry+atr*tp_mult if pos==1 else entry-atr*tp_mult
            trail=sl; size=(cap*risk/100)/r_sl if r_sl>0 else 0
        eq.append(cap)
    df_t=pd.DataFrame(trades)
    eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<5: return None
    w=df_t[df_t['pnl']>0]; l=df_t[df_t['pnl']<=0]
    gp=w['pnl'].sum(); gl=abs(l['pnl'].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    ret=eq_s.pct_change().dropna()
    sh=ret.mean()/ret.std()*np.sqrt(35040) if ret.std()>0 else 0
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    import scipy.stats as st
    se=np.sqrt(wr*(1-wr)/len(df_t)); z=st.norm.ppf(0.975)
    return {
        'trades':len(df_t),'winrate':round(wr*100,1),
        'ci_low':round((wr-z*se)*100,1),'ci_high':round((wr+z*se)*100,1),
        'cagr':round(cagr,2),'sharpe':round(sh,3),
        'max_dd':round(dd.min(),2),'pf':round(gp/gl,3) if gl>0 else 999,
        'calmar':round(cagr/abs(dd.min()),3) if dd.min()<0 else 0,
        'rr_real':round(w['pnl'].mean()/abs(l['pnl'].mean()),2) if not l.empty and not w.empty else 0,
    }


def score(m):
    if m is None or m['trades']<40: return -9999
    pen = max(0,(150-m['trades'])/150)*1.5
    cal = min(m['calmar'],5)/5
    wr  = (m['winrate']/100-0.45)/0.3
    pf  = min(m['pf'],4)/4
    sh  = max(min(m['sharpe'],3),-3)/3
    return 0.35*cal+0.25*wr+0.20*pf+0.20*sh-pen


# ─── SEARCH PRINCIPAL ─────────────────────────────────────────────────────────
CRYPTO_STRATEGIES = {
    'Liquidation Cascade': (sig_liquidation_cascade, {
        'spike_mult':  [1.5,1.8,2.0,2.5],
        'confirm_pct': [0.2,0.3,0.4],
        'cooldown':    [4,6,8,10],
    }),
    'Funding Rate Contrarian': (sig_funding_contrarian, {
        'rsi_ob':    [60,65,70],
        'rsi_os':    [30,35,40],
        'use_htf':   [True,False],
        'cooldown':  [8,12,16,20],
    }),
    'Session Open Breakout': (sig_session_open_breakout, {
        'window_bars':    [2,3,4],
        'min_move':       [0.3,0.5,0.7],
        'session_opens':  [[8],[13],[8,13],[1,8,13]],
        'cooldown':       [4,6,8],
    }),
    'CVD Divergence': (sig_cvd_divergence, {
        'lookback':    [14,20,30],
        'rsi_filter':  [True,False],
        'cooldown':    [8,10,12],
    }),
    'Structure + OFI': (sig_structure_ofi, {
        'swing_len':     [8,10,14],
        'ofi_threshold': [0.3,0.4,0.5,0.6],
        'cooldown':      [6,8,10],
    }),
}

SL_RANGE    = [1.0,1.3,1.5,1.7,2.0,2.3]
TP_RANGE    = [2.0,2.5,3.0,3.5,4.0,4.5]
TRAIL_RANGE = [1.5,2.0,2.5,3.0]
N_SAMPLES   = 500  # por estrategia


def run_crypto_search(tf='15m', n_per_strategy=N_SAMPLES):
    from core.data import fetch_ohlcv
    from core.features import build_features

    print(f"\n{'='*65}")
    print(f"  CRYPTO STRATEGY SEARCH — {tf.upper()}")
    print(f"  {list(CRYPTO_STRATEGIES.keys())}")
    print(f"  {n_per_strategy} muestras x {len(CRYPTO_STRATEGIES)} estrategias")
    print(f"{'='*65}")

    TF_MAP = {'15m':('1h','4h',365),'1h':('4h','1d',730),'5m':('15m','1h',180)}
    htf1,htf2,days = TF_MAP.get(tf,('1h','4h',365))

    df_b  = fetch_ohlcv(tf=tf,   days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1:df_h1, htf2:df_h2})
    df.dropna(subset=['close','atr','ema50'],inplace=True)

    # Funding rate
    df_fr = fetch_funding_rate(days=days)
    df    = merge_funding(df, df_fr)

    # OOS split
    split  = int(len(df)*0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"  {len(df):,} velas | IS: {days_is}d | OOS: {days_oos}d\n")

    all_valid  = []
    best_global= None; best_score_g = -9999
    best_name  = ''; best_cfg_g = {}

    for strat_name,(fn,param_space) in CRYPTO_STRATEGIES.items():
        print(f"  [{strat_name}]")
        best_strat_s = -9999; best_strat_m = None

        for _ in range(n_per_strategy):
            cfg = {k:random.choice(v) for k,v in param_space.items()}
            sl  = random.choice(SL_RANGE)
            tp  = random.choice(TP_RANGE)
            use_trail = random.choice([True,False])
            trail = random.choice(TRAIL_RANGE) if use_trail else 2.0
            if tp <= sl: continue

            try:
                sig = fn(df_is, cfg)
                if (sig!=0).sum() < 20: continue
                m = backtest(df_is,sig,sl,tp,use_trail=use_trail,trail_mult=trail)
                if m is None: continue
                s = score(m)

                if s > best_strat_s:
                    best_strat_s = s; best_strat_m = m.copy()

                if s > best_score_g:
                    best_score_g = s; best_global = m.copy()
                    best_name = strat_name
                    best_cfg_g = {**cfg,'sl':sl,'tp':tp,'trail':use_trail,'trail_mult':trail}
                    if m['trades']>=100 and m['cagr']>10:
                        print(f"  *** NUEVO MEJOR ({strat_name}) ***")
                        print(f"  {m['trades']}T | WR {m['winrate']:.1f}% [{m['ci_low']:.1f}-{m['ci_high']:.1f}%] | "
                              f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f} | R:R {m['rr_real']:.2f}")

                if (m['trades']>=100 and m['winrate']>=52 and
                    m['calmar']>=1.0 and m['cagr']>0):
                    all_valid.append({**m,'strategy':strat_name,'cfg':cfg,
                                      'sl':sl,'tp':tp,'trail':use_trail})
            except Exception:
                continue

        if best_strat_m:
            print(f"  Mejor: {best_strat_m['trades']}T | WR {best_strat_m['winrate']:.1f}% | "
                  f"CAGR {best_strat_m['cagr']:+.1f}%/año | Calmar {best_strat_m['calmar']:.2f}")

    # OOS del mejor
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_name}")
    print(f"{'='*65}")

    if best_name and best_cfg_g:
        fn,_ = CRYPTO_STRATEGIES[best_name]
        inner_cfg = {k:v for k,v in best_cfg_g.items()
                     if k not in ('sl','tp','trail','trail_mult')}
        try:
            sig_oos = fn(df_oos, inner_cfg)
            m_oos = backtest(df_oos, sig_oos,
                             best_cfg_g['sl'], best_cfg_g['tp'],
                             use_trail=best_cfg_g.get('trail',False),
                             trail_mult=best_cfg_g.get('trail_mult',2.0))
            if m_oos:
                print(f"  OOS: {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% "
                      f"[{m_oos['ci_low']:.1f}-{m_oos['ci_high']:.1f}%] | "
                      f"CAGR {m_oos['cagr']:+.1f}%/año | "
                      f"Calmar {m_oos['calmar']:.2f} | R:R {m_oos['rr_real']:.2f}")

                if m_oos['cagr']>0 and m_oos['calmar']>=0.8:
                    verdict = "EDGE REAL — llevar a produccion"
                else:
                    verdict = "Sin edge en OOS — seguir buscando"
                print(f"  VEREDICTO: {verdict}")

                # Guardar
                model_dir = OUTPUT_DIR/'models'/tf
                model_dir.mkdir(parents=True,exist_ok=True)
                with open(model_dir/'crypto_best.json','w') as f:
                    json.dump({'strategy':best_name,'params':best_cfg_g,
                               'metrics_is':best_global,'metrics_oos':m_oos,
                               'valid_configs':len(all_valid)},f,indent=2)
                print(f"  [SAVED] models/{tf}/crypto_best.json")
        except Exception as e:
            print(f"  OOS error: {e}")

    # Ranking
    all_valid.sort(key=lambda x:x.get('calmar',0),reverse=True)
    print(f"\n  Configs validas (100+T, WR>=52%, Calmar>=1): {len(all_valid)}")
    if all_valid:
        print(f"\n  TOP 5:")
        for i,m in enumerate(all_valid[:5],1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | WR {m['winrate']:.1f}% "
                  f"[{m['ci_low']:.1f}-{m['ci_high']:.1f}%] | "
                  f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f}")

    # CSV
    if all_valid:
        rows=[];
        for m in all_valid:
            r={k:v for k,v in m.items() if k!='cfg'}; rows.append(r)
        pd.DataFrame(rows).to_csv(
            OUTPUT_DIR/'results'/'reports'/f'crypto_strategies_{tf}.csv',index=False)

    print(f"\n[DONE] Crypto strategy search completado.")

    # Notificar
    try:
        for _ in range(4): winsound.Beep(1200,300)
        m = all_valid[0] if all_valid else {}
        msg=(f"CRYPTO SEARCH {tf.upper()} LISTO!\\n\\n"
             f"Configs validas: {len(all_valid)}\\n\\n"
             f"MEJOR: {m.get('strategy','?')}\\n"
             f"Trades: {m.get('trades',0)} | WR: {m.get('winrate',0):.1f}% "
             f"[{m.get('ci_low',0):.1f}-{m.get('ci_high',0):.1f}%]\\n"
             f"CAGR: {m.get('cagr',0):+.1f}%/año | Calmar: {m.get('calmar',0):.2f}\\n\\n"
             f"Ver: models/{tf}/crypto_best.json")
        subprocess.Popen(['powershell','-WindowStyle','Hidden','-Command',
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","Crypto Search","OK","Information")'])
    except: pass

    return all_valid


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--tf',      default='15m')
    parser.add_argument('--samples', type=int, default=N_SAMPLES)
    args=parser.parse_args()
    run_crypto_search(args.tf, args.samples)
