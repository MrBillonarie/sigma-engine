"""
SIGMA 1H BREAKOUT — Alta Frecuencia
Objetivo: mantener OOS +16.2% pero con 100-150 trades/año en vez de 56
Mas trades = confianza estadistica mas rapida

Cambios vs breakout original:
  - lookback: 60 -> 20-45 barras (mas frecuente)
  - vol_mult: 2.9x -> 1.5-2.5x (menos restrictivo)
  - allow_friday: True (agrega ~15% mas trades)
  - cooldown: 11 -> 4-8 barras
  - Acepta shorts en bear market tambien

Si P(CAGR>0) >= 70% con 150+ trades OOS -> usar en produccion
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; CAPITAL = 1000.0


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    df_1h = fetch_ohlcv(tf='1h', days=3200)
    df_4h = fetch_ohlcv(tf='4h', days=3200)
    df_1d = fetch_ohlcv(tf='1d', days=3200)
    df = build_features(df_1h, {'4h': df_4h, '1d': df_1d})
    df.dropna(subset=['close','atr','ema50'], inplace=True)
    return df


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def get_rsi_w_filter(df, threshold=55):
    close_w = df['close'].resample('W').last().ffill()
    rsi_w   = rsi(close_w, 14)
    return rsi_w.reindex(df.index, method='ffill') > threshold


def generate_signals(df, params):
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))
    atr = df['atr']
    ema200 = c.ewm(span=200, adjust=False).mean()
    vol_ma = v.rolling(20).mean()
    dow    = df.index.dayofweek  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    lb       = params['lookback']
    vm       = params['vol_mult']
    cd       = params['cooldown']
    allow_fri= params.get('allow_friday', True)
    bull_only= params.get('bull_only', True)
    rsi_w_thr= params.get('rsi_w_thr', 55)

    prev_high = h.rolling(lb).max().shift(1)
    prev_low  = l.rolling(lb).min().shift(1)
    vol_ok    = v > vol_ma * vm
    above_200 = c > ema200
    below_200 = c < ema200
    day_ok    = dow <= (4 if allow_fri else 3)

    # RSI-W bull filter
    rsi_w_bull = get_rsi_w_filter(df, rsi_w_thr)

    bull_filter = rsi_w_bull if bull_only else pd.Series(True, index=df.index)

    bl  = (c > prev_high) & vol_ok & above_200 & day_ok & bull_filter
    bs  = (c < prev_low)  & vol_ok & below_200 & day_ok

    sig = pd.Series(0, index=df.index)
    sl  = pd.Series(0.0, index=df.index)
    tp  = pd.Series(0.0, index=df.index)

    sl_m = params['sl_mult']
    tp_m = params['tp_mult']

    sig[bl]  = 1;  sl[bl]  = c[bl]  - atr[bl]  * sl_m; tp[bl]  = c[bl]  + atr[bl]  * tp_m
    sig[bs]  = -1; sl[bs]  = c[bs]  + atr[bs]  * sl_m; tp[bs]  = c[bs]  - atr[bs]  * tp_m

    # Apply cooldown
    last = -cd - 1
    final_sig = pd.Series(0, index=df.index)
    for i in range(len(sig)):
        if (i - last) >= cd and sig.iloc[i] != 0:
            final_sig.iloc[i] = sig.iloc[i]
            sl.iloc[i] = sl.iloc[i]
            tp.iloc[i] = tp.iloc[i]
            last = i

    return final_sig, sl, tp


def backtest(df, signals, sl_s, tp_s, risk_pct=3.3):
    c = df['close'].to_numpy(); h = df['high'].to_numpy(); lo = df['low'].to_numpy()
    ent = signals.to_numpy(); sl = sl_s.to_numpy(); tp = tp_s.to_numpy()
    cap = CAPITAL; eq = [cap]; pos = 0; entry_p = slv = tpv = size = 0.0; trades = []
    for i in range(1, len(c)):
        pr = c[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if lo[i] <= slv: pnl = size*(slv-entry_p)-size*(entry_p+slv)*COMMISSION; closed = True
                elif h[i] >= tpv: pnl = size*(tpv-entry_p)-size*(entry_p+tpv)*COMMISSION; closed = True
            else:
                if h[i] >= slv: pnl = size*(entry_p-slv)-size*(entry_p+slv)*COMMISSION; closed = True
                elif lo[i] <= tpv: pnl = size*(entry_p-tpv)-size*(entry_p+tpv)*COMMISSION; closed = True
            if closed: cap += pnl; trades.append({'pnl': pnl, 'won': pnl > 0}); pos = 0
        if pos == 0 and ent[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size = (cap*risk_pct/100)/rsl; pos = int(ent[i-1]); entry_p = pr; slv = sl[i-1]; tpv = tp[i-1]
        eq.append(cap)
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def metrics(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5: return None
    w = df_t[df_t['pnl'] > 0]; l = df_t[df_t['pnl'] <= 0]
    wr = len(w)/len(df_t)*100; pf = w['pnl'].sum()/abs(l['pnl'].sum()) if not l.empty and l['pnl'].sum() != 0 else 999
    peak = eq_s.cummax(); dd = ((eq_s-peak)/peak*100).min()
    last = float(eq_s.iloc[-1])
    if last <= 0: return None
    cagr = ((last/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr != cagr: return None
    tmo = len(df_t)/max(days/30.44, 0.1)
    return {'trades': len(df_t), 'wr': round(wr,1), 'cagr': round(cagr,1),
            'dd': round(dd,1), 'pf': round(pf,2), 'trades_month': round(tmo,1)}


def score(m, min_t=20):
    if m is None or m['trades'] < min_t or m['cagr'] <= 0: return -9999
    # Premia frecuencia + CAGR + WR
    s_freq = min(m['trades_month'] / 12.0, 1.0) * 0.30  # objetivo 12T/mes
    s_cagr = min(m['cagr'], 50) / 50 * 0.35
    s_wr   = max(m['wr']/100 - 0.45, 0) / 0.25 * 0.20
    s_cal  = min(m['cagr']/abs(m['dd']) if m['dd'] < 0 else 0, 3) / 3 * 0.15
    return s_freq + s_cagr + s_wr + s_cal


def run(n_trials=500):
    from core.database import save_run, init_db
    init_db()

    print('\n' + '='*65)
    print('  1H BREAKOUT HIGH-FREQ — Objetivo: 100-150T/ano')
    print(f'  {n_trials} trials | IS/OOS 80/20 | 8.7 anos')
    print('='*65)

    df = load_data()
    n = len(df); split = int(n * 0.80)
    df_is = df.iloc[:split]; df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f'  IS:  {df_is.index[0].strftime("%Y-%m-%d")} -> {df_is.index[-1].strftime("%Y-%m-%d")} ({days_is}d)')
    print(f'  OOS: {df_oos.index[0].strftime("%Y-%m-%d")} -> {df_oos.index[-1].strftime("%Y-%m-%d")} ({days_oos}d)')

    def objective(trial):
        p = {
            'lookback':    trial.suggest_int('lookback',    20, 50),
            'vol_mult':    trial.suggest_float('vol_mult',  1.3, 3.0, step=0.1),
            'sl_mult':     trial.suggest_float('sl_mult',   1.5, 3.5, step=0.1),
            'tp_mult':     trial.suggest_float('tp_mult',   1.2, 3.0, step=0.1),
            'cooldown':    trial.suggest_int('cooldown',    3, 12),
            'allow_friday':trial.suggest_categorical('allow_friday', [True, False]),
            'bull_only':   trial.suggest_categorical('bull_only',    [True, True, False]),
            'rsi_w_thr':   trial.suggest_int('rsi_w_thr',  45, 65),
        }
        risk_pct = trial.suggest_float('risk_pct', 2.0, 5.0, step=0.1)
        try:
            sig, sl, tp = generate_signals(df_is, p)
            if (sig != 0).sum() < 30: return -9999
            dt, eq = backtest(df_is, sig, sl, tp, risk_pct)
            m = metrics(dt, eq, days_is)
            s = score(m, min_t=30)
            if m and m['trades'] >= 20:
                save_run('1h', 'breakout_hf', p, m, s)
            return float(s) if s is not None else -9999
        except: return -9999

    study = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=80))

    best_s = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_s[0] and trial.value > 0.30:
            best_s[0] = trial.value
            p = trial.params
            try:
                sig, sl, tp = generate_signals(df_is, {k:v for k,v in p.items() if k != 'risk_pct'} | {'risk_pct': p.get('risk_pct',3.3)})
                dt, eq = backtest(df_is, sig, sl, tp, p.get('risk_pct',3.3))
                m = metrics(dt, eq, days_is)
                if m:
                    print(f'  [T{trial.number}] IS: {m["trades"]}T ({m["trades_month"]:.1f}/mes) | '
                          f'WR {m["wr"]:.1f}% | CAGR {m["cagr"]:+.1f}% | score={trial.value:.4f}')
            except: pass

    print(f'\n  Corriendo {n_trials} trials...')
    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    bp = study.best_params.copy()
    rp = bp.pop('risk_pct', 3.3)

    sig_is, sl_is, tp_is = generate_signals(df_is, bp)
    dt_is, eq_is = backtest(df_is, sig_is, sl_is, tp_is, rp)
    m_is = metrics(dt_is, eq_is, days_is)

    sig_oos, sl_oos, tp_oos = generate_signals(df_oos, bp)
    dt_oos, eq_oos = backtest(df_oos, sig_oos, sl_oos, tp_oos, rp)
    m_oos = metrics(dt_oos, eq_oos, days_oos)

    print('\n' + '='*65)
    print('  RESULTADO 1H BREAKOUT HIGH-FREQ')
    print('='*65)
    if m_is:
        print(f'  IS:  {m_is["trades"]}T ({m_is["trades_month"]:.1f}T/mes) | WR {m_is["wr"]:.1f}% | CAGR {m_is["cagr"]:+.1f}%')
    if m_oos:
        print(f'  OOS: {m_oos["trades"]}T ({m_oos["trades_month"]:.1f}T/mes) | WR {m_oos["wr"]:.1f}% | CAGR {m_oos["cagr"]:+.1f}% | DD {m_oos["dd"]:.1f}%')
        if m_oos['cagr'] > 0 and m_oos['trades'] > 50:
            print(f'  *** OOS POSITIVO CON {m_oos["trades"]} TRADES — CONFIANZA MEDIA/ALTA ***')
    print(f'  Params: lookback={bp.get("lookback")} vol={bp.get("vol_mult")} cooldown={bp.get("cooldown")}')
    print(f'  allow_friday={bp.get("allow_friday")} rsi_w_thr={bp.get("rsi_w_thr")}')

    if m_oos and m_oos.get('cagr', -999) > 0:
        out = OUTPUT_DIR / 'models' / '1h'
        result = {'tf':'1h','strategy':'BREAKOUT_HF','params':bp,'risk_pct':rp,
                  'metrics_is':m_is,'metrics_oos':m_oos,'target':'100-150T/ano'}
        with open(out/'best_bull_breakout_hf.json','w') as f:
            json.dump(result, f, indent=2, default=str)
        print(f'  [SAVED] models/1h/best_bull_breakout_hf.json')
    print('='*65)


if __name__ == '__main__':
    run()
