"""
SIGMA PORTFOLIO SIMULATOR
Simula correr 1H Breakout + 4H Aggressive simultaneamente con capital compartido.

Approach: run each strategy independently on its own TF data to get a list of
trades with entry/exit timestamps and PnL. Then replay both trade lists against
a shared capital pool in chronological order.

Saves: results/reports/portfolio_simulation.json
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
CAPITAL     = 1000.0


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
    df_1h_f = build_features(df_1h, {'4h': df_4h, '1d': df_1d})
    df_4h_f = build_features(df_4h, {'1d': df_1d})
    df_1h_f.dropna(subset=['close', 'atr', 'ema50'], inplace=True)
    df_4h_f.dropna(subset=['close', 'atr', 'ema50'], inplace=True)
    return df_1h_f, df_4h_f


def signals_1h_breakout(df, params):
    c = df['close']; h = df['high']
    v = df.get('volume', pd.Series(1, index=df.index))
    atr    = df['atr']
    ema200 = c.ewm(span=200, adjust=False).mean()
    vol_ma = v.rolling(20).mean()

    lb  = params.get('lookback', 60)
    vm  = params.get('vol_mult', 2.9)
    slm = params.get('sl_mult', 2.3)
    tpm = params.get('tp_mult', 2.0)
    cd  = params.get('cooldown', 11)

    prev_high = h.rolling(lb).max().shift(1)
    vol_ok    = v > vol_ma * vm
    above_200 = c > ema200
    close_w   = c.resample('W').last().ffill()
    rsi_w     = rsi(close_w, 14)
    rsi_w_1h  = rsi_w.reindex(df.index, method='ffill')
    bull_ok   = rsi_w_1h > 55
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


def backtest_with_timestamps(df, sig, sl_s, tp_s, risk_pct, strat_name):
    """Run backtest and return list of trades with entry/exit timestamps."""
    c_arr  = df['close'].to_numpy()
    h_arr  = df['high'].to_numpy()
    lo_arr = df['low'].to_numpy()
    s_arr  = sig.to_numpy()
    sl_arr = sl_s.to_numpy()
    tp_arr = tp_s.to_numpy()
    idx    = df.index

    cap = CAPITAL; pos = 0
    entry_p = slv = tpv = sz = 0.0
    entry_ts = None
    trades = []

    for i in range(1, len(c_arr)):
        pr = c_arr[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if lo_arr[i] <= slv:
                    pnl = sz*(slv-entry_p) - sz*(entry_p+slv)*COMMISSION; closed = True
                elif h_arr[i] >= tpv:
                    pnl = sz*(tpv-entry_p) - sz*(entry_p+tpv)*COMMISSION; closed = True
            else:
                if h_arr[i] >= slv:
                    pnl = sz*(entry_p-slv) - sz*(entry_p+slv)*COMMISSION; closed = True
                elif lo_arr[i] <= tpv:
                    pnl = sz*(entry_p-tpv) - sz*(entry_p+tpv)*COMMISSION; closed = True
            if closed:
                cap += pnl
                trades.append({
                    'entry_ts': entry_ts, 'exit_ts': idx[i],
                    'pnl': pnl, 'pnl_pct': pnl / CAPITAL * 100,
                    'strat': strat_name,
                })
                pos = 0

        if pos == 0 and s_arr[i-1] != 0 and sl_arr[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl_arr[i-1])
            if rsl > 0:
                sz = (cap * risk_pct / 100) / rsl
                pos = int(s_arr[i-1]); entry_p = pr
                slv = sl_arr[i-1]; tpv = tp_arr[i-1]
                entry_ts = idx[i]

    final_cap = cap
    trades_df = pd.DataFrame(trades)
    eq_pnl = [0.0]
    running_pnl = 0.0
    for t in trades:
        running_pnl += t['pnl']
        eq_pnl.append(running_pnl)

    return trades, final_cap


def portfolio_replay(trades_1h, trades_4h, reduce_on_overlap=True):
    """
    Replay both trade lists against shared capital chronologically.
    Trades are pre-computed on CAPITAL base. We scale PnL proportionally
    as the shared capital grows/shrinks.
    """
    # Build unified event list: entry + exit per trade
    events = []
    for t in trades_1h:
        if t['entry_ts'] and t['exit_ts']:
            events.append({'ts': t['entry_ts'], 'type': 'entry', 'trade': t})
            events.append({'ts': t['exit_ts'],  'type': 'exit',  'trade': t})
    for t in trades_4h:
        if t['entry_ts'] and t['exit_ts']:
            events.append({'ts': t['entry_ts'], 'type': 'entry', 'trade': t})
            events.append({'ts': t['exit_ts'],  'type': 'exit',  'trade': t})

    events.sort(key=lambda x: (x['ts'], 0 if x['type'] == 'exit' else 1))

    cap = CAPITAL
    active = {}
    combined_trades = []
    overlap_bars = 0

    for ev in events:
        n_active = len(active)
        if n_active >= 2:
            overlap_bars += 1

        trade = ev['trade']
        strat = trade['strat']
        tid   = id(trade)

        if ev['type'] == 'entry':
            if strat not in active:
                size_mult = 0.70 if (n_active > 0 and reduce_on_overlap) else 1.0
                cap_ratio = cap / CAPITAL
                active[strat] = {'trade': trade, 'size_mult': size_mult, 'cap_ratio': cap_ratio}

        elif ev['type'] == 'exit':
            if strat in active:
                info = active.pop(strat)
                raw_pnl_pct = trade['pnl_pct']
                # Scale by current cap and size_mult
                adjusted_pnl = (raw_pnl_pct / 100) * CAPITAL * info['size_mult'] * info['cap_ratio']
                cap += adjusted_pnl
                combined_trades.append({'pnl': adjusted_pnl, 'strat': strat, 'ts': ev['ts']})

    return combined_trades, cap, overlap_bars


def calc_metrics(trades, final_cap, days):
    if not trades: return None
    df_t = pd.DataFrame(trades)
    pnls = df_t['pnl'].values
    wins = pnls[pnls > 0]; loss = pnls[pnls <= 0]
    wr = len(wins)/len(pnls)*100
    pf = wins.sum()/abs(loss.sum()) if len(loss) > 0 and loss.sum() != 0 else 999
    eq = CAPITAL
    eq_curve = [CAPITAL]
    for p in pnls:
        eq += p; eq_curve.append(eq)
    eq_s = pd.Series(eq_curve)
    peak = eq_s.cummax(); dd = ((eq_s-peak)/peak*100).min()
    cagr = ((final_cap/CAPITAL)**(365.25/max(days,1))-1)*100
    return {
        'trades': len(df_t), 'wr': round(wr,1), 'cagr': round(cagr,1),
        'dd': round(float(dd),1), 'pf': round(float(pf),2),
    }


def run():
    print('\n' + '='*65)
    print('  SIGMA PORTFOLIO SIMULATOR')
    print('  1H Breakout + 4H Aggressive --- Capital Compartido')
    print('='*65)

    p1h_path = OUTPUT_DIR / 'models' / '1h' / 'best_bull_breakout.json'
    p4h_path = OUTPUT_DIR / 'models' / '4h' / 'best_validated.json'

    if not p1h_path.exists():
        print('  Sin modelo 1H'); return
    if not p4h_path.exists():
        print('  Sin modelo 4H'); return

    with open(p1h_path) as f: d1 = json.load(f)
    with open(p4h_path) as f: d4 = json.load(f)

    params_1h = d1.get('params', {})
    risk_1h   = d1.get('risk_pct', 3.3)
    params_4h = d4.get('params', {})
    risk_4h   = d4.get('risk_pct', 3.3)
    cagr_1h   = d1.get('metrics_oos', {}).get('cagr', 0)
    cagr_4h   = d4.get('metrics_oos', {}).get('cagr', 0)

    print(f'  1H: risk={risk_1h:.1f}% | OOS CAGR {cagr_1h:+.1f}%')
    print(f'  4H: risk={risk_4h:.1f}% | OOS CAGR {cagr_4h:+.1f}%')
    print(f'  Modelo teorico (compuesto): {(1+cagr_1h/100)*(1+cagr_4h/100)-1:.1%}\n')

    df_1h, df_4h = load_data()
    split_1h = int(len(df_1h) * 0.80)
    split_4h = int(len(df_4h) * 0.80)
    df_1h_oos = df_1h.iloc[split_1h:]
    df_4h_oos = df_4h.iloc[split_4h:]
    days_oos = (df_1h_oos.index[-1] - df_1h_oos.index[0]).days

    print(f'  OOS: {df_1h_oos.index[0].date()} -> {df_1h_oos.index[-1].date()} ({days_oos}d)')
    print('  Generando senales...')

    sig_1h, sl_1h, tp_1h = signals_1h_breakout(df_1h_oos, params_1h)
    trades_1h, cap_1h = backtest_with_timestamps(df_1h_oos, sig_1h, sl_1h, tp_1h, risk_1h, '1H')

    try:
        from core.signals import get_signals
        sig_4h_raw, _ = get_signals(df_4h_oos, params_4h)
        atr_4h = df_4h_oos['atr']
        c_4h   = df_4h_oos['close']
        slm4   = params_4h.get('sl_mult', 1.5)
        tpm4   = params_4h.get('tp_mult', 2.5)
        sl_4h  = pd.Series(0.0, index=df_4h_oos.index)
        tp_4h  = pd.Series(0.0, index=df_4h_oos.index)
        sl_4h[sig_4h_raw ==  1] = c_4h[sig_4h_raw ==  1] - atr_4h[sig_4h_raw ==  1] * slm4
        tp_4h[sig_4h_raw ==  1] = c_4h[sig_4h_raw ==  1] + atr_4h[sig_4h_raw ==  1] * tpm4
        sl_4h[sig_4h_raw == -1] = c_4h[sig_4h_raw == -1] + atr_4h[sig_4h_raw == -1] * slm4
        tp_4h[sig_4h_raw == -1] = c_4h[sig_4h_raw == -1] - atr_4h[sig_4h_raw == -1] * tpm4
        trades_4h, cap_4h = backtest_with_timestamps(df_4h_oos, sig_4h_raw, sl_4h, tp_4h, risk_4h, '4H')
    except Exception as e:
        print(f'  4H signals error: {e}')
        trades_4h = []; cap_4h = CAPITAL

    m1 = calc_metrics(trades_1h, cap_1h, days_oos)
    m4 = calc_metrics(trades_4h, cap_4h, days_oos) if trades_4h else None

    print('\n  Resultados individuales (capital separado):')
    if m1: print(f'  1H: {m1["trades"]}T | WR {m1["wr"]:.1f}% | CAGR {m1["cagr"]:+.1f}% | DD {m1["dd"]:.1f}%')
    if m4: print(f'  4H: {m4["trades"]}T | WR {m4["wr"]:.1f}% | CAGR {m4["cagr"]:+.1f}% | DD {m4["dd"]:.1f}%')

    # Portfolio combinado
    combined, cap_port, overlap_bars = portfolio_replay(trades_1h, trades_4h)
    mp = calc_metrics(combined, cap_port, days_oos) if combined else None
    overlap_pct = overlap_bars / max(len(trades_1h)+len(trades_4h), 1) * 100

    print('\n  Portfolio combinado (capital compartido, -30% size en overlap):')
    if mp:
        print(f'  Portfolio: {mp["trades"]}T | WR {mp["wr"]:.1f}% | CAGR {mp["cagr"]:+.1f}% | DD {mp["dd"]:.1f}%')
        if m1: print(f'  CAGR adicional vs solo 1H: {mp["cagr"]-m1["cagr"]:+.1f}pp')
    print(f'  Trades en overlap (ambas activas): {overlap_bars} ({overlap_pct:.1f}%)')

    if combined:
        by_strat = {}
        for t in combined:
            by_strat[t['strat']] = by_strat.get(t['strat'], 0) + t['pnl']
        print('\n  Contribucion por estrategia:')
        for s, pnl in by_strat.items():
            print(f'    {s}: ${pnl:+.2f}')

    # Guardar
    result = {
        'timestamp': str(pd.Timestamp.now()),
        'metrics_1h_solo': m1,
        'metrics_4h_solo': m4,
        'metrics_portfolio': mp,
        'overlap_pct': round(overlap_pct, 1),
        'risk_1h': risk_1h, 'risk_4h': risk_4h,
    }
    out = OUTPUT_DIR / 'results' / 'reports' / 'portfolio_simulation.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    print(f'\n  [SAVED] {out.name}')
    print('='*65)


if __name__ == '__main__':
    run()
