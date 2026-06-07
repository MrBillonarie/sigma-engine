"""
SIGMA SESSION ANALYZER
Desglosa el rendimiento del 1H Breakout por sesion de trading:
  - Asia:   01-06 UTC
  - London: 07-12 UTC
  - NY:     13-20 UTC
  - Off:    21-00 UTC

Si una sesion concentra el edge, se puede agregar un filtro de hora
para subir el WR de 55.8% a 60%+ sin perder demasiados trades.

Output: session_analysis.json + tabla por sesion con CAGR/WR/trades
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
CAPITAL     = 1000.0

SESSIONS = {
    'Asia':   (1, 6),
    'London': (7, 12),
    'NY':     (13, 20),
    'Off':    (21, 24),
}


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    df_1h = fetch_ohlcv(tf='1h', days=3200)
    df_4h = fetch_ohlcv(tf='4h', days=3200)
    df_1d = fetch_ohlcv(tf='1d', days=3200)
    df = build_features(df_1h, {'4h': df_4h, '1d': df_1d})
    df.dropna(subset=['close', 'atr', 'ema50'], inplace=True)
    return df


def generate_signals_breakout(df, params):
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))
    atr     = df['atr']
    ema200  = c.ewm(span=200, adjust=False).mean()
    vol_ma  = v.rolling(20).mean()

    lb  = params.get('lookback', 60)
    vm  = params.get('vol_mult', 2.9)
    slm = params.get('sl_mult', 2.3)
    tpm = params.get('tp_mult', 2.0)
    cd  = params.get('cooldown', 11)

    prev_high = h.rolling(lb).max().shift(1)
    vol_ok    = v > vol_ma * vm
    above_200 = c > ema200

    close_w  = c.resample('W').last().ffill()
    rsi_w    = rsi(close_w, 14)
    rsi_w_1h = rsi_w.reindex(df.index, method='ffill')
    bull_ok  = rsi_w_1h > 55

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


def backtest_with_sessions(df, sig, sl_s, tp_s, risk_pct=3.3):
    """Backtest que registra la sesion de entrada de cada trade."""
    c_arr  = df['close'].to_numpy()
    h_arr  = df['high'].to_numpy()
    lo_arr = df['low'].to_numpy()
    sig_arr = sig.to_numpy()
    sl_arr  = sl_s.to_numpy()
    tp_arr  = tp_s.to_numpy()
    hours   = df.index.hour

    cap = CAPITAL; pos = 0
    entry_p = slv = tpv = size = 0.0
    entry_session = ''
    trades = []

    for i in range(1, len(c_arr)):
        pr = c_arr[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if lo_arr[i] <= slv:
                pnl = size*(slv-entry_p) - size*(entry_p+slv)*COMMISSION
                closed = True
            elif h_arr[i] >= tpv:
                pnl = size*(tpv-entry_p) - size*(entry_p+tpv)*COMMISSION
                closed = True
            if closed:
                cap += pnl
                trades.append({'pnl': pnl, 'won': pnl > 0, 'session': entry_session})
                pos = 0

        if pos == 0 and sig_arr[i-1] == 1 and sl_arr[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl_arr[i-1])
            if rsl <= 0:
                continue
            hour = hours[i-1]
            sess = 'Off'
            for s_name, (s_start, s_end) in SESSIONS.items():
                if s_name == 'Off':
                    continue
                if s_start <= hour < s_end:
                    sess = s_name
                    break
            size = (cap * risk_pct / 100) / rsl
            pos = 1; entry_p = pr; slv = sl_arr[i-1]; tpv = tp_arr[i-1]
            entry_session = sess

    return pd.DataFrame(trades)


def session_metrics(df_trades, days):
    if df_trades.empty:
        return None
    w = df_trades[df_trades['pnl'] > 0]
    l = df_trades[df_trades['pnl'] <= 0]
    wr = len(w)/len(df_trades)*100
    pf = w['pnl'].sum()/abs(l['pnl'].sum()) if not l.empty and l['pnl'].sum() != 0 else 999
    total_pnl = df_trades['pnl'].sum()
    cagr_approx = (total_pnl / CAPITAL) / (days / 365.25) * 100
    return {
        'trades': len(df_trades),
        'wr': round(wr, 1),
        'pf': round(pf, 2),
        'total_pnl': round(total_pnl, 2),
        'cagr_est': round(cagr_approx, 1),
    }


def run():
    print('\n' + '='*65)
    print('  SIGMA SESSION ANALYZER — 1H Breakout por sesion')
    print('  Asia(01-06) | London(07-12) | NY(13-20) | Off(21-00)')
    print('='*65)

    model_path = OUTPUT_DIR / 'models' / '1h' / 'best_bull_breakout.json'
    if not model_path.exists():
        print('  Sin modelo best_bull_breakout.json'); return

    with open(model_path) as f:
        data = json.load(f)
    params   = data.get('params', {})
    risk_pct = data.get('risk_pct', 3.3)
    print(f'  Params: lookback={params.get("lookback")} vol={params.get("vol_mult")} risk={risk_pct}%\n')

    df = load_data()
    n  = len(df); split = int(n * 0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    results = {}
    best_session = None
    best_wr = 0

    for label, df_part, days in [('IS', df_is, days_is), ('OOS', df_oos, days_oos)]:
        print(f'  [{label}] {df_part.index[0].date()} -> {df_part.index[-1].date()} ({days}d)')
        sig, sl, tp = generate_signals_breakout(df_part, params)
        trades = backtest_with_sessions(df_part, sig, sl, tp, risk_pct)

        if trades.empty:
            print(f'  Sin trades'); continue

        print(f'  Total: {len(trades)} trades | WR {(trades["won"]).mean()*100:.1f}%')

        session_results = {}
        for sess in ['Asia', 'London', 'NY', 'Off']:
            t = trades[trades['session'] == sess]
            m = session_metrics(t, days)
            if m:
                pct = len(t)/len(trades)*100
                print(f'    {sess:8s}: {m["trades"]:3d}T ({pct:.0f}%) | WR {m["wr"]:.1f}% | PF {m["pf"]:.2f} | CAGR_est {m["cagr_est"]:+.1f}%')
                session_results[sess] = m
                if label == 'OOS' and m['wr'] > best_wr and m['trades'] >= 5:
                    best_wr = m['wr']
                    best_session = sess
            else:
                print(f'    {sess:8s}: sin trades')

        results[label] = session_results
        print()

    # Recomendacion
    print('  ' + '='*50)
    if best_session:
        m_oos = results.get('OOS', {}).get(best_session, {})
        print(f'  Mejor sesion OOS: {best_session} (WR {m_oos.get("wr",0):.1f}%, {m_oos.get("trades",0)} trades)')
        # Calcular cuantos trades perdemos si filtramos a esa sesion
        oos_all = results.get('OOS', {})
        total_oos = sum(v.get('trades', 0) for v in oos_all.values())
        sess_trades = oos_all.get(best_session, {}).get('trades', 0)
        retained = sess_trades / max(total_oos, 1) * 100
        print(f'  Si se filtra a {best_session}: {sess_trades}/{total_oos} trades retenidos ({retained:.0f}%)')
        if retained < 40:
            print(f'  ADVERTENCIA: perder {100-retained:.0f}% de trades puede reducir significancia estadistica')

    # Guardar
    out = OUTPUT_DIR / 'results' / 'reports' / 'session_analysis.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump({
            'timestamp':    str(pd.Timestamp.now()),
            'params':       params,
            'risk_pct':     risk_pct,
            'results':      results,
            'best_session': best_session,
            'best_wr_oos':  round(best_wr, 1),
        }, f, indent=2, default=str)
    print(f'\n  [SAVED] {out.name}')
    print('='*65)


if __name__ == '__main__':
    run()
