"""
SIGMA ENGINE — Nuevas estrategias a explorar
Enfoques completamente distintos a EMA/MACD que no hemos probado.

1. Pure Price Action — velas clave (engulfing, pin bar, inside bar)
2. Delta / CVD puro — momentum de volumen sin precio
3. VWAP estrategia — zonas de valor justo
4. Soporte/Resistencia — niveles clave con rebote
5. Volatility breakout — esperar compresion y explotar la expansion
6. Funding rate arbitrage — aprovechar extremos de funding
7. Market structure — BOS + ChoCH puro (ICT)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import numpy as np
import pandas as pd
import json
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

random.seed(99); np.random.seed(99)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004
SLIPPAGE   = 0.0005
COST       = COMMISSION + SLIPPAGE  # 0.09% por lado

TF_MAP = {
    '15m': ('1h',  '4h',  180),
    '1h':  ('4h',  '1d',  365),
    '5m':  ('15m', '1h',  90),
}


# ─── SEÑALES NUEVAS ───────────────────────────────────────────────────────────

def sig_price_action(df, cfg):
    """
    Pure price action: engulfing + pin bar + inside bar breakout.
    Sin indicadores de tendencia — solo estructura de velas.
    """
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    sig = pd.Series(0, index=df.index)

    body     = (c - o).abs()
    candle_r = (h - l).replace(0, np.nan)
    atr      = df['atr']

    # Engulfing alcista: vela roja seguida de vela verde que la engloba
    bull_eng = (c > o) & (c[1] < o[1]) & (c > o[1]) & (o < c[1]) & (body > atr * 0.5)
    # Engulfing bajista
    bear_eng = (c < o) & (c[1] > o[1]) & (c < o[1]) & (o > c[1]) & (body > atr * 0.5)

    # Pin bar alcista: lower wick > 60% del rango, vela pequeña
    lower_wick = (l - o.clip(upper=c)).clip(lower=0)
    bull_pin   = (lower_wick / candle_r > 0.60) & (body / candle_r < 0.25) & (candle_r > atr * 0.7)
    # Pin bar bajista
    upper_wick = (h - o.clip(lower=c))
    bear_pin   = (upper_wick / candle_r > 0.60) & (body / candle_r < 0.25) & (candle_r > atr * 0.7)

    # Filtro: solo en zona de soporte/resistencia (cerca de HTF EMA)
    near_ema50 = (c - df['ema50']).abs() / c < 0.005  # dentro del 0.5%

    # Con HTF
    htf_l = df.get('htf1_long',  pd.Series(True,  index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))

    # Cooldown
    min_bars = cfg.get('cooldown', 6)
    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
           ~df.get('is_spike',  pd.Series(False, index=df.index)) & \
           df.get('gap_ok', pd.Series(True, index=df.index))

    long_raw  = (bull_eng | bull_pin) & base & htf_l
    short_raw = (bear_eng | bear_pin) & base & htf_s

    sig[long_raw]  = 1
    sig[short_raw] = -1
    return _apply_cooldown(sig, min_bars)


def sig_cvd_momentum(df, cfg):
    """
    CVD puro: entra cuando el CVD rompe sus maximos/minimos.
    Sin indicadores de precio — solo presion compradora/vendedora acumulada.
    """
    sig   = pd.Series(0, index=df.index)
    cvd   = df['cvd']
    lb    = cfg.get('lookback', 20)
    atr   = df['atr']

    cvd_hh = cvd.rolling(lb).max().shift(1)
    cvd_ll = cvd.rolling(lb).min().shift(1)

    bull_m = df.get('bull', pd.Series(True, index=df.index))
    bear_m = df.get('bear', pd.Series(False, index=df.index))
    htf_l  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s  = df.get('htf1_short', pd.Series(False, index=df.index))

    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('vol_ok', df['volume'] > df['volume'].rolling(20).mean())

    # CVD rompe al alza + precio bull
    long_raw  = (cvd > cvd_hh) & (cvd.shift(1) <= cvd_hh.shift(1)) & bull_m & htf_l & base
    short_raw = (cvd < cvd_ll) & (cvd.shift(1) >= cvd_ll.shift(1)) & bear_m & htf_s & base

    sig[long_raw]  = 1
    sig[short_raw] = -1
    return _apply_cooldown(sig, cfg.get('cooldown', 8))


def sig_vwap_bounce(df, cfg):
    """
    VWAP bounce: precio toca el AVWAP y rebota.
    Alta precision si se combina con confluencia de volumen.
    """
    sig  = pd.Series(0, index=df.index)
    avwap = df['avwap']
    atr   = df['atr']
    tol   = cfg.get('tolerance', 0.003)  # 0.3% del precio

    # Toca AVWAP desde abajo (soporte) → long
    touch_from_below = (df['low'] <= avwap * (1 + tol)) & \
                       (df['low'] >= avwap * (1 - tol)) & \
                       (df['close'] > avwap) & \
                       (df['close'] > df['open'])  # vela alcista

    # Toca AVWAP desde arriba (resistencia) → short
    touch_from_above = (df['high'] >= avwap * (1 - tol)) & \
                       (df['high'] <= avwap * (1 + tol)) & \
                       (df['close'] < avwap) & \
                       (df['close'] < df['open'])  # vela bajista

    # Filtros
    htf_l  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s  = df.get('htf1_short', pd.Series(False, index=df.index))
    vol_ok = df.get('vol_ok', pd.Series(True, index=df.index))
    base   = vol_ok & ~df.get('is_spike', pd.Series(False, index=df.index))

    sig[touch_from_below & htf_l & base]  = 1
    sig[touch_from_above & htf_s & base]  = -1
    return _apply_cooldown(sig, cfg.get('cooldown', 6))


def sig_volatility_breakout(df, cfg):
    """
    Volatility breakout: esperar compresion (ATR bajo) y entrar en la expansion.
    El squeeze de Bollinger / Keltner es el setup clasico.
    """
    sig = pd.Series(0, index=df.index)

    sma20  = df['close'].rolling(20).mean()
    std20  = df['close'].rolling(20).std()
    bb_u   = sma20 + 2 * std20
    bb_l   = sma20 - 2 * std20
    bb_w   = (bb_u - bb_l) / sma20  # BB width normalizado

    # Compression: BB width en percentil bajo
    bb_pct = bb_w.rolling(100, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )

    # Squeeze: BB width < percentil 25 (comprimido)
    squeezed = bb_pct < 0.25

    # Breakout: precio sale del BB con fuerza
    bull_bo = (df['close'] > bb_u) & squeezed.shift(cfg.get('squeeze_bars', 3)) & \
              (df['close'] > df['open']) & df.get('vol_ok', pd.Series(True, index=df.index))
    bear_bo = (df['close'] < bb_l) & squeezed.shift(cfg.get('squeeze_bars', 3)) & \
              (df['close'] < df['open']) & df.get('vol_ok', pd.Series(True, index=df.index))

    # HTF confirma
    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))

    sig[bull_bo & htf_l] = 1
    sig[bear_bo & htf_s] = -1
    return _apply_cooldown(sig, cfg.get('cooldown', 10))


def sig_market_structure(df, cfg):
    """
    ICT Market Structure puro: BOS (Break of Structure) + ChoCH.
    Solo entra en la primera vela despues de confirmar BOS.
    """
    sig  = pd.Series(0, index=df.index)
    sw   = cfg.get('swing_len', 10)
    c, h, l = df['close'], df['high'], df['low']

    # Swing highs / lows
    swing_h = h.rolling(sw).max().shift(sw)
    swing_l = l.rolling(sw).min().shift(sw)

    bull = df.get('bull', c > df['ema50'])
    bear = df.get('bear', c < df['ema50'])

    # BOS alcista: precio rompe swing high previo en tendencia bull
    bos_bull = (c > swing_h) & (c.shift(1) <= swing_h.shift(1)) & bull
    # BOS bajista: precio rompe swing low previo en tendencia bear
    bos_bear = (c < swing_l) & (c.shift(1) >= swing_l.shift(1)) & bear

    # ChoCH: cambio de estructura (señal de reversal)
    choch_to_bull = (c > swing_h) & bear  # precio rompe alto en tendencia bajista → reversal
    choch_to_bear = (c < swing_l) & bull  # precio rompe bajo en tendencia alcista → reversal

    htf_l  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s  = df.get('htf1_short', pd.Series(False, index=df.index))
    base   = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
              df.get('gap_ok', pd.Series(True, index=df.index))

    use_choch = cfg.get('use_choch', False)
    if use_choch:
        sig[(bos_bull | choch_to_bull) & htf_l & base]  = 1
        sig[(bos_bear | choch_to_bear) & htf_s & base]  = -1
    else:
        sig[bos_bull & htf_l & base]  = 1
        sig[bos_bear & htf_s & base]  = -1

    return _apply_cooldown(sig, cfg.get('cooldown', 8))


def sig_ob_fvg_pure(df, cfg):
    """
    ICT Order Blocks + FVG puro.
    Entra cuando el precio regresa a un OB o llena un FVG.
    Sin filtros de tendencia — confía solo en la estructura ICT.
    """
    sig = pd.Series(0, index=df.index)

    htf_l  = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s  = df.get('htf1_short', pd.Series(False, index=df.index))
    atr    = df['atr']

    # OB: precio regresa al order block
    if 'in_bull_ob' in df.columns:
        ob_long  = df['in_bull_ob']  & (df['close'] > df['open'])  # vela alcista en OB
        ob_short = df['in_bear_ob']  & (df['close'] < df['open'])  # vela bajista en OB
        sig[ob_long  & htf_l] = 1
        sig[ob_short & htf_s] = -1

    # FVG: precio llena el gap y rebota
    if 'fill_bull_fvg' in df.columns:
        fvg_long  = df['fill_bull_fvg'] & (df['close'] > df['open'])
        fvg_short = df['fill_bear_fvg'] & (df['close'] < df['open'])
        sig[fvg_long  & htf_l] = 1
        sig[fvg_short & htf_s] = -1

    base = ~df.get('fake_move', pd.Series(False, index=df.index))
    sig  = sig * base.astype(int)

    return _apply_cooldown(sig, cfg.get('cooldown', 6))


def sig_multi_tf_momentum(df, cfg):
    """
    Momentum alineado en 3 timeframes usando proxies de barras.
    15m: momentum actual
    1h aprox: momentum de 4 velas
    4h aprox: momentum de 16 velas
    Entra solo cuando los 3 apuntan en la misma direccion.
    """
    sig = pd.Series(0, index=df.index)
    c   = df['close']

    # Momentum en distintos horizontes
    mom_1  = (c - c.shift(3))  / c.shift(3).replace(0, np.nan)   # ~45min
    mom_4  = (c - c.shift(12)) / c.shift(12).replace(0, np.nan)  # ~3h
    mom_16 = (c - c.shift(48)) / c.shift(48).replace(0, np.nan)  # ~12h

    threshold = cfg.get('mom_threshold', 0.002)  # 0.2% minimo

    all_bull = (mom_1 > threshold) & (mom_4 > threshold) & (mom_16 > threshold)
    all_bear = (mom_1 < -threshold) & (mom_4 < -threshold) & (mom_16 < -threshold)

    # Cruce: primer momento en que los 3 se alinean
    bull_cross = all_bull & ~all_bull.shift(1).fillna(False)
    bear_cross = all_bear & ~all_bear.shift(1).fillna(False)

    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok', pd.Series(True, index=df.index))

    sig[bull_cross & base] = 1
    sig[bear_cross & base] = -1
    return _apply_cooldown(sig, cfg.get('cooldown', 8))


# ─── HELPER ───────────────────────────────────────────────────────────────────
def _apply_cooldown(sig, bars):
    final = pd.Series(0, index=sig.index)
    last  = -bars - 1
    for i in range(len(sig)):
        if sig.iloc[i] != 0 and (i - last) >= bars:
            final.iloc[i] = sig.iloc[i]
            last = i
    return final


# ─── BACKTEST RAPIDO ──────────────────────────────────────────────────────────
def quick_backtest(df, signals, sl_mult=1.5, tp_mult=2.5, risk=0.5):
    cap = CAPITAL; eq = [cap]; pos = 0
    entry = sl = tp = size = 0.0; trades = []
    for i in range(1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i-1]
        sig = signals.iloc[i-1]
        pr  = row['close']; atr = prev['atr']
        h_  = row['high'];  lo  = row['low']
        if pos != 0:
            pnl    = 0.0; closed = False; reason = ''
            if pos == 1:
                if lo <= sl: pnl = size*(sl-entry) - size*(entry+sl)*COST; closed=True; reason='SL'
                elif h_ >= tp: pnl = size*(tp-entry) - size*(entry+tp)*COST; closed=True; reason='TP'
            else:
                if h_ >= sl: pnl = size*(entry-sl) - size*(entry+sl)*COST; closed=True; reason='SL'
                elif lo <= tp: pnl = size*(entry-tp) - size*(entry+tp)*COST; closed=True; reason='TP'
            if not closed and sig == -pos:
                pnl = size*(pr-entry)*pos - size*(entry+pr)*COST; closed=True; reason='Sig'
            if closed:
                cap += pnl
                trades.append({'pnl':pnl,'won':pnl>0,'reason':reason})
                pos = 0
        if pos == 0 and sig != 0 and cap > 50:
            pos=sig; entry=pr
            r_sl=atr*sl_mult; sl=entry-r_sl if pos==1 else entry+r_sl
            tp=entry+atr*tp_mult if pos==1 else entry-atr*tp_mult
            size=(cap*risk/100)/r_sl if r_sl>0 else 0
        eq.append(cap)
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    if df_t.empty or len(df_t) < 5:
        return {'trades':0,'winrate':0,'pnl_pct':-999,'sharpe':-99,'max_dd':-100,'pf':0}
    w  = df_t[df_t['pnl']>0]; l = df_t[df_t['pnl']<=0]
    gp = w['pnl'].sum(); gl = abs(l['pnl'].sum())
    peak = eq_s.cummax(); dd = (eq_s-peak)/peak*100
    ret  = eq_s.pct_change().dropna()
    sh   = ret.mean()/ret.std()*np.sqrt(35040) if ret.std()>0 else 0
    pnl  = (eq_s.iloc[-1]-CAPITAL)/CAPITAL*100
    days = (eq_s.index[-1]-eq_s.index[0]).days
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    return {'trades':len(df_t),'winrate':len(w)/len(df_t)*100,
            'pnl_pct':pnl,'cagr':cagr,'sharpe':sh,
            'max_dd':dd.min(),'pf':gp/gl if gl>0 else 999}


# ─── SEARCH ───────────────────────────────────────────────────────────────────
STRATEGY_SPACE = {
    'Price Action': (sig_price_action, {
        'cooldown': [4,6,8,12],
    }),
    'CVD Momentum': (sig_cvd_momentum, {
        'lookback': [10,15,20,30],
        'cooldown': [6,8,12,16],
    }),
    'VWAP Bounce': (sig_vwap_bounce, {
        'tolerance': [0.002,0.003,0.005,0.007],
        'cooldown':  [4,6,8],
    }),
    'Volatility Breakout': (sig_volatility_breakout, {
        'squeeze_bars': [2,3,5],
        'cooldown':     [8,12,16],
    }),
    'Market Structure': (sig_market_structure, {
        'swing_len':  [8,10,14,20],
        'use_choch':  [True, False],
        'cooldown':   [6,8,12],
    }),
    'OB FVG Pure': (sig_ob_fvg_pure, {
        'cooldown': [4,6,8],
    }),
    'Multi-TF Momentum': (sig_multi_tf_momentum, {
        'mom_threshold': [0.001,0.002,0.003,0.005],
        'cooldown':      [6,8,12],
    }),
}

SL_RANGE = [1.0,1.3,1.5,1.7,2.0,2.3]
TP_RANGE = [1.5,2.0,2.5,3.0,3.5,4.0,4.5]
N_SAMPLES = 2000


def run_new_strategy_search(tf='15m', n_samples=N_SAMPLES):
    import sys; sys.path.insert(0, str(OUTPUT_DIR/'engine'))
    import subprocess, winsound
    from core.data import fetch_ohlcv
    from core.features import build_features

    print(f"\n{'='*65}")
    print(f"  NEW STRATEGY SEARCH — {tf.upper()}")
    print(f"  Estrategias: {list(STRATEGY_SPACE.keys())}")
    print(f"  {n_samples} muestras por estrategia")
    print(f"{'='*65}")

    TF_MAP = {'15m':('1h','4h',180),'1h':('4h','1d',365),'5m':('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))
    df_b  = fetch_ohlcv(tf=tf,   days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)
    print(f"  {len(df)} velas listas\n")

    all_results = []
    best_global = {'pnl_pct': -999, 'winrate': 0}
    best_name   = ''
    best_cfg    = {}

    for strat_name, (fn, param_space) in STRATEGY_SPACE.items():
        print(f"  Testeando: {strat_name}...")
        best_strat = {'pnl_pct': -999}

        for _ in range(n_samples // len(STRATEGY_SPACE)):
            # Muestra aleatoria de parametros
            cfg = {k: random.choice(v) for k, v in param_space.items()}
            sl  = random.choice(SL_RANGE)
            tp  = random.choice(TP_RANGE)
            if tp <= sl * 0.8: continue

            try:
                sig = fn(df, cfg)
                if (sig != 0).sum() < 20: continue
                m = quick_backtest(df, sig, sl_mult=sl, tp_mult=tp)
                m['strategy'] = strat_name
                m['cfg']      = cfg
                m['sl']       = sl
                m['tp']       = tp

                if m['winrate'] >= 55 and m['pnl_pct'] > 0 and m['trades'] >= 30:
                    all_results.append(m.copy())

                if m.get('cagr', m['pnl_pct']) > best_strat.get('cagr', best_strat['pnl_pct']):
                    best_strat = m.copy()

                if m.get('cagr', m['pnl_pct']) > best_global.get('cagr', best_global['pnl_pct']):
                    best_global = m.copy()
                    best_name   = strat_name
                    best_cfg    = cfg.copy()
                    print(f"  *** NUEVO MEJOR ({strat_name}) ***")
                    print(f"  {m['trades']}T | WR {m['winrate']:.1f}% | "
                          f"CAGR {m.get('cagr',m['pnl_pct']):+.1f}%/año | "
                          f"PF {m['pf']:.2f} | DD {m['max_dd']:.1f}%")
                    print(f"  SL={sl:.1f}x | TP={tp:.1f}x | Params={cfg}")
            except Exception:
                continue

        if best_strat['pnl_pct'] > -999:
            print(f"  {strat_name}: CAGR {best_strat.get('cagr',best_strat['pnl_pct']):+.1f}%/año | "
                  f"WR {best_strat['winrate']:.1f}% | Trades {best_strat['trades']}")

    # Resultados
    all_results.sort(key=lambda x: x.get('cagr', x['pnl_pct']), reverse=True)

    print(f"\n{'='*65}")
    print(f"  RESULTADO NEW STRATEGY SEARCH — {tf.upper()}")
    print(f"{'='*65}")
    print(f"  Configs positivas (WR>55%, PnL>0, 30+T): {len(all_results)}")
    if all_results:
        print(f"\n  TOP 5:")
        for i, m in enumerate(all_results[:5], 1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | "
                  f"WR {m['winrate']:.1f}% | CAGR {m.get('cagr',m['pnl_pct']):+.1f}%/año | "
                  f"PF {m['pf']:.2f} | DD {m['max_dd']:.1f}%")

    if best_global.get('pnl_pct', -999) > -999:
        print(f"\n  MEJOR GLOBAL: {best_name}")
        print(f"  {best_global['trades']}T | WR {best_global['winrate']:.1f}% | "
              f"CAGR {best_global.get('cagr',best_global['pnl_pct']):+.1f}%/año | "
              f"PF {best_global['pf']:.2f} | DD {best_global['max_dd']:.1f}%")

        # Guardar mejor
        model_dir = OUTPUT_DIR / 'models' / tf
        model_dir.mkdir(parents=True, exist_ok=True)
        with open(model_dir / 'new_strategy.json', 'w') as f:
            json.dump({
                'strategy': best_name,
                'params': best_cfg,
                'sl_mult': best_global['sl'],
                'tp_mult': best_global['tp'],
                'metrics': {k: round(v,4) if isinstance(v,float) else v
                            for k,v in best_global.items() if k not in ('cfg',)},
            }, f, indent=2)
        print(f"  [SAVED] models/{tf}/new_strategy.json")

    # CSV completo
    if all_results:
        pd.DataFrame(all_results).to_csv(
            OUTPUT_DIR/'results'/'reports'/f'new_strategies_{tf}.csv', index=False
        )
        print(f"  [CSV] new_strategies_{tf}.csv")

    print(f"\n[DONE] New strategy search completado.")

    try:
        for _ in range(4): winsound.Beep(1200,300)
        n = len(all_results)
        m = best_global
        msg=(f"NEW STRATEGY SEARCH {tf.upper()} LISTO!\\n\\n"
             f"Configs positivas: {n}\\n\\n"
             f"MEJOR: {best_name}\\n"
             f"Trades: {m.get('trades',0)} | WR: {m.get('winrate',0):.1f}%\\n"
             f"CAGR: {m.get('cagr',m.get('pnl_pct',0)):+.1f}%/año | PF: {m.get('pf',0):.2f}")
        subprocess.Popen(['powershell','-WindowStyle','Hidden','-Command',
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","New Strategies","OK","Information")'])
    except: pass

    return all_results, best_global


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf', default='15m')
    parser.add_argument('--samples', type=int, default=N_SAMPLES)
    args = parser.parse_args()
    run_new_strategy_search(args.tf, args.samples)
