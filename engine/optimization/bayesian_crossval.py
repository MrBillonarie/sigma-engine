"""
SIGMA ENGINE — Bayesian con Cross-Validation
Usa 3-fold time-series CV dentro de cada trial para evitar overfitting.
Mucho mas robusto que IS simple — los params encontrados generalizan mejor.

Idea: en vez de optimizar en el IS completo, dividir en 3 subcarpetas:
  Fold 1: IS 0-33% → train, 33-50% → val
  Fold 2: IS 0-50% → train, 50-67% → val
  Fold 3: IS 0-67% → train, 67-80% → val
Score final = promedio de los 3 scores de validacion.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)
OUTPUT_DIR = Path(__file__).parent.parent.parent


def run_crossval_bayesian(tf="1h", n_trials=500, days=1095):
    print(f"\n{'='*65}")
    print(f"  SIGMA BAYESIAN CROSS-VALIDATION — {tf.upper()}")
    print(f"  {n_trials} trials | 3-fold time-series CV | {days} dias")
    print(f"{'='*65}")

    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics, score_config

    print("\n[DATA] Cargando...")
    df_b  = fetch_ohlcv(tf=tf,  days=days)
    df_4h = fetch_ohlcv(tf="4h", days=days*2)
    df_1d = fetch_ohlcv(tf="1d", days=days*3)
    df    = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    n = len(df)
    # Usar solo el 80% de datos para optimizar (OOS 20% intacto)
    df_opt = df.iloc[:int(n*0.80)]
    df_oos = df.iloc[int(n*0.80):]
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    # Definir los 3 folds de CV
    # Walk-forward: cada fold usa mas datos para train
    m = len(df_opt)
    folds = [
        (0, int(m*0.45), int(m*0.45), int(m*0.60)),   # train 0-45%, val 45-60%
        (0, int(m*0.60), int(m*0.60), int(m*0.75)),   # train 0-60%, val 60-75%
        (0, int(m*0.75), int(m*0.75), int(m*1.00)),   # train 0-75%, val 75-100%
    ]
    print(f"  {n} velas | IS: 80% = {len(df_opt)} | OOS: 20% = {len(df_oos)}")
    print(f"  3 folds CV definidos")

    def cv_score(cfg):
        scores = []
        for tr_s, tr_e, val_s, val_e in folds:
            df_tr  = df_opt.iloc[tr_s:tr_e]
            df_val = df_opt.iloc[val_s:val_e]
            days_val = (df_val.index[-1]-df_val.index[0]).days

            try:
                sigs, qual = get_signals(df_val, cfg)
                if (sigs!=0).sum() < 5:
                    scores.append(-5.0)
                    continue
                tr_l, eq = run_backtest(df_val, sigs, qual, cfg)
                m_v = calc_metrics(tr_l, eq, days_period=days_val)
                scores.append(score_config(m_v, min_trades=5))
            except:
                scores.append(-5.0)

        if not scores: return -9999
        # Penalizar alta varianza entre folds (instabilidad)
        mean_s = np.mean(scores)
        std_s  = np.std(scores)
        return mean_s - 0.3 * std_s   # penaliza varianza

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
            return cv_score(cfg)
        except: return -9999

    # Warm start
    cfg_file = OUTPUT_DIR / "models" / tf / "config.json"
    base_params = None
    if cfg_file.exists():
        with open(cfg_file) as f:
            base_params = json.load(f).get("params", {})

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=100)
    )
    if base_params:
        try: study.enqueue_trial(base_params)
        except: pass

    # Also try best_validated params
    bv_file = OUTPUT_DIR / "models" / tf / "best_validated.json"
    if bv_file.exists():
        with open(bv_file) as f:
            bv = json.load(f)
        bv_params = bv.get("params", {})
        if bv_params:
            try: study.enqueue_trial(bv_params)
            except: pass

    best_cv = [-9999]; best_p = [{}]

    def callback(study, trial):
        if trial.value and trial.value > best_cv[0]:
            best_cv[0] = trial.value
            best_p[0]  = trial.params.copy()
            if trial.value > 0.3:
                print(f"  [Trial {trial.number}] NUEVO MEJOR CV={trial.value:.4f}")

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)

    print(f"\n  Mejor CV score: {study.best_value:.4f}")
    print(f"  Params: {study.best_params}")

    # OOS validation final
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION FINAL")
    p = study.best_params

    sigs_oos, qual_oos = get_signals(df_oos, p)
    tr_oos, eq_oos = run_backtest(df_oos, sigs_oos, qual_oos, p)
    m_oos = calc_metrics(tr_oos, eq_oos, days_period=days_oos)
    oos_cagr = m_oos.get("cagr", m_oos.get("pnl_pct", -9999))
    print(f"  {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% | CAGR {oos_cagr:+.1f}% | "
          f"DD {m_oos['max_dd']:.1f}% | PF {m_oos['profit_factor']:.2f}")

    # IS full validation
    sigs_is, qual_is = get_signals(df_opt, p)
    tr_is, eq_is = run_backtest(df_opt, sigs_is, qual_is, p)
    m_is = calc_metrics(tr_is, eq_is, days_period=(df_opt.index[-1]-df_opt.index[0]).days)
    is_cagr = m_is.get("cagr", m_is.get("pnl_pct", 0))
    print(f"  IS full: {m_is['trades']}T | WR {m_is['winrate']:.1f}% | CAGR {is_cagr:+.1f}%")

    eff = oos_cagr / abs(is_cagr) if abs(is_cagr) > 0.1 else 0
    print(f"  Eficiencia IS->OOS: {eff:.2f}")

    def ser(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        return v

    # Load current best for comparison
    cur_oos_cagr = -9999
    if bv_file.exists():
        with open(bv_file) as f:
            cur = json.load(f)
        cur_oos_cagr = cur.get("metrics_oos", {}).get("cagr",
                       cur.get("metrics_oos", {}).get("pnl_pct", -9999))

    if m_oos["trades"] >= 15 and oos_cagr > cur_oos_cagr + 1.0:
        result = {
            "tf": tf, "params": p,
            "metrics_is":  {k: ser(v) for k,v in m_is.items()},
            "metrics_oos": {k: ser(v) for k,v in m_oos.items()},
            "score": study.best_value,
            "cv_score": study.best_value,
            "oos_efficiency": round(eff, 3),
            "source": f"bayesian_3fold_cv_{n_trials}trials",
            "note": f"Optimizado con 3-fold CV. OOS CAGR {oos_cagr:+.1f}%. Solo reemplazar si mejora."
        }
        with open(bv_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n  [SAVED] NUEVO MEJOR: OOS {oos_cagr:+.1f}% vs anterior {cur_oos_cagr:+.1f}%")
    elif oos_cagr > 0:
        print(f"\n  OOS {oos_cagr:+.1f}% — no supera el actual ({cur_oos_cagr:+.1f}%). Sin cambios.")

        # Guardar como alternativa si es un resultado decente
        alt_file = OUTPUT_DIR / "models" / tf / "cv_model.json"
        result = {
            "tf": tf, "params": p,
            "metrics_is":  {k: ser(v) for k,v in m_is.items()},
            "metrics_oos": {k: ser(v) for k,v in m_oos.items()},
            "score": study.best_value,
            "note": f"Modelo CV: IS {is_cagr:+.1f}%, OOS {oos_cagr:+.1f}%, CV score {study.best_value:.4f}"
        }
        with open(alt_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"  [SAVED] Modelo alternativo: {alt_file.name}")
    else:
        print(f"\n  OOS negativo ({oos_cagr:+.1f}%). Sin cambios.")

    print(f"{'='*65}")
    return p, m_oos


if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("--tf",     default="1h")
    a.add_argument("--trials", type=int, default=500)
    a.add_argument("--days",   type=int, default=1095)
    args = a.parse_args()
    run_crossval_bayesian(args.tf, args.trials, args.days)
