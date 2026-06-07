"""
SIGMA ASSET OPTIMIZER
Optimiza la estrategia 1H Breakout (o 4H) para cualquier activo crypto.

Uso:
  python asset_optimizer.py --symbol ETH/USDT --tf 1h
  python asset_optimizer.py --symbol XRP/USDT --tf 1h
  python asset_optimizer.py --symbol ETH/USDT --tf 4h

Misma logica que best_bull_breakout pero descarga datos del activo indicado.
Guarda: models/{tf}/{symbol_clean}_breakout.json
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, argparse, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
CAPITAL     = 1000.0


def fetch_asset(symbol, tf='1h', days=3200):
    import ccxt
    exchanges = [
        ccxt.binance({'timeout': 30000, 'options': {'defaultType': 'future'}}),
        ccxt.binance({'timeout': 30000}),
    ]
    since_ms = int((pd.Timestamp.now() - pd.Timedelta(days=days)).timestamp() * 1000)
    for ex in exchanges:
        try:
            all_ohlcv = []
            since = since_ms
            while True:
                data = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
                if not data: break
                all_ohlcv.extend(data)
                if len(data) < 1000: break
                since = data[-1][0] + 1
            if not all_ohlcv: continue
            df = pd.DataFrame(all_ohlcv, columns=['ts','open','high','low','close','volume'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            print(f'  [{symbol} {tf}] {len(df):,} velas descargadas')
            return df
        except Exception as e:
            print(f'  [{symbol}] Error exchange: {e}')
            continue
    return None


def add_features(df):
    c = df['close']; h = df['high']; l = df['low']; v = df['volume']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['atr']   = tr.ewm(alpha=1/14, adjust=False).mean()
    df['ema200']= c.ewm(span=200, adjust=False).mean()
    df['vol_ma']= v.rolling(20).mean()
    # RSI semanal
    close_w = c.resample('W').last().ffill()
    d = close_w.diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    ll = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi_w = 100 - 100 / (1 + g / (ll + 1e-9))
    df['rsi_w'] = rsi_w.reindex(df.index, method='ffill').fillna(50)
    df.dropna(subset=['atr', 'ema200'], inplace=True)
    return df


def generate_signals(df, params):
    c = df['close']; h = df['high']
    v = df['volume']
    atr    = df['atr']
    ema200 = df['ema200']
    vol_ma = df['vol_ma']

    lb   = params.get('lookback', 40)
    vm   = params.get('vol_mult', 2.0)
    slm  = params.get('sl_mult', 2.0)
    tpm  = params.get('tp_mult', 2.5)
    cd   = params.get('cooldown', 8)
    rsi_thr = params.get('rsi_w_thr', 55)

    prev_high = h.rolling(lb).max().shift(1)
    vol_ok    = v > vol_ma * vm
    above_200 = c > ema200
    bull_ok   = df['rsi_w'] > rsi_thr

    bl = (c > prev_high) & vol_ok & above_200 & bull_ok

    sig = pd.Series(0, index=df.index)
    sl_s = pd.Series(0.0, index=df.index)
    tp_s = pd.Series(0.0, index=df.index)

    last = -cd - 1
    for i in range(lb, len(df)):
        if (i - last) >= cd and bl.iloc[i]:
            sig.iloc[i] = 1
            sl_s.iloc[i] = c.iloc[i] - atr.iloc[i] * slm
            tp_s.iloc[i] = c.iloc[i] + atr.iloc[i] * tpm
            last = i

    return sig, sl_s, tp_s


def backtest(df, sig, sl_s, tp_s, risk_pct=3.3):
    c  = df['close'].to_numpy()
    h  = df['high'].to_numpy()
    lo = df['low'].to_numpy()
    sa = sig.to_numpy(); sla = sl_s.to_numpy(); tpa = tp_s.to_numpy()

    cap = CAPITAL; eq = [cap]; pos = 0
    entry_p = slv = tpv = sz = 0.0
    trades = []

    for i in range(1, len(c)):
        pr = c[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if lo[i] <= slv:
                pnl = sz*(slv-entry_p) - sz*(entry_p+slv)*COMMISSION; closed = True
            elif h[i] >= tpv:
                pnl = sz*(tpv-entry_p) - sz*(entry_p+tpv)*COMMISSION; closed = True
            if closed:
                cap += pnl; trades.append({'pnl': pnl, 'won': pnl > 0}); pos = 0
        if pos == 0 and sa[i-1] == 1 and sla[i-1] > 0 and cap > 50:
            rsl = abs(pr - sla[i-1])
            if rsl <= 0: continue
            sz = (cap * risk_pct / 100) / rsl
            pos = 1; entry_p = pr; slv = sla[i-1]; tpv = tpa[i-1]
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
    tpa  = len(df_t) / max(days/365.25, 0.1)
    return {'trades': len(df_t), 'wr': round(wr,1), 'cagr': round(cagr,1),
            'dd': round(dd,1), 'pf': round(pf,2), 'trades_year': round(tpa,1)}


def score(m, min_t=25):
    if m is None or m['trades'] < min_t or m['cagr'] <= 0: return -9999
    # Requiere minimo 8T/ano para tener datos OOS suficientes
    if m.get('trades_year', 0) < 6: return -9999
    s_freq = min(m.get('trades_year', 0) / 15.0, 1.0) * 0.25  # objetivo 15T/ano
    s_cagr = min(m['cagr'], 60) / 60 * 0.35
    s_wr   = max(m['wr']/100 - 0.52, 0) / 0.18 * 0.20
    s_cal  = min(m['cagr']/abs(m['dd']) if m['dd'] < 0 else 0, 5) / 5 * 0.15
    s_pf   = min(m['pf'], 3) / 3 * 0.05
    return s_freq + s_cagr + s_wr + s_cal + s_pf


def run(symbol='ETH/USDT', tf='1h', n_trials=500, days=3200):
    sym_clean = symbol.replace('/', '').replace('USDT', '').lower()

    print('\n' + '='*65)
    print(f'  SIGMA ASSET OPTIMIZER — {symbol} {tf.upper()}')
    print(f'  {n_trials} trials | IS/OOS 80/20 | {days//365}yr datos')
    print('='*65)

    df = fetch_asset(symbol, tf, days)
    if df is None or len(df) < 500:
        print(f'  Sin datos para {symbol}'); return

    df = add_features(df)
    n = len(df); split = int(n * 0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f'  IS:  {df_is.index[0].strftime("%Y-%m-%d")} -> {df_is.index[-1].strftime("%Y-%m-%d")} ({days_is}d)')
    print(f'  OOS: {df_oos.index[0].strftime("%Y-%m-%d")} -> {df_oos.index[-1].strftime("%Y-%m-%d")} ({days_oos}d)\n')

    def objective(trial):
        p = {
            'lookback':  trial.suggest_int('lookback',   20, 80),
            'vol_mult':  trial.suggest_float('vol_mult',  1.2, 4.0, step=0.1),
            'sl_mult':   trial.suggest_float('sl_mult',   1.5, 3.5, step=0.1),
            'tp_mult':   trial.suggest_float('tp_mult',   1.5, 4.0, step=0.1),
            'cooldown':  trial.suggest_int('cooldown',    4, 16),
            'rsi_w_thr': trial.suggest_int('rsi_w_thr',  45, 65),
        }
        rp = trial.suggest_float('risk_pct', 2.0, 5.0, step=0.1)
        try:
            sig, sl, tp = generate_signals(df_is, p)
            if (sig != 0).sum() < 20: return -9999
            dt, eq = backtest(df_is, sig, sl, tp, rp)
            m = metrics(dt, eq, days_is)
            s = score(m, min_t=15)
            return float(s) if s is not None else -9999
        except:
            return -9999

    study = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=80))

    best_s = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_s[0] and trial.value > 0.25:
            best_s[0] = trial.value
            p = {k: v for k, v in trial.params.items() if k != 'risk_pct'}
            rp = trial.params.get('risk_pct', 3.3)
            try:
                sig, sl, tp = generate_signals(df_is, p)
                dt, eq = backtest(df_is, sig, sl, tp, rp)
                m = metrics(dt, eq, days_is)
                if m:
                    print(f'  [T{trial.number}] IS: {m["trades"]}T ({m["trades_year"]:.1f}T/ano) '
                          f'| WR {m["wr"]:.1f}% | CAGR {m["cagr"]:+.1f}% | s={trial.value:.4f}')
            except:
                pass

    print(f'  Corriendo {n_trials} trials...')
    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    bp = {k: v for k, v in study.best_params.items() if k != 'risk_pct'}
    rp = study.best_params.get('risk_pct', 3.3)

    sig_is, sl_is, tp_is = generate_signals(df_is, bp)
    dt_is, eq_is = backtest(df_is, sig_is, sl_is, tp_is, rp)
    m_is = metrics(dt_is, eq_is, days_is)

    sig_oos, sl_oos, tp_oos = generate_signals(df_oos, bp)
    dt_oos, eq_oos = backtest(df_oos, sig_oos, sl_oos, tp_oos, rp)
    m_oos = metrics(dt_oos, eq_oos, days_oos)

    print('\n' + '='*65)
    print(f'  RESULTADO {symbol} {tf.upper()}')
    print('='*65)
    if m_is:
        print(f'  IS:  {m_is["trades"]}T ({m_is["trades_year"]:.1f}T/ano) | '
              f'WR {m_is["wr"]:.1f}% | CAGR {m_is["cagr"]:+.1f}% | DD {m_is["dd"]:.1f}%')
    if m_oos:
        print(f'  OOS: {m_oos["trades"]}T ({m_oos["trades_year"]:.1f}T/ano) | '
              f'WR {m_oos["wr"]:.1f}% | CAGR {m_oos["cagr"]:+.1f}% | DD {m_oos["dd"]:.1f}%')
        gap = abs(m_is['cagr'] - m_oos['cagr']) if m_is and m_oos else 999
        if m_oos['cagr'] > 0:
            verdict = 'POSITIVO'
            if gap < 30: verdict += ' — generalizacion buena'
            else:        verdict += ' — IS/OOS gap alto'
            print(f'  {verdict}')
        else:
            print(f'  OOS negativo — no guardado')
    else:
        print(f'  OOS: sin trades suficientes (<5) — params demasiado selectivos')
    print(f'  Params: lb={bp.get("lookback")} vol={bp.get("vol_mult")} '
          f'cd={bp.get("cooldown")} rsi_w={bp.get("rsi_w_thr")}')

    # Guardar si OOS positivo
    if m_oos and m_oos.get('cagr', -999) > 0:
        out_dir = OUTPUT_DIR / 'models' / tf
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            'symbol': symbol, 'tf': tf, 'strategy': 'breakout',
            'params': bp, 'risk_pct': rp,
            'metrics_is': m_is, 'metrics_oos': m_oos,
        }
        fname = f'{sym_clean}_breakout.json'
        with open(out_dir / fname, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        print(f'  [SAVED] models/{tf}/{fname}')
    print('='*65)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--symbol',  default='ETH/USDT')
    p.add_argument('--tf',      default='1h', choices=['4h','1h','15m','5m'])
    p.add_argument('--trials',  type=int, default=500)
    p.add_argument('--days',    type=int, default=3200)
    args = p.parse_args()
    run(args.symbol, args.tf, args.trials, args.days)
