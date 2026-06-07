"""
SIGMA WALK-FORWARD v2 — Rolling Window Validation
Divide los 8.7 anos en ventanas de 6 meses, optimiza en cada una
y valida en el siguiente. Detecta si los params son estables
a traves del tiempo o si overfitan a un periodo especifico.

Ventana:  6 meses IS (entrenar) + 2 meses OOS (validar)
Pasos:    1 mes
Total:    ~80 ventanas desde 2017 hasta 2026

Metricas que importan:
  - % ventanas positivas (queremos > 55%)
  - CAGR promedio OOS de ventanas positivas
  - Consistencia: std baja = mas confiable
  - Factor mejora: OOS/IS ratio
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from datetime import datetime, timedelta

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent


def run_walk_forward(tf='1h', train_months=6, test_months=2, step_months=1, trials=150):
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics, score_config
    from core.database import save_run, init_db

    init_db()

    print(f'\n{"="*65}')
    print(f'  WALK-FORWARD v2 — {tf.upper()}')
    print(f'  Train: {train_months}m | Test: {test_months}m | Step: {step_months}m')
    print(f'  {trials} trials por ventana')
    print(f'{"="*65}')

    htf_map = {'1h': ('4h','1d'), '4h': ('1d','1d'), '15m': ('1h','4h')}
    h1, h2  = htf_map.get(tf, ('4h','1d'))
    df_b  = fetch_ohlcv(tf=tf, days=3200)
    df_h1 = fetch_ohlcv(tf=h1, days=3200)
    df_h2 = fetch_ohlcv(tf=h2, days=3200)
    df    = build_features(df_b, {h1: df_h1, h2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)
    print(f'  {len(df):,} velas cargadas')

    start = df.index[0] + pd.DateOffset(months=train_months)
    end   = df.index[-1] - pd.DateOffset(months=test_months)

    windows = []
    cur = start
    while cur <= end:
        train_start = cur - pd.DateOffset(months=train_months)
        train_end   = cur
        test_end    = cur + pd.DateOffset(months=test_months)
        windows.append((train_start, train_end, test_end))
        cur += pd.DateOffset(months=step_months)

    print(f'  {len(windows)} ventanas | {start.strftime("%Y-%m")} -> {end.strftime("%Y-%m")}')

    results = []
    min_trades = {'1h': 8, '4h': 3, '15m': 15}.get(tf, 8)

    print(f'\n  {"Ventana":12} {"Train":>8} {"Test":>8}  {"T":>4}  {"WR":>6}  {"CAGR":>8}  {"ST":>4}')
    print(f'  {"-"*60}')

    for i, (ts, te, tt) in enumerate(windows):
        df_train = df[(df.index >= ts) & (df.index < te)]
        df_test  = df[(df.index >= te) & (df.index < tt)]
        if len(df_train) < 200 or len(df_test) < 50:
            continue

        days_train = (te - ts).days
        days_test  = (tt - te).days

        def objective(trial):
            p = {
                'use_elite_ict': trial.suggest_categorical('use_elite_ict', [True, False]),
                'use_elite':     trial.suggest_categorical('use_elite',     [True, False]),
                'use_execute':   trial.suggest_categorical('use_execute',   [True, True, False]),
                'use_trend':     trial.suggest_categorical('use_trend',     [True, False]),
                'use_range':     trial.suggest_categorical('use_range',     [True, False]),
                'use_sess_b':    trial.suggest_categorical('use_sess_b',    [True, False]),
                'use_asia':      trial.suggest_categorical('use_asia',      [True, False]),
                'allow_friday':  trial.suggest_categorical('allow_friday',  [True, False]),
                'req_htf2':      trial.suggest_categorical('req_htf2',      [True, False]),
                'use_be':        False,
                'adx_min':       trial.suggest_int('adx_min', 10, 30),
                'hurst_t':       trial.suggest_float('hurst_t', 0.50, 0.65, step=0.01),
                'adx_t':         trial.suggest_int('adx_t', 15, 35),
                'hurst_r':       trial.suggest_float('hurst_r', 0.42, 0.53, step=0.01),
                'adx_r':         trial.suggest_int('adx_r', 10, 22),
                'temp_min':      trial.suggest_int('temp_min', 5, 25),
                'temp_max':      trial.suggest_int('temp_max', 70, 100),
                'ofi_threshold': trial.suggest_float('ofi_threshold', 0.3, 0.8, step=0.05),
                'elite_sl_mult': trial.suggest_float('elite_sl_mult', 0.9, 2.5, step=0.1),
                'elite_tp_mult': trial.suggest_float('elite_tp_mult', 1.5, 5.0, step=0.25),
                'exec_sl_mult':  trial.suggest_float('exec_sl_mult',  1.0, 2.5, step=0.1),
                'exec_tp_mult':  trial.suggest_float('exec_tp_mult',  1.5, 5.0, step=0.25),
                'risk_pct':      trial.suggest_float('risk_pct', 1.0, 3.5, step=0.1),
                'qty_tp1':       trial.suggest_float('qty_tp1', 0.35, 0.65, step=0.05),
                'signal_cooldown': trial.suggest_int('signal_cooldown', 2, 15),
            }
            try:
                sig, q = get_signals(df_train, p)
                if (sig != 0).sum() < min_trades: return -999
                tr, eq = run_backtest(df_train, sig, q, p)
                m = calc_metrics(tr, eq, days_period=days_train)
                return score_config(m, min_trades=min_trades)
            except:
                return -999

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=i*7, n_startup_trials=30)
        )
        study.optimize(objective, n_trials=trials, show_progress_bar=False)

        if study.best_value < -100:
            status = 'XX'
            cagr_test, wr_test, trades_test = 0, 0, 0
        else:
            p = study.best_params
            try:
                sig_t, q_t = get_signals(df_test, p)
                tr_t, eq_t = run_backtest(df_test, sig_t, q_t, p)
                m_t = calc_metrics(tr_t, eq_t, days_period=days_test)
                cagr_test   = m_t.get('cagr', 0) if m_t else 0
                wr_test     = m_t.get('winrate', 0) if m_t else 0
                trades_test = m_t.get('trades', 0) if m_t else 0
                status      = 'OK' if cagr_test > 0 else 'XX'
                save_run(tf, 'walk_forward', p, m_t, study.best_value)
            except:
                cagr_test, wr_test, trades_test, status = 0, 0, 0, 'ER'

        label = ts.strftime('%Y-%m')
        print(f'  {label:12} {days_train:>7}d {days_test:>7}d  '
              f'{trades_test:>4}  {wr_test:>5.1f}%  {cagr_test:>+7.1f}%  {status:>4}')

        results.append({
            'window': label,
            'cagr_test': cagr_test,
            'wr_test': wr_test,
            'trades_test': trades_test,
            'status': status,
        })

    # Resumen
    if results:
        pos = [r for r in results if r['cagr_test'] > 0]
        neg = [r for r in results if r['cagr_test'] <= 0]
        pct_pos = len(pos) / len(results) * 100
        avg_cagr_pos = np.mean([r['cagr_test'] for r in pos]) if pos else 0
        avg_cagr_all = np.mean([r['cagr_test'] for r in results])

        print(f'\n  {"="*50}')
        print(f'  Ventanas positivas: {len(pos)}/{len(results)} ({pct_pos:.0f}%)')
        print(f'  CAGR promedio OOS (positivas): {avg_cagr_pos:+.1f}%')
        print(f'  CAGR promedio OOS (todas):     {avg_cagr_all:+.1f}%')

        # Guardar resultados
        out = OUTPUT_DIR / 'models' / tf / 'walk_forward_v2.json'
        result = {
            'tf': tf, 'train_months': train_months, 'test_months': test_months,
            'windows': results,
            'summary': {
                'total_windows': len(results),
                'positive_windows': len(pos),
                'pct_positive': round(pct_pos, 1),
                'avg_cagr_positive': round(avg_cagr_pos, 2),
                'avg_cagr_all': round(avg_cagr_all, 2),
            }
        }
        with open(out, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'  [SAVED] {out.name}')
    print('='*65)
    return results


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--tf',     default='1h',  choices=['4h','1h','15m'])
    p.add_argument('--train',  type=int, default=6)
    p.add_argument('--test',   type=int, default=2)
    p.add_argument('--trials', type=int, default=150)
    args = p.parse_args()
    run_walk_forward(args.tf, args.train, args.test, trials=args.trials)
