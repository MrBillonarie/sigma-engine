"""
SIGMA 2H TIMEFRAME OPTIMIZER
El slot vacio entre 1H (56T/ano) y 4H (9T/ano).
Objetivo: ~25-35 trades/ano con calidad similar al 1H.

Por que 2H?
  - 1H: mucho ruido, 56T/ano pero WR 55.8% OOS
  - 4H: muy pocos trades, 9T/ano
  - 2H: equilibrio esperado: menos ruido que 1H, mas trades que 4H

Estrategias probadas: breakout, pullback, momentum, TMA bands

Genera: models/2h/best_validated.json
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
CAPITAL     = 1000.0


def load_data_2h():
    """Descarga 1H y resamplea a 2H."""
    from core.data import fetch_ohlcv
    df_1h = fetch_ohlcv(tf='1h', days=3200)
    df_1d = fetch_ohlcv(tf='1d', days=3200)

    # Resamplear a 2H (pandas 2.x usa 'h' minuscula)
    df_2h = df_1h.resample('2h').agg({
        'open':   'first',
        'high':   'max',
        'low':    'min',
        'close':  'last',
        'volume': 'sum',
    }).dropna()

    # ATR en 2H
    h = df_2h['high']; l = df_2h['low']; c = df_2h['close']
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    df_2h['atr']   = tr.ewm(alpha=1/14, adjust=False).mean()
    df_2h['ema50'] = c.ewm(span=50, adjust=False).mean()
    df_2h['ema200']= c.ewm(span=200, adjust=False).mean()

    # RSI semanal desde 1D
    df_1d = df_1d[df_1d.index >= df_2h.index[0]]
    close_w = df_1d['close'].resample('W').last().ffill()
    d = close_w.diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    ll = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi_w = 100 - 100 / (1 + g / (ll + 1e-9))
    df_2h['rsi_w'] = rsi_w.reindex(df_2h.index, method='ffill').fillna(50)

    df_2h.dropna(subset=['atr', 'ema50'], inplace=True)
    return df_2h


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def generate_signals(df, params, strategy):
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))
    atr    = df['atr']
    ema200 = df['ema200']
    vol_ma = v.rolling(20).mean()

    lb   = params.get('lookback', 40)
    vm   = params.get('vol_mult', 2.0)
    slm  = params.get('sl_mult', 2.0)
    tpm  = params.get('tp_mult', 2.5)
    cd   = params.get('cooldown', 8)
    rsi_thr = params.get('rsi_w_thr', 55)

    bull_ok = df['rsi_w'] > rsi_thr
    above_200 = c > ema200
    below_200 = c < ema200
    vol_ok = v > vol_ma * vm

    if strategy == 'breakout':
        prev_high = h.rolling(lb).max().shift(1)
        bl = (c > prev_high) & vol_ok & above_200 & bull_ok
        bs = pd.Series(False, index=df.index)

    elif strategy == 'pullback':
        # Pullback a EMA en tendencia alcista
        ema20  = c.ewm(span=20, adjust=False).mean()
        ema50  = c.ewm(span=50, adjust=False).mean()
        rsi2h  = rsi(c, 14)
        # Long: precio toca EMA20 despues de estar por encima de EMA50
        at_ema = (c <= ema20 * 1.003) & (c >= ema20 * 0.997)
        trend  = ema50 > ema200
        oversold = rsi2h < params.get('rsi_entry', 45)
        bl = at_ema & trend & oversold & bull_ok
        bs = pd.Series(False, index=df.index)

    elif strategy == 'momentum':
        # MACD histogram positivo y creciente
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        signal= macd.ewm(span=9, adjust=False).mean()
        hist  = macd - signal
        rsi2h = rsi(c, 14)
        bl = (hist > 0) & (hist > hist.shift(1)) & above_200 & bull_ok & vol_ok & (rsi2h < 70)
        bs = pd.Series(False, index=df.index)

    elif strategy == 'tma':
        # TMA Bands: precio toca banda inferior en tendencia
        period = params.get('tma_period', 14)
        atr_mult = params.get('atr_mult', 1.5)
        sma = c.rolling(period).mean()
        sma2 = sma.rolling(period).mean()  # TMA = doble SMA
        upper = sma2 + atr * atr_mult
        lower = sma2 - atr * atr_mult
        bl = (c <= lower) & above_200 & bull_ok & vol_ok
        bs = (c >= upper) & below_200 & (~bull_ok) & vol_ok

    else:
        return pd.Series(0, index=df.index), pd.Series(0.0, index=df.index), pd.Series(0.0, index=df.index)

    sig = pd.Series(0, index=df.index)
    sl_s = pd.Series(0.0, index=df.index)
    tp_s = pd.Series(0.0, index=df.index)

    sig[bl] = 1
    sl_s[bl] = c[bl] - atr[bl] * slm
    tp_s[bl] = c[bl] + atr[bl] * tpm

    sig[bs] = -1
    sl_s[bs] = c[bs] + atr[bs] * slm
    tp_s[bs] = c[bs] - atr[bs] * tpm

    # Cooldown
    last = -cd - 1
    final = pd.Series(0, index=df.index)
    final_sl = sl_s.copy()
    final_tp = tp_s.copy()
    for i in range(lb, len(sig)):
        if (i - last) >= cd and sig.iloc[i] != 0:
            final.iloc[i] = sig.iloc[i]
            last = i

    return final, final_sl, final_tp


def backtest(df, signals, sl_s, tp_s, risk_pct=3.3):
    c  = df['close'].to_numpy()
    h  = df['high'].to_numpy()
    lo = df['low'].to_numpy()
    ent = signals.to_numpy()
    sl  = sl_s.to_numpy()
    tp  = tp_s.to_numpy()

    cap = CAPITAL; eq = [cap]; pos = 0
    entry_p = slv = tpv = size = 0.0
    trades = []

    for i in range(1, len(c)):
        pr = c[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if lo[i] <= slv:
                    pnl = size*(slv-entry_p) - size*(entry_p+slv)*COMMISSION; closed = True
                elif h[i] >= tpv:
                    pnl = size*(tpv-entry_p) - size*(entry_p+tpv)*COMMISSION; closed = True
            else:
                if h[i] >= slv:
                    pnl = size*(entry_p-slv) - size*(entry_p+slv)*COMMISSION; closed = True
                elif lo[i] <= tpv:
                    pnl = size*(entry_p-tpv) - size*(entry_p+tpv)*COMMISSION; closed = True
            if closed:
                cap += pnl; trades.append({'pnl': pnl, 'won': pnl > 0}); pos = 0

        if pos == 0 and ent[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size = (cap * risk_pct / 100) / rsl
            pos = int(ent[i-1]); entry_p = pr; slv = sl[i-1]; tpv = tp[i-1]
        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def metrics(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5: return None
    w = df_t[df_t['pnl'] > 0]; l = df_t[df_t['pnl'] <= 0]
    wr  = len(w)/len(df_t)*100
    pf  = w['pnl'].sum()/abs(l['pnl'].sum()) if not l.empty and l['pnl'].sum() != 0 else 999
    peak = eq_s.cummax(); dd = ((eq_s-peak)/peak*100).min()
    last = float(eq_s.iloc[-1])
    if last <= 0: return None
    cagr = ((last/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr != cagr: return None
    tpa  = len(df_t) / max(days/365.25, 0.1)
    return {'trades': len(df_t), 'wr': round(wr,1), 'cagr': round(cagr,1),
            'dd': round(dd,1), 'pf': round(pf,2), 'trades_year': round(tpa,1)}


def score(m, min_t=10):
    if m is None or m['trades'] < min_t or m['cagr'] <= 0: return -9999
    s_cagr = min(m['cagr'], 60) / 60 * 0.40
    s_wr   = max(m['wr']/100 - 0.48, 0) / 0.22 * 0.25
    s_cal  = min(m['cagr']/abs(m['dd']) if m['dd'] < 0 else 0, 4) / 4 * 0.20
    s_pf   = min(m['pf'], 3) / 3 * 0.15
    return s_cagr + s_wr + s_cal + s_pf


def run(n_trials=600):
    from core.database import save_run, init_db
    init_db()

    print('\n' + '='*65)
    print('  SIGMA 2H OPTIMIZER — Slot entre 1H y 4H')
    print(f'  {n_trials} trials | IS/OOS 80/20 | 8.7 anos')
    print('='*65)

    df = load_data_2h()
    n = len(df); split = int(n * 0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f'  IS:  {df_is.index[0].strftime("%Y-%m-%d")} -> {df_is.index[-1].strftime("%Y-%m-%d")} ({days_is}d)')
    print(f'  OOS: {df_oos.index[0].strftime("%Y-%m-%d")} -> {df_oos.index[-1].strftime("%Y-%m-%d")} ({days_oos}d)')
    print(f'  Total velas 2H: {len(df):,}\n')

    def objective(trial):
        strategy = trial.suggest_categorical('strategy', ['breakout', 'pullback', 'momentum', 'tma'])
        p = {
            'lookback':   trial.suggest_int('lookback',   20, 60),
            'vol_mult':   trial.suggest_float('vol_mult',  1.2, 3.5, step=0.1),
            'sl_mult':    trial.suggest_float('sl_mult',   1.5, 3.5, step=0.1),
            'tp_mult':    trial.suggest_float('tp_mult',   1.5, 4.0, step=0.1),
            'cooldown':   trial.suggest_int('cooldown',    4, 15),
            'rsi_w_thr':  trial.suggest_int('rsi_w_thr',  45, 65),
            'rsi_entry':  trial.suggest_int('rsi_entry',  35, 55),
            'tma_period': trial.suggest_int('tma_period', 10, 25),
            'atr_mult':   trial.suggest_float('atr_mult', 1.0, 2.5, step=0.1),
        }
        risk_pct = trial.suggest_float('risk_pct', 2.0, 5.0, step=0.1)
        try:
            sig, sl, tp = generate_signals(df_is, p, strategy)
            if (sig != 0).sum() < 20: return -9999
            dt, eq = backtest(df_is, sig, sl, tp, risk_pct)
            m = metrics(dt, eq, days_is)
            s = score(m, min_t=15)
            if m and m['trades'] >= 15:
                save_run('2h', f'breakout_{strategy}', p, m, s)
            return float(s) if s is not None else -9999
        except:
            return -9999

    study = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=100))

    best_s = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_s[0] and trial.value > 0.25:
            best_s[0] = trial.value
            p = trial.params.copy()
            strategy = p.pop('strategy', 'breakout')
            rp = p.pop('risk_pct', 3.3)
            try:
                sig, sl, tp = generate_signals(df_is, p, strategy)
                dt, eq = backtest(df_is, sig, sl, tp, rp)
                m = metrics(dt, eq, days_is)
                if m:
                    print(f'  [T{trial.number}] {strategy:10s} IS: {m["trades"]}T ({m["trades_year"]:.1f}T/ano) | '
                          f'WR {m["wr"]:.1f}% | CAGR {m["cagr"]:+.1f}% | score={trial.value:.4f}')
            except:
                pass

    print(f'  Corriendo {n_trials} trials...')
    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    bp = study.best_params.copy()
    best_strategy = bp.pop('strategy', 'breakout')
    rp = bp.pop('risk_pct', 3.3)

    sig_is, sl_is, tp_is = generate_signals(df_is, bp, best_strategy)
    dt_is, eq_is = backtest(df_is, sig_is, sl_is, tp_is, rp)
    m_is = metrics(dt_is, eq_is, days_is)

    sig_oos, sl_oos, tp_oos = generate_signals(df_oos, bp, best_strategy)
    dt_oos, eq_oos = backtest(df_oos, sig_oos, sl_oos, tp_oos, rp)
    m_oos = metrics(dt_oos, eq_oos, days_oos)

    print('\n' + '='*65)
    print(f'  RESULTADO 2H — Estrategia: {best_strategy.upper()}')
    print('='*65)
    if m_is:
        print(f'  IS:  {m_is["trades"]}T ({m_is["trades_year"]:.1f}T/ano) | WR {m_is["wr"]:.1f}% | CAGR {m_is["cagr"]:+.1f}% | DD {m_is["dd"]:.1f}%')
    if m_oos:
        print(f'  OOS: {m_oos["trades"]}T ({m_oos["trades_year"]:.1f}T/ano) | WR {m_oos["wr"]:.1f}% | CAGR {m_oos["cagr"]:+.1f}% | DD {m_oos["dd"]:.1f}%')
        if m_oos['cagr'] > 0:
            gap = abs(m_is['cagr'] - m_oos['cagr']) if m_is else 999
            print(f'  IS->OOS gap CAGR: {gap:.1f}pp')
            if gap < 30:
                print(f'  *** GENERALIZACION ACEPTABLE ***')
    print(f'  Params: lookback={bp.get("lookback")} vol={bp.get("vol_mult")} cd={bp.get("cooldown")}')

    out_dir = OUTPUT_DIR / 'models' / '2h'
    out_dir.mkdir(parents=True, exist_ok=True)

    if m_oos and m_oos.get('cagr', -999) > 0:
        result = {
            'tf': '2h', 'strategy': best_strategy,
            'params': bp, 'risk_pct': rp,
            'metrics_is': m_is, 'metrics_oos': m_oos,
        }
        out = out_dir / 'best_validated.json'
        with open(out, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        print(f'  [SAVED] models/2h/best_validated.json')
    else:
        print(f'  OOS negativo — no guardado')
    print('='*65)


if __name__ == '__main__':
    run()
