"""
SIGMA ENGINE — Bayesian focusado en periodo reciente (2024-2025)
Optimiza sobre los ultimos ~18 meses donde el modelo tiene mejor performance.
Guarda solo si OOS CAGR > threshold del modelo actual.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
DAYS_RECENT = 540   # ~18 meses
SPLIT_IS    = 0.80


def run_recent_bayesian(tf="1h", n_trials=600):
    print(f"\n{'='*65}")
    print(f"  SIGMA BAYESIAN — PERIODO RECIENTE {tf.upper()}")
    print(f"  {n_trials} trials | Ultimos {DAYS_RECENT} dias (2024-2025)")
    print(f"{'='*65}")

    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics, score_config

    print("\n[DATA] Cargando...")
    df_b  = fetch_ohlcv(tf=tf,  days=DAYS_RECENT)
    df_4h = fetch_ohlcv(tf="4h", days=DAYS_RECENT*2)
    df_1d = fetch_ohlcv(tf="1d", days=DAYS_RECENT*3)
    df    = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    days_total = (df.index[-1]-df.index[0]).days
    n  = len(df)
    split = int(n * SPLIT_IS)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"  {n} velas | {days_total}d total")
    print(f"  IS: {df_is.index[0].strftime('%Y-%m-%d')} -> {df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> {df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    # Cargar mejor modelo actual para comparar
    best_file = OUTPUT_DIR / "models" / tf / "best_validated.json"
    current_oos_cagr = -9999
    if best_file.exists():
        with open(best_file) as f:
            cur = json.load(f)
        oos_m = cur.get("metrics_oos", {})
        current_oos_cagr = oos_m.get("cagr", oos_m.get("pnl_pct", -9999))
        print(f"  Modelo actual OOS CAGR: {current_oos_cagr:+.1f}%")

    def objective(trial):
        cfg = {
            "use_execute":     trial.suggest_categorical("use_execute",   [True, True, False]),
            "use_trend":       trial.suggest_categorical("use_trend",     [True, True, False]),
            "use_range":       trial.suggest_categorical("use_range",     [True, False]),
            "use_watch":       trial.suggest_categorical("use_watch",     [False, True]),
            "use_sess_b":      trial.suggest_categorical("use_sess_b",    [True, True, False]),
            "use_asia":        trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":    trial.suggest_categorical("allow_friday",  [True, False]),
            "req_htf2":        trial.suggest_categorical("req_htf2",      [True, True, False]),
            "use_be":          trial.suggest_categorical("use_be",        [True, True, False]),
            "adx_min":         trial.suggest_int("adx_min",        12, 30),
            "hurst_t":         trial.suggest_float("hurst_t",      0.50, 0.62, step=0.01),
            "adx_t":           trial.suggest_int("adx_t",          18, 32),
            "hurst_r":         trial.suggest_float("hurst_r",      0.44, 0.52, step=0.01),
            "adx_r":           trial.suggest_int("adx_r",          14, 24),
            "temp_min":        trial.suggest_int("temp_min",        5, 25),
            "temp_max":        trial.suggest_int("temp_max",        75, 100),
            "ofi_threshold":   trial.suggest_float("ofi_threshold", 0.35, 0.75, step=0.05),
            "elite_sl_mult":   trial.suggest_float("elite_sl_mult", 1.0, 2.5, step=0.1),
            "elite_tp_mult":   trial.suggest_float("elite_tp_mult", 1.5, 5.0, step=0.25),
            "exec_sl_mult":    trial.suggest_float("exec_sl_mult",  1.2, 2.5, step=0.1),
            "exec_tp_mult":    trial.suggest_float("exec_tp_mult",  1.5, 4.5, step=0.25),
            "risk_pct":        trial.suggest_float("risk_pct",      0.3, 1.5, step=0.1),
            "qty_tp1":         trial.suggest_float("qty_tp1",       0.35, 0.65, step=0.05),
            "signal_cooldown": trial.suggest_int("signal_cooldown", 2, 20),
        }
        try:
            sigs, qual = get_signals(df_is, cfg)
            if (sigs != 0).sum() < 8: return -999
            tr, eq = run_backtest(df_is, sigs, qual, cfg)
            m = calc_metrics(tr, eq, days_period=days_is)
            return score_config(m, min_trades=8)
        except: return -999

    # Warm start desde config actual
    cfg_file = OUTPUT_DIR / "models" / tf / "config.json"
    base_params = None
    if cfg_file.exists():
        with open(cfg_file) as f:
            base_params = json.load(f).get("params", {})

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=80)
    )
    if base_params:
        try: study.enqueue_trial(base_params)
        except: pass

    best_score  = [-9999]; best_params = [{}]

    def callback(study, trial):
        if trial.value and trial.value > best_score[0]:
            best_score[0] = trial.value
            best_params[0] = trial.params.copy()
            if trial.value > 0.4:
                print(f"  [Trial {trial.number}] NUEVO MEJOR IS score={trial.value:.4f}")

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)

    print(f"\n  Mejor IS score: {study.best_value:.4f}")
    print(f"  Params: {study.best_params}")

    # OOS validation
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION (periodo reciente)")
    p = study.best_params
    sigs_oos, qual_oos = get_signals(df_oos, p)
    tr_oos, eq_oos = run_backtest(df_oos, sigs_oos, qual_oos, p)
    m_oos = calc_metrics(tr_oos, eq_oos, days_period=days_oos)
    oos_cagr = m_oos.get("cagr", m_oos.get("pnl_pct", -9999))

    print(f"  {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% | CAGR {oos_cagr:+.1f}% | "
          f"DD {m_oos['max_dd']:.1f}% | PF {m_oos['profit_factor']:.2f}")

    # IS metrics del ganador
    sigs_is, qual_is = get_signals(df_is, p)
    tr_is, eq_is = run_backtest(df_is, sigs_is, qual_is, p)
    m_is = calc_metrics(tr_is, eq_is, days_period=days_is)
    is_cagr = m_is.get("cagr", m_is.get("pnl_pct", 0))
    eff = oos_cagr / abs(is_cagr) if abs(is_cagr) > 0.1 else 0
    print(f"  IS: {m_is['trades']}T | WR {m_is['winrate']:.1f}% | CAGR {is_cagr:+.1f}%")
    print(f"  Eficiencia IS->OOS: {eff:.2f}")

    def ser(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        return v

    # Guardar solo si mejora OOS vs actual
    if m_oos["trades"] >= 10 and oos_cagr > current_oos_cagr + 1.0:
        result = {
            "tf": tf,
            "params": p,
            "metrics_is":  {k: ser(v) for k,v in m_is.items()},
            "metrics_oos": {k: ser(v) for k,v in m_oos.items()},
            "score": study.best_value,
            "oos_efficiency": round(eff, 3),
            "source": f"bayesian_recent_{DAYS_RECENT}d",
            "note": f"Optimizado en periodo reciente {DAYS_RECENT}d. Solo reemplazar si OOS CAGR > {oos_cagr:.1f}%"
        }
        with open(best_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n  [SAVED] Nuevo modelo: OOS CAGR {oos_cagr:+.1f}% (mejora vs {current_oos_cagr:+.1f}%)")
        print(f"  [SAVED] {best_file}")
    elif oos_cagr > 0:
        print(f"\n  Sin mejora vs modelo actual ({current_oos_cagr:+.1f}% -> {oos_cagr:+.1f}%).")
    else:
        print(f"\n  OOS negativo ({oos_cagr:+.1f}%). Sin cambios.")

    print(f"{'='*65}")
    return p, m_oos


if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("--tf",     default="1h")
    a.add_argument("--trials", type=int, default=600)
    args = a.parse_args()
    run_recent_bayesian(args.tf, args.trials)
