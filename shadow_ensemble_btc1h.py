"""shadow_ensemble_btc1h.py — Backtest histórico ensemble 2/3 BTC 1H."""
import sys, json
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine')

import pandas as pd
import numpy as np
from pathlib import Path

from engine.optimization import asset_pipeline as ap


def load_btc_1h(oos_days=637):
    """Carga BTC 1H y devuelve solo el período OOS (últimos N días)."""
    csv = Path('/opt/sigma/models/data_1h_max.csv')
    df = pd.read_csv(csv)
    if 'symbol' in df.columns:
        df = df[df['symbol'].str.contains('BTC', case=False, na=False)].copy()
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').set_index('timestamp')
    df = ap.add_features(df)
    bars_oos = oos_days * 24
    return df.iloc[-bars_oos:].copy() if len(df) > bars_oos else df


def load_params(strategy):
    p = Path(f'/opt/sigma/models/1h/btc_{strategy}.json')
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get('params', {})


def sig_fn_for(strategy):
    for d_name in ['SIG_FN', 'SIG_FN_SHORT', 'SIG_FN_ADAPTIVE']:
        if hasattr(ap, d_name):
            d = getattr(ap, d_name)
            if strategy in d:
                return d[strategy]
    return None


def run_signals(df, strategy):
    """Corre la estrategia. Retorna {long, short, sl, tp} con long/short como bool series."""
    fn = sig_fn_for(strategy)
    if fn is None:
        return None
    p = load_params(strategy)
    if not p:
        return None
    out = fn(df, p)
    # _apply_cd retorna (signal, sl, tp). signal: 1=long, -1=short, 0=flat
    if isinstance(out, tuple) and len(out) >= 1:
        signal = out[0]
        sl = out[1] if len(out) > 1 else None
        tp = out[2] if len(out) > 2 else None
        return {'long': (signal == 1), 'short': (signal == -1), 'sl': sl, 'tp': tp}
    return {'long': (out == 1), 'short': (out == -1), 'sl': None, 'tp': None}


def count_trades(sigs):
    if sigs is None: return 0
    l = int(sigs['long'].sum())  if sigs.get('long')  is not None else 0
    s = int(sigs['short'].sum()) if sigs.get('short') is not None else 0
    return l + s


def backtest_signals(df, longs, shorts, sl_series=None, tp_series=None, default_sl_pct=0.02, default_tp_pct=0.03):
    """Backtest minimal: cada señal entra al close del bar, sale por SL/TP o max bars."""
    close = df['close'].values
    n = len(df)
    longs_arr  = longs.values  if hasattr(longs, 'values')  else np.array(longs)
    shorts_arr = shorts.values if hasattr(shorts, 'values') else np.array(shorts)
    sl_arr = sl_series.values if sl_series is not None and hasattr(sl_series, 'values') else None
    tp_arr = tp_series.values if tp_series is not None and hasattr(tp_series, 'values') else None
    trades = []
    i = 0
    max_bars = 48
    while i < n - 1:
        if longs_arr[i]:
            entry = close[i]
            sl = sl_arr[i] if (sl_arr is not None and sl_arr[i] > 0) else entry * (1 - default_sl_pct)
            tp = tp_arr[i] if (tp_arr is not None and tp_arr[i] > 0) else entry * (1 + default_tp_pct)
            exit_p, exit_i = None, i + max_bars
            for j in range(i + 1, min(i + max_bars + 1, n)):
                if close[j] <= sl: exit_p, exit_i = sl, j; break
                if close[j] >= tp: exit_p, exit_i = tp, j; break
            if exit_p is None: exit_p, exit_i = close[min(i + max_bars, n-1)], min(i + max_bars, n-1)
            trades.append({'dir': 1, 'entry': entry, 'exit': exit_p, 'pnl_pct': (exit_p / entry - 1) * 100, 'bars': exit_i - i})
            i = exit_i + 1
        elif shorts_arr[i]:
            entry = close[i]
            sl = sl_arr[i] if (sl_arr is not None and sl_arr[i] > 0) else entry * (1 + default_sl_pct)
            tp = tp_arr[i] if (tp_arr is not None and tp_arr[i] > 0) else entry * (1 - default_tp_pct)
            exit_p, exit_i = None, i + max_bars
            for j in range(i + 1, min(i + max_bars + 1, n)):
                if close[j] >= sl: exit_p, exit_i = sl, j; break
                if close[j] <= tp: exit_p, exit_i = tp, j; break
            if exit_p is None: exit_p, exit_i = close[min(i + max_bars, n-1)], min(i + max_bars, n-1)
            trades.append({'dir': -1, 'entry': entry, 'exit': exit_p, 'pnl_pct': (entry / exit_p - 1) * 100, 'bars': exit_i - i})
            i = exit_i + 1
        else:
            i += 1
    if not trades:
        return {'trades': 0, 'wr': 0, 'avg_pnl': 0, 'pnl_total': 0, 'pf': 0, 'cagr_est': 0}
    pnls = [t['pnl_pct'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 99.0
    pnl_total = sum(pnls)
    days = n / 24.0
    cagr_est = pnl_total * (365.0 / days)
    return {
        'trades': len(trades),
        'wr': len(wins) / len(trades) * 100,
        'avg_pnl': pnl_total / len(trades),
        'pnl_total': pnl_total,
        'pf': pf,
        'cagr_est': cagr_est,
        'longs': sum(1 for t in trades if t['dir'] == 1),
        'shorts': sum(1 for t in trades if t['dir'] == -1),
    }


def main():
    print('=' * 70)
    print('SHADOW ENSEMBLE BTC 1H — backtest hist órico')
    print('=' * 70)

    print('\nCargando data BTC 1H OOS...')
    df = load_btc_1h()
    print(f'  Bars: {len(df)}  desde {df.index[0]} hasta {df.index[-1]}')
    print(f'  Columnas: {len(df.columns)}')

    strategies = ['momentum_short', 'pullback', 'momentum']
    sigs_all = {}
    for s in strategies:
        sigs = run_signals(df, s)
        sigs_all[s] = sigs
        n = count_trades(sigs)
        print(f'\n{s}: {n} bars con señal')
        if sigs and sigs.get('long') is not None:
            print(f'  Longs: {int(sigs["long"].sum())}')
        if sigs and sigs.get('short') is not None:
            print(f'  Shorts: {int(sigs["short"].sum())}')

    # Ensemble 2/3
    print('\n=== ENSEMBLE 2/3 ===')
    # Tomamos todas las series long y short
    longs  = [sigs_all[s]['long']  for s in strategies if sigs_all[s] and sigs_all[s].get('long')  is not None]
    shorts = [sigs_all[s]['short'] for s in strategies if sigs_all[s] and sigs_all[s].get('short') is not None]

    if longs:
        long_vote  = sum(l.astype(int) for l in longs) >= 2
        print(f'Longs ensemble (2/3 acuerdo): {int(long_vote.sum())} bars')
    if shorts:
        short_vote = sum(s.astype(int) for s in shorts) >= 2
        print(f'Shorts ensemble (2/3 acuerdo): {int(short_vote.sum())} bars')

    print('\n--- Resumen comparativo (señales en barras OOS) ---')
    for s in strategies:
        n = count_trades(sigs_all[s])
        print(f'  {s:18s}: {n:5d} bars')
    if longs:
        print(f'  ENSEMBLE long      : {int(long_vote.sum()):5d} bars')
    if shorts:
        print(f'  ENSEMBLE short     : {int(short_vote.sum()):5d} bars')

    # BACKTEST individual de cada estrategia
    print('\n=== BACKTEST INDIVIDUAL ===')
    individual_results = {}
    for s in strategies:
        si = sigs_all[s]
        if si is None:
            individual_results[s] = {'error': 'no signal'}
            continue
        res = backtest_signals(df, si['long'], si['short'], si.get('sl'), si.get('tp'))
        individual_results[s] = res
        print(f'  {s:18s}: {res["trades"]:3d} trades  WR {res["wr"]:5.1f}%  PnL_total {res["pnl_total"]:+7.2f}%  CAGR_est {res["cagr_est"]:+7.2f}%  PF {res["pf"]:.2f}')

    # ENSEMBLE WINDOW AGREEMENT — esta barra dispara + otra estrategia disparó en últimas W barras
    print('\n=== ENSEMBLE WINDOW AGREEMENT (varias ventanas) ===')
    window_results = {}
    for W in [3, 6, 12, 24, 48]:
        long_arrs  = [(sigs_all[s]['long'].values  if sigs_all[s] else np.zeros(len(df),dtype=bool)) for s in strategies]
        short_arrs = [(sigs_all[s]['short'].values if sigs_all[s] else np.zeros(len(df),dtype=bool)) for s in strategies]
        n = len(df)
        ens_long  = np.zeros(n, dtype=bool)
        ens_short = np.zeros(n, dtype=bool)
        for i in range(n):
            # Longs
            firing = [k for k, a in enumerate(long_arrs) if a[i]]
            if firing:
                for k_o, a in enumerate(long_arrs):
                    if k_o in firing: continue
                    if a[max(0,i-W+1):i].any():
                        ens_long[i] = True; break
            # Shorts
            firing = [k for k, a in enumerate(short_arrs) if a[i]]
            if firing:
                for k_o, a in enumerate(short_arrs):
                    if k_o in firing: continue
                    if a[max(0,i-W+1):i].any():
                        ens_short[i] = True; break
        ens_long_s  = pd.Series(ens_long,  index=df.index)
        ens_short_s = pd.Series(ens_short, index=df.index)
        # SL/TP por defecto (no promediar series que tienen 0 en bars vacíos — bug original)
        sl_avg = None
        tp_avg = None
        res = backtest_signals(df, ens_long_s, ens_short_s, sl_avg, tp_avg)
        window_results[W] = res
        print(f'  W={W:3d}h: {res["trades"]:4d} trades  WR {res["wr"]:5.1f}%  PnL {res["pnl_total"]:+7.2f}%  CAGR_est {res["cagr_est"]:+7.2f}%  PF {res["pf"]:.2f}')

    # BACKTEST ensemble original (same-bar) ya hecho arriba — sumamos
    print('\n=== BACKTEST ENSEMBLE 2/3 SAME-BAR ===')
    if longs and shorts:
        # Para el SL/TP del ensemble, uso el promedio de los strategies que acuerdan
        # SL/TP por defecto (no promediar series que tienen 0 en bars vacíos — bug original)
        sl_avg = None
        tp_avg = None
        res_ens = backtest_signals(df, long_vote, short_vote, sl_avg, tp_avg)
        print(f'  ensemble 2/3       : {res_ens["trades"]:3d} trades  WR {res_ens["wr"]:5.1f}%  PnL_total {res_ens["pnl_total"]:+7.2f}%  CAGR_est {res_ens["cagr_est"]:+7.2f}%  PF {res_ens["pf"]:.2f}')
    else:
        res_ens = {}

    print('\n=== COMPARATIVO FINAL ===')
    base = individual_results.get('momentum_short', {})
    print(f'  Campeón actual (momentum_short solo): {base.get("trades",0):4d} trades  WR {base.get("wr",0):5.1f}%  CAGR_est {base.get("cagr_est",0):+7.2f}%  PF {base.get("pf",0):.2f}')
    print(f'  Ensemble same-bar                   : {res_ens.get("trades",0):4d} trades  WR {res_ens.get("wr",0):5.1f}%  CAGR_est {res_ens.get("cagr_est",0):+7.2f}%')
    for W, r in window_results.items():
        print(f'  Ensemble window W={W:3d}h            : {r["trades"]:4d} trades  WR {r["wr"]:5.1f}%  CAGR_est {r["cagr_est"]:+7.2f}%  PF {r["pf"]:.2f}')

    out = {
        'oos_bars': len(df),
        'oos_start': str(df.index[0]),
        'oos_end':   str(df.index[-1]),
        'individual': individual_results,
        'ensemble_same_bar': res_ens,
        'ensemble_window':   {f'W={W}': r for W, r in window_results.items()},
    }
    Path('/opt/sigma/results/reports/shadow_ensemble_btc1h.json').write_text(json.dumps(out, indent=2, default=str))
    print('\nGuardado: /opt/sigma/results/reports/shadow_ensemble_btc1h.json')


if __name__ == '__main__':
    main()
