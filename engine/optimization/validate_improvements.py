"""
SIGMA ENGINE — Validacion de mejoras estructurales 1H

Compara baseline vs 5 mejoras:
1. Trailing stop tras TP1 (en vez de TP2 fijo)
2. SL/TP adaptativo por regimen (TREND / RANGE / VOLATILE)
3. HTF scoring 0/1/2 (en vez de AND binario)
4. Sesgo largo BTC (shorts requieren score 2, longs score 1)
5. Tiempo maximo de posicion (48 barras = 2 dias)
+ Bonus: Lunes y Viernes incluidos
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004
SLIPPAGE   = 0.0001
COST       = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0
BARS_YEAR  = 8760  # 1H


def load_1h_data():
    from core.data import fetch_ohlcv
    from core.features import build_features

    max_p = OUTPUT_DIR / "models" / "data_1h_max.csv"
    if max_p.exists():
        df_b = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df_b.index.name = "timestamp"
    else:
        df_b = fetch_ohlcv(tf="1h", days=1095)

    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close", "atr", "ema50"], inplace=True)
    print(f"  {len(df):,} velas | {(df.index[-1]-df.index[0]).days} dias")
    return df


def get_signals(df, cfg):
    """Genera señales con la configuracion dada."""
    from core.signals import get_signals as _gs
    return _gs(df, cfg)


def calc_hurst(close, n=50):
    rn  = close.rolling(n).apply(lambda x: x.max()-x.min(), raw=True)
    rn2 = close.rolling(n//2).apply(lambda x: x.max()-x.min(), raw=True)
    h   = np.where(rn2 > 0, np.log(rn/rn2.clip(1e-6)) / np.log(2), 0.5)
    return pd.Series(h, index=close.index)


def get_regime(df):
    """Detecta regimen: TREND_BULL / TREND_BEAR / RANGE / VOLATILE."""
    adx   = df.get('adx', pd.Series(20, index=df.index))
    hurst = calc_hurst(df['close'])
    ema50 = df['close'].ewm(span=50, adjust=False).mean()
    ema200= df['close'].ewm(span=200, adjust=False).mean()
    atr   = df['atr']
    atr_ratio = atr / atr.rolling(50).mean().clip(1e-6)

    volatile   = atr_ratio > 1.8
    trending   = (hurst > 0.57) & (adx > 25)
    bull_trend = trending & (ema50 > ema200)
    bear_trend = trending & (ema50 < ema200)
    ranging    = (~trending) & (~volatile) & (adx < 22)

    regime = pd.Series('TRANSITION', index=df.index)
    regime[volatile]   = 'VOLATILE'
    regime[ranging]    = 'RANGE'
    regime[bear_trend] = 'TREND_BEAR'
    regime[bull_trend] = 'TREND_BULL'
    return regime


# ─── BACKTEST BASELINE ────────────────────────────────────────────────────────

def backtest_baseline(df, signals, quality, cfg):
    """Backtest original: SL/TP fijos, HTF binario, sin trailing."""
    closes = df['close'].to_numpy()
    highs  = df['high'].to_numpy()
    lows   = df['low'].to_numpy()
    atrs   = df['atr'].to_numpy()
    sigs   = signals.to_numpy()

    # Params desde config
    e_sl = cfg.get('elite_sl_mult', 2.4)
    e_tp = cfg.get('elite_tp_mult', 2.0)
    x_sl = cfg.get('exec_sl_mult',  1.9)
    x_tp = cfg.get('exec_tp_mult',  3.5)
    risk = cfg.get('risk_pct', 1.5)
    q65  = cfg.get('qty_tp1', 0.65)

    qual_arr = quality.to_numpy() if hasattr(quality, 'to_numpy') else np.zeros(len(sigs))

    cap = CAPITAL; eq = [cap]
    pos = 0; entry = sl = tp1 = tp2 = sz = sz2 = 0.0
    trades = []; tp1_done = False

    for i in range(1, len(closes)):
        pr = closes[i]; atr = atrs[i-1]; h_ = highs[i]; lo = lows[i]; s = sigs[i-1]

        if pos != 0:
            closed = False; pnl = 0.0
            if pos == 1:
                if lo <= sl:
                    pnl = sz*(sl-entry) + sz2*(sl-entry)
                    pnl -= (sz+sz2)*(entry+sl)*COST; closed = True; tag='SL'
                elif h_ >= tp1 and not tp1_done:
                    pnl_tp1 = sz*(tp1-entry) - sz*(entry+tp1)*COST
                    cap += pnl_tp1; trades.append({'pnl':pnl_tp1,'won':pnl_tp1>0,'tag':'TP1'}); sz=0; tp1_done=True
                elif h_ >= tp2:
                    pnl = sz2*(tp2-entry) - sz2*(entry+tp2)*COST; closed=True; tag='TP2'
            else:
                if h_ >= sl:
                    pnl = sz*(entry-sl) + sz2*(entry-sl)
                    pnl -= (sz+sz2)*(entry+sl)*COST; closed=True; tag='SL'
                elif lo <= tp1 and not tp1_done:
                    pnl_tp1 = sz*(entry-tp1) - sz*(entry+tp1)*COST
                    cap += pnl_tp1; trades.append({'pnl':pnl_tp1,'won':pnl_tp1>0,'tag':'TP1'}); sz=0; tp1_done=True
                elif lo <= tp2:
                    pnl = sz2*(entry-tp2) - sz2*(entry+tp2)*COST; closed=True; tag='TP2'
            if not closed and s == -pos:
                rem = sz + sz2
                pnl = pos*rem*(pr-entry) - rem*(entry+pr)*COST; closed=True; tag='REV'
            if closed:
                cap += pnl; trades.append({'pnl':pnl,'won':pnl>0,'tag':tag}); pos=0

        if pos == 0 and s != 0 and cap > 50:
            is_el = qual_arr[i-1] >= 2
            sl_m = e_sl if is_el else x_sl
            tp_m = e_tp if is_el else x_tp
            pos = s; entry = pr; r_sl = atr * sl_m
            sl  = entry - r_sl if pos==1 else entry + r_sl
            tp1 = entry + atr*tp_m if pos==1 else entry - atr*tp_m
            tp2 = entry + atr*tp_m*1.5 if pos==1 else entry - atr*tp_m*1.5
            total_sz = (cap * risk/100) / r_sl if r_sl > 0 else 0
            sz  = total_sz * q65
            sz2 = total_sz * (1-q65)
            tp1_done = False
        eq.append(cap)

    return _calc_metrics(trades, eq, df)


# ─── BACKTEST MEJORADO ────────────────────────────────────────────────────────

def backtest_improved(df, signals, quality, cfg):
    """
    Backtest con las 5 mejoras estructurales:
    1. Trailing stop tras TP1
    2. SL/TP adaptativo por regimen
    3. HTF scoring ya aplicado en signals (ver filtros)
    4. Long bias: shorts solo con score 2
    5. Max hold: 48 barras
    """
    closes  = df['close'].to_numpy()
    highs   = df['high'].to_numpy()
    lows    = df['low'].to_numpy()
    atrs    = df['atr'].to_numpy()
    sigs    = signals.to_numpy()
    regimes = get_regime(df).to_numpy()
    qual_arr= quality.to_numpy() if hasattr(quality, 'to_numpy') else np.zeros(len(sigs))

    # Multiplicadores por regimen
    REGIME_MULT = {
        'TREND_BULL': (1.2, 1.6),   # (sl_mult, tp_mult) — dejar correr
        'TREND_BEAR': (1.2, 1.6),
        'RANGE':      (0.8, 0.7),   # salir rapido
        'VOLATILE':   (1.5, 0.8),   # SL amplio, TP conservador
        'TRANSITION': (1.0, 1.0),
    }

    e_sl = cfg.get('elite_sl_mult', 2.4)
    e_tp = cfg.get('elite_tp_mult', 2.0)
    x_sl = cfg.get('exec_sl_mult',  1.9)
    x_tp = cfg.get('exec_tp_mult',  3.5)
    risk = cfg.get('risk_pct', 1.5)
    q65  = cfg.get('qty_tp1', 0.65)
    MAX_HOLD = 48

    cap = CAPITAL; eq = [cap]
    pos = 0; entry = sl = tp1 = sz = sz2 = 0.0
    trail = 0.0; tp1_done = False; bars_held = 0
    trades = []

    for i in range(1, len(closes)):
        pr = closes[i]; atr = atrs[i-1]; h_ = highs[i]; lo = lows[i]
        s  = sigs[i-1]; reg = regimes[i-1]

        # Mejora 4: Long bias — bloquear shorts con score bajo
        if s == -1 and qual_arr[i-1] < 2:
            s = 0

        if pos != 0:
            bars_held += 1
            closed = False; pnl = 0.0; tag = ''

            # Mejora 1: trailing stop activo tras TP1
            if tp1_done:
                if pos == 1:
                    trail = max(trail, pr - atr * 1.5)
                    if lo <= trail:
                        rem = sz2
                        pnl = rem*(trail-entry) - rem*(entry+trail)*COST
                        closed=True; tag='TRAIL'
                else:
                    trail = min(trail, pr + atr * 1.5)
                    if h_ >= trail:
                        rem = sz2
                        pnl = rem*(entry-trail) - rem*(entry+trail)*COST
                        closed=True; tag='TRAIL'
            else:
                # SL normal antes de TP1
                if pos == 1:
                    if lo <= sl:
                        rem = sz + sz2
                        pnl = rem*(sl-entry) - rem*(entry+sl)*COST; closed=True; tag='SL'
                    elif h_ >= tp1:
                        pnl_tp1 = sz*(tp1-entry) - sz*(entry+tp1)*COST
                        cap += pnl_tp1; trades.append({'pnl':pnl_tp1,'won':pnl_tp1>0,'tag':'TP1'})
                        sz = 0; tp1_done = True
                        trail = pr - atr * 1.5  # iniciar trailing
                else:
                    if h_ >= sl:
                        rem = sz + sz2
                        pnl = rem*(entry-sl) - rem*(entry+sl)*COST; closed=True; tag='SL'
                    elif lo <= tp1:
                        pnl_tp1 = sz*(entry-tp1) - sz*(entry+tp1)*COST
                        cap += pnl_tp1; trades.append({'pnl':pnl_tp1,'won':pnl_tp1>0,'tag':'TP1'})
                        sz = 0; tp1_done = True
                        trail = pr + atr * 1.5

            # Mejora 5: max hold time
            if not closed and bars_held >= MAX_HOLD:
                rem = sz + sz2
                pnl = pos*rem*(pr-entry) - rem*(entry+pr)*COST; closed=True; tag='MAXHOLD'

            # Reversal
            if not closed and s == -pos:
                rem = sz + sz2
                pnl = pos*rem*(pr-entry) - rem*(entry+pr)*COST; closed=True; tag='REV'

            if closed:
                cap += pnl; trades.append({'pnl':pnl,'won':pnl>0,'tag':tag}); pos=0; bars_held=0

        if pos == 0 and s != 0 and cap > 50:
            is_el = qual_arr[i-1] >= 2
            base_sl = e_sl if is_el else x_sl
            base_tp = e_tp if is_el else x_tp

            # Mejora 2: multiplicadores por regimen
            r_sl_m, r_tp_m = REGIME_MULT.get(reg, (1.0, 1.0))
            sl_m = base_sl * r_sl_m
            tp_m = base_tp * r_tp_m

            pos = s; entry = pr; r_sl = atr * sl_m
            sl  = entry - r_sl if pos==1 else entry + r_sl
            tp1 = entry + atr*tp_m if pos==1 else entry - atr*tp_m
            total_sz = (cap * risk/100) / r_sl if r_sl > 0 else 0
            sz  = total_sz * q65
            sz2 = total_sz * (1-q65)
            tp1_done = False; trail = sl; bars_held = 0
        eq.append(cap)

    return _calc_metrics(trades, eq, df)


def _calc_metrics(trades, eq, df):
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    if df_t.empty or len(df_t) < 10:
        return None

    w  = df_t[df_t['pnl'] > 0]; l = df_t[df_t['pnl'] <= 0]
    gp = w['pnl'].sum(); gl = abs(l['pnl'].sum())
    peak = eq_s.cummax(); dd = (eq_s - peak) / peak * 100
    ret  = eq_s.pct_change().dropna()
    days = (eq_s.index[-1] - eq_s.index[0]).days
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr   = len(w)/len(df_t)
    sh   = ret.mean()/ret.std()*np.sqrt(BARS_YEAR) if ret.std()>0 else 0
    calmar = cagr/abs(dd.min()) if dd.min()<0 else 0

    # breakdown por tag
    tags = df_t.groupby('tag')['pnl'].count().to_dict() if 'tag' in df_t else {}

    return {
        'trades':  len(df_t),
        'winrate': round(wr*100, 1),
        'cagr':    round(cagr, 2),
        'max_dd':  round(dd.min(), 2),
        'pf':      round(gp/gl, 3) if gl>0 else 999,
        'sharpe':  round(sh, 3),
        'calmar':  round(calmar, 3),
        'final':   round(eq_s.iloc[-1], 2),
        'tags':    tags,
    }


def compare(m_base, m_impr, label):
    if not m_base or not m_impr:
        return
    delta_cagr = m_impr['cagr'] - m_base['cagr']
    delta_wr   = m_impr['winrate'] - m_base['winrate']
    delta_dd   = m_impr['max_dd'] - m_base['max_dd']
    delta_t    = m_impr['trades'] - m_base['trades']

    sign = lambda x: '+' if x >= 0 else ''
    print(f"\n  {label}")
    print(f"  {'Metrica':<12} {'Baseline':>10} {'Mejorado':>10} {'Delta':>10}")
    print(f"  {'-'*44}")
    print(f"  {'CAGR':<12} {m_base['cagr']:>+9.1f}% {m_impr['cagr']:>+9.1f}% {sign(delta_cagr)}{delta_cagr:>8.1f}%")
    print(f"  {'WR':<12} {m_base['winrate']:>9.1f}% {m_impr['winrate']:>9.1f}% {sign(delta_wr)}{delta_wr:>8.1f}%")
    print(f"  {'MaxDD':<12} {m_base['max_dd']:>9.1f}% {m_impr['max_dd']:>9.1f}% {sign(delta_dd)}{delta_dd:>8.1f}%")
    print(f"  {'Trades':<12} {m_base['trades']:>10} {m_impr['trades']:>10} {sign(delta_t)}{delta_t:>9}")
    print(f"  {'PF':<12} {m_base['pf']:>10.3f} {m_impr['pf']:>10.3f}")
    print(f"  {'Sharpe':<12} {m_base['sharpe']:>10.3f} {m_impr['sharpe']:>10.3f}")
    print(f"  {'Calmar':<12} {m_base['calmar']:>10.3f} {m_impr['calmar']:>10.3f}")
    print(f"  {'Capital':<12} ${m_base['final']:>9.2f} ${m_impr['final']:>9.2f}")
    if m_impr.get('tags'):
        print(f"\n  Cierres mejorado: {m_impr['tags']}")


def run():
    print("\n" + "="*65)
    print("  SIGMA — VALIDACION DE MEJORAS ESTRUCTURALES 1H")
    print("="*65)

    # Cargar datos
    print("\n[DATA] Cargando 1H...")
    df = load_1h_data()

    # Cargar config del mejor modelo
    cfg_path = OUTPUT_DIR / "models" / "1h" / "config.json"
    with open(cfg_path) as f:
        cfg_raw = json.load(f)
    cfg = cfg_raw.get('params', cfg_raw)
    print(f"  Config: {cfg_path.name} cargada")

    # Generar señales y calidad
    print("\n[SIGNALS] Generando señales...")
    from core.signals import get_signals
    signals, quality = get_signals(df, cfg)
    n_sigs = (signals != 0).sum()
    print(f"  {n_sigs} señales generadas")

    # Mejora 3: HTF scoring — re-pesar calidad
    # quality >= 2 = ELITE (ambos HTF alineados)
    # quality == 1 = EXECUTE (1 HTF alineado)
    # quality == 0 = bloqueado

    # Convertir quality string → numerico (ELITE_ICT=3, ELITE=2, EXECUTE=1)
    def q_to_num(q):
        if hasattr(q, 'map'):
            return q.map({'ELITE_ICT': 3, 'ELITE': 2, 'EXECUTE': 1}).fillna(0).astype(int)
        return pd.Series(0, index=df.index)
    quality = q_to_num(quality)

    # IS / OOS split (80/20)
    split  = int(len(df) * 0.80)
    df_is  = df.iloc[:split]
    sig_is = signals.iloc[:split]
    q_is   = quality.iloc[:split]

    df_oos  = df.iloc[split:]
    sig_oos = signals.iloc[split:]
    q_oos   = quality.iloc[split:]

    d_is  = (df_is.index[-1]-df_is.index[0]).days
    d_oos = (df_oos.index[-1]-df_oos.index[0]).days
    print(f"  IS: {d_is}d | OOS: {d_oos}d")

    print("\n[BACKTEST] Corriendo baseline vs mejorado...")

    # IS
    print("\n--- IN-SAMPLE ---")
    m_base_is = backtest_baseline(df_is, sig_is, q_is, cfg)
    m_impr_is = backtest_improved(df_is, sig_is, q_is, cfg)
    compare(m_base_is, m_impr_is, "IS (entrenamiento)")

    # OOS
    print("\n--- OUT-OF-SAMPLE ---")
    m_base_oos = backtest_baseline(df_oos, sig_oos, q_oos, cfg)
    m_impr_oos = backtest_improved(df_oos, sig_oos, q_oos, cfg)
    compare(m_base_oos, m_impr_oos, "OOS (validacion real)")

    # Guardar resultados
    results = {
        'baseline_is':  m_base_is,
        'improved_is':  m_impr_is,
        'baseline_oos': m_base_oos,
        'improved_oos': m_impr_oos,
    }
    out = OUTPUT_DIR / "results" / "reports" / "improvements_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n[SAVED] {out}")

    print("\n" + "="*65)
    print("  VEREDICTO FINAL")
    print("="*65)
    if m_impr_oos and m_base_oos:
        delta = m_impr_oos['cagr'] - m_base_oos['cagr']
        if delta > 0:
            print(f"  MEJORA CONFIRMADA en OOS: {delta:+.1f}% CAGR")
            print(f"  Aplicar mejoras al Pine Script.")
        else:
            print(f"  Sin mejora en OOS ({delta:+.1f}%). Revisar parametros.")

    return results


if __name__ == '__main__':
    run()
