"""
SIGMA REGIME OPTIMIZER
Optimiza parametros SEPARADOS para cada regimen de mercado:
  BULL   (RSI-W > 60): tendencias fuertes, momentum funciona
  RANGE  (40-60):      mercado lateral, mean reversion funciona
  BEAR   (RSI-W < 40): caidas, solo shorts o fuera

Por que mejora los resultados:
  Un solo set de params para 8.7 anos de BTC = compromiso suboptimo.
  En bull: quieres SL ancho y TP muy amplio (dejar correr tendencia)
  En range: quieres SL tight y TP rapido (reversiones cortas)
  En bear: quieres ignorar longs y tomar shorts agresivos

Resultado esperado: +30-50% mejor CAGR que params universales.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def get_regime_masks(df, rsi_bull=60, rsi_bear=40):
    """Clasifica cada barra segun regimen usando RSI semanal proxy."""
    close_w  = df['close'].resample('W').last().ffill()
    rsi_w    = rsi(close_w, 14)
    rsi_w_1h = rsi_w.reindex(df.index, method='ffill')

    bull_mask  = rsi_w_1h > rsi_bull
    bear_mask  = rsi_w_1h < rsi_bear
    range_mask = ~bull_mask & ~bear_mask

    return {
        'BULL':  bull_mask,
        'RANGE': range_mask,
        'BEAR':  bear_mask,
    }


def run_regime_optimizer(tf='1h', n_trials=300):
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics, score_config
    from core.database import save_run, init_db

    init_db()

    print(f'\n{"="*65}')
    print(f'  SIGMA REGIME OPTIMIZER — {tf.upper()}')
    print(f'  3 regimenes x {n_trials} trials | 8.7 anos IS/OOS')
    print(f'{"="*65}')

    # Cargar datos
    htf_map = {'1h': ('4h','1d'), '4h': ('1d','1d'), '15m': ('1h','4h')}
    h1, h2  = htf_map.get(tf, ('4h','1d'))
    df_b  = fetch_ohlcv(tf=tf, days=3200)
    df_h1 = fetch_ohlcv(tf=h1, days=3200)
    df_h2 = fetch_ohlcv(tf=h2, days=3200)
    df    = build_features(df_b, {h1: df_h1, h2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)

    n     = len(df)
    split = int(n * 0.80)
    df_is = df.iloc[:split]
    df_oos= df.iloc[split:]
    days_is  = (df_is.index[-1] - df_is.index[0]).days
    days_oos = (df_oos.index[-1] - df_oos.index[0]).days

    print(f'  {n:,} velas | IS: {days_is}d | OOS: {days_oos}d')

    masks_is  = get_regime_masks(df_is)
    masks_oos = get_regime_masks(df_oos)

    for regime, bull_pct in [(k, v.mean()*100) for k,v in masks_is.items()]:
        print(f'  {regime}: {bull_pct:.0f}% del tiempo IS')

    best_params = {}
    best_metrics_oos = {}

    for regime in ['BULL', 'RANGE', 'BEAR']:
        mask_is  = masks_is[regime]
        mask_oos = masks_oos[regime]

        pct_is = mask_is.mean() * 100
        if pct_is < 5:
            print(f'\n  [{regime}] Muy poco data ({pct_is:.0f}%), saltando')
            continue

        print(f'\n  [{regime}] Optimizando {n_trials} trials ({pct_is:.0f}% del IS)...')

        # Datos filtrados al regimen
        df_regime_is = df_is[mask_is]
        if len(df_regime_is) < 100:
            print(f'  [{regime}] Pocos datos, saltando')
            continue

        min_trades = max(int(len(df_regime_is) / 500), 5)

        def objective(trial):
            params = {
                'use_elite_ict': trial.suggest_categorical('use_elite_ict', [True, False]),
                'use_elite':     trial.suggest_categorical('use_elite',     [True, False]),
                'use_execute':   trial.suggest_categorical('use_execute',   [True, True, False]),
                'use_trend':     trial.suggest_categorical('use_trend',     [True, False]),
                'use_range':     trial.suggest_categorical('use_range',     [True if regime=='RANGE' else False, False]),
                'use_sess_b':    trial.suggest_categorical('use_sess_b',    [True, False]),
                'use_asia':      trial.suggest_categorical('use_asia',      [True, False]),
                'allow_friday':  trial.suggest_categorical('allow_friday',  [True, False]),
                'req_htf2':      trial.suggest_categorical('req_htf2',      [True, False]),
                'use_be':        trial.suggest_categorical('use_be',        [False, True]),
                'adx_min':       trial.suggest_int('adx_min', 10, 30),
                'hurst_t':       trial.suggest_float('hurst_t', 0.50, 0.65, step=0.01),
                'adx_t':         trial.suggest_int('adx_t', 15, 35),
                'hurst_r':       trial.suggest_float('hurst_r', 0.42, 0.53, step=0.01),
                'adx_r':         trial.suggest_int('adx_r', 10, 22),
                'temp_min':      trial.suggest_int('temp_min', 5, 25),
                'temp_max':      trial.suggest_int('temp_max', 70, 100),
                'ofi_threshold': trial.suggest_float('ofi_threshold', 0.3, 0.8, step=0.05),
                # SL/TP especificos por regimen
                'elite_sl_mult': trial.suggest_float('elite_sl_mult',
                    0.8 if regime=='RANGE' else 1.2,
                    1.8 if regime=='RANGE' else 3.0, step=0.1),
                'elite_tp_mult': trial.suggest_float('elite_tp_mult',
                    1.5 if regime=='RANGE' else 2.5,
                    4.0 if regime=='RANGE' else 7.0, step=0.25),
                'exec_sl_mult':  trial.suggest_float('exec_sl_mult',
                    0.8 if regime=='RANGE' else 1.2,
                    2.0 if regime=='RANGE' else 3.5, step=0.1),
                'exec_tp_mult':  trial.suggest_float('exec_tp_mult',
                    1.5 if regime=='RANGE' else 2.5,
                    4.0 if regime=='RANGE' else 8.0, step=0.25),
                'risk_pct':      trial.suggest_float('risk_pct', 1.0, 4.0, step=0.1),
                'qty_tp1':       trial.suggest_float('qty_tp1', 0.35, 0.65, step=0.05),
                'signal_cooldown': trial.suggest_int('signal_cooldown', 2, 15),
            }
            try:
                signals, quality = get_signals(df_regime_is, params)
                if (signals != 0).sum() < min_trades // 2:
                    return -999
                trades, equity = run_backtest(df_regime_is, signals, quality, params)
                m = calc_metrics(trades, equity, days_period=len(df_regime_is)//24)
                s = score_config(m, min_trades=min_trades)
                if m['trades'] >= min_trades // 2:
                    save_run(tf, f'regime_{regime.lower()}', params, m, s)
                return s
            except:
                return -999

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=hash(regime) % 100, n_startup_trials=50)
        )

        best_v = [-9999]
        def cb(study, trial):
            if trial.value and trial.value > best_v[0] and trial.value > 0.3:
                best_v[0] = trial.value
                print(f'    [T{trial.number}] score={trial.value:.4f}')

        study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

        if study.best_value < -100:
            print(f'  [{regime}] Sin resultado positivo')
            continue

        p = study.best_params
        best_params[regime] = p

        # Evaluar en OOS del mismo regimen
        df_regime_oos = df_oos[mask_oos] if mask_oos.sum() > 0 else df_oos
        days_reg_oos  = (df_regime_oos.index[-1] - df_regime_oos.index[0]).days if len(df_regime_oos) > 1 else 30
        try:
            sig_oos, q_oos = get_signals(df_regime_oos, p)
            tr_oos, eq_oos = run_backtest(df_regime_oos, sig_oos, q_oos, p)
            m_oos = calc_metrics(tr_oos, eq_oos, days_period=days_reg_oos)
            best_metrics_oos[regime] = m_oos

            sig_is_full, q_is_full = get_signals(df_regime_is, p)
            tr_is, eq_is = run_backtest(df_regime_is, sig_is_full, q_is_full, p)
            m_is = calc_metrics(tr_is, eq_is, days_period=days_is)

            cagr_is  = m_is.get('cagr', 0) if m_is else 0
            cagr_oos = m_oos.get('cagr', 0) if m_oos else 0
            wr_oos   = m_oos.get('winrate', 0) if m_oos else 0
            t_oos    = m_oos.get('trades', 0) if m_oos else 0
            dd_oos   = m_oos.get('max_dd', 0) if m_oos else 0

            print(f'  [{regime}] IS CAGR: {cagr_is:+.1f}% | OOS: {t_oos}T WR {wr_oos:.1f}% CAGR {cagr_oos:+.1f}% DD {dd_oos:.1f}%')
        except Exception as e:
            print(f'  [{regime}] Error OOS: {e}')

    # Guardar configuracion multi-regimen
    if best_params:
        out_dir = OUTPUT_DIR / 'models' / tf
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            'tf': tf,
            'type': 'regime_specific',
            'params_by_regime': best_params,
            'metrics_oos_by_regime': {
                k: {m: round(float(v), 4) if isinstance(v, (int,float)) else v
                    for m, v in met.items()}
                for k, met in best_metrics_oos.items()
            } if best_metrics_oos else {},
            'usage': (
                'Usar params_by_regime[BULL] cuando RSI-W > 60, '
                '[RANGE] cuando 40-60, [BEAR] cuando < 40'
            ),
            'note': f'Optimizado separadamente por regimen. {n_trials} trials x 3 regimenes.'
        }
        path = out_dir / 'regime_params.json'
        with open(path, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        print(f'\n  [SAVED] {path}')
        print(f'  Regimenes guardados: {list(best_params.keys())}')
    else:
        print('\n  Sin resultados suficientes')

    print('='*65)
    return best_params


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--tf', default='1h', choices=['4h','1h','15m'])
    p.add_argument('--trials', type=int, default=300)
    args = p.parse_args()
    run_regime_optimizer(args.tf, args.trials)
