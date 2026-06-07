"""
SIGMA ENGINE — Bayesian Optimizer (Optuna)
Aprende de cada iteracion para explorar mejor el espacio de parametros.
NO empieza desde cero — usa el historial de la DB como prior.

Diferencia vs random search:
  Random: explora al azar, no aprende
  Bayesian: aprende que zonas dan mejores scores y va ahi
  Resultado: encuentra mejores configs con 3-5x menos iteraciones
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import optuna
import json
import warnings
import numpy as np
from pathlib import Path
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from core.database import save_run, get_best, get_top_runs, get_param_importance, init_db

OUTPUT_DIR = Path(__file__).parent.parent.parent

# ─── CONFIG POR TF ────────────────────────────────────────────────────────────
TF_SETTINGS = {
    "1m":  {"days": 30,   "min_trades": 150, "n_trials": 300,  "bars_year": 525960},
    "5m":  {"days": 180,  "min_trades": 60,  "n_trials": 400,  "bars_year": 105192},
    "15m": {"days": 730,  "min_trades": 35,  "n_trials": 500,  "bars_year": 35040},
    "1h":  {"days": 1095, "min_trades": 18,  "n_trials": 400,  "bars_year": 8760},
    "4h":  {"days": 1500, "min_trades": 12,  "n_trials": 300,  "bars_year": 2190},
    "1d":  {"days": 1500, "min_trades": 8,   "n_trials": 200,  "bars_year": 365},
}


def run_bayesian_search(tf="15m", n_trials=None, df=None, htf_dict=None,
                        study_name=None, resume=True):
    """
    Bayesian optimization con Optuna.
    Si resume=True, carga el estudio anterior y continua desde donde quedo.
    """
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics, score_config

    tf_cfg   = TF_SETTINGS.get(tf, TF_SETTINGS["15m"])
    n_trials = n_trials or tf_cfg["n_trials"]
    min_tr   = tf_cfg["min_trades"]

    print(f"\n{'='*65}")
    print(f"  SIGMA BAYESIAN OPTIMIZER — {tf.upper()}")
    print(f"  {n_trials} trials | Aprende del historial en DB")
    print(f"{'='*65}")

    # Cargar datos si no se proveen
    if df is None:
        print(f"[DATA] Cargando {tf}...")
        htf_map = {"15m":("1h","4h"), "1h":("4h","1d"),
                   "4h":("1d","1d"), "1m":("5m","15m"),
                   "5m":("15m","1h"), "1d":("1d","1d")}
        h1, h2 = htf_map.get(tf, ("1h","4h"))
        df_base = fetch_ohlcv(tf=tf, days=tf_cfg["days"])
        df_htf1 = fetch_ohlcv(tf=h1, days=tf_cfg["days"]*2)
        df_htf2 = fetch_ohlcv(tf=h2, days=tf_cfg["days"]*3)
        df      = build_features(df_base, {h1: df_htf1, h2: df_htf2})
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        print(f"  {len(df)} velas listas")

    # Analizar importancia de parametros del historial
    importance = get_param_importance(tf, n=100)
    if importance:
        top3 = list(importance.items())[:3]
        print(f"  Params mas importantes (historial): {top3}")

    # Cargar mejor config conocida como warm start
    known_best = get_best(tf)
    if known_best and resume:
        print(f"  Warm start desde historial: score={known_best['score']:.4f}")

    days = (df.index[-1] - df.index[0]).days

    # ── Funcion objetivo para Optuna ──────────────────────────────────────────
    def objective(trial):
        params = {
            # ── Tipos de señal activos ──────────────────────────────────────
            "use_elite_ict": trial.suggest_categorical("use_elite_ict", [True, True, False]),
            "use_elite":     trial.suggest_categorical("use_elite",     [True, True, False]),
            "use_execute":   trial.suggest_categorical("use_execute",   [True, True, False]),
            "use_trend":     trial.suggest_categorical("use_trend",     [True, True, False]),
            "use_range":     trial.suggest_categorical("use_range",     [True, False]),
            # ── Sesiones ─────────────────────────────────────────────────────
            "use_sess_b":    trial.suggest_categorical("use_sess_b",    [True, True, False]),
            "use_asia":      trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":  trial.suggest_categorical("allow_friday",  [True, False]),
            "allow_monday":  trial.suggest_categorical("allow_monday",  [False, False, True]),
            "req_htf2":      trial.suggest_categorical("req_htf2",      [True, True, False]),
            # ── Filtros cuantitativos ────────────────────────────────────────
            "adx_min":         trial.suggest_int("adx_min",     10, 30),
            "hurst_t":         trial.suggest_float("hurst_t",   0.50, 0.65, step=0.01),
            "adx_t":           trial.suggest_int("adx_t",       18, 40),
            "hurst_r":         trial.suggest_float("hurst_r",   0.42, 0.53, step=0.01),
            "adx_r":           trial.suggest_int("adx_r",       12, 25),
            "temp_min":        trial.suggest_int("temp_min",    5,  22),
            "temp_max":        trial.suggest_int("temp_max",    72, 98),
            # ── SL/TP por calidad (formato correcto para _get_sl_tp) ─────────
            "sl_elite_ict":    trial.suggest_float("sl_elite_ict", 0.9, 2.0, step=0.1),
            "tp_elite_ict":    trial.suggest_float("tp_elite_ict", 1.5, 4.5, step=0.25),
            "sl_elite":        trial.suggest_float("sl_elite",     0.9, 2.0, step=0.1),
            "tp_elite":        trial.suggest_float("tp_elite",     1.5, 4.5, step=0.25),
            "sl_execute":      trial.suggest_float("sl_execute",   1.0, 2.5, step=0.1),
            "tp_execute":      trial.suggest_float("tp_execute",   1.5, 4.0, step=0.25),
            # ── Gestión de posición ──────────────────────────────────────────
            "use_trail":       trial.suggest_categorical("use_trail",   [True, True, False]),
            "trail_mult":      trial.suggest_float("trail_mult",        0.8, 2.5, step=0.1),
            "max_bars_in_trade": trial.suggest_categorical("max_bars_in_trade", [0, 0, 10, 20, 30, 40]),
            # ── Risk / sizing ────────────────────────────────────────────────
            "risk_pct":        trial.suggest_float("risk_pct",     0.3, 1.5, step=0.1),
            "qty_tp1":         trial.suggest_float("qty_tp1",      0.35, 0.65, step=0.05),
            # ── Cooldown ─────────────────────────────────────────────────────
            "signal_cooldown": trial.suggest_int("signal_cooldown", 2, 20),
            # ── Filtros de futuros (si hay datos) ────────────────────────────
            "use_funding_filter": trial.suggest_categorical("use_funding_filter", [True, False]),
            "use_oi_filter":      trial.suggest_categorical("use_oi_filter",      [True, False]),
        }

        try:
            signals, quality = get_signals(df, params)
            if (signals != 0).sum() < min_tr // 2:
                return -999
            trades, equity = run_backtest(df, signals, quality, params)
            m = calc_metrics(trades, equity, days_period=days)
            s = score_config(m, min_trades=min_tr)

            # Guardar en DB (solo si tiene minimo de trades)
            if m["trades"] >= min_tr // 2:
                save_run(tf, "bayesian", params, m, s)

            return s
        except Exception:
            return -999

    # ── Crear o cargar estudio ────────────────────────────────────────────────
    study_name = study_name or f"sigma_{tf}_{len(df)}bars"
    storage    = f"sqlite:///{OUTPUT_DIR}/models/sigma_optuna.db"

    if resume:
        try:
            study = optuna.load_study(study_name=study_name, storage=storage)
            prev  = len(study.trials)
            print(f"  Cargando estudio existente: {prev} trials previos")
        except Exception:
            study = optuna.create_study(
                study_name=study_name, storage=storage,
                direction="maximize",
                sampler=optuna.samplers.TPESampler(seed=42)
            )
            prev = 0
    else:
        study = optuna.create_study(
            study_name=study_name, storage=storage,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            load_if_exists=True
        )
        prev = 0

    # Warm start: agregar mejores configs conocidas como hints
    if known_best and known_best.get("params") and prev == 0:
        p = known_best["params"]
        valid_p = {}
        for k, v in p.items():
            if k in study.sampler._param_configs if hasattr(study.sampler, "_param_configs") else {}:
                valid_p[k] = v
        if valid_p:
            try:
                study.enqueue_trial(valid_p)
            except Exception:
                pass

    # ── Callbacks ─────────────────────────────────────────────────────────────
    best_score_so_far = [known_best["score"] if known_best else -9999]

    def callback(study, trial):
        if trial.value and trial.value > best_score_so_far[0]:
            best_score_so_far[0] = trial.value
            p = trial.params
            # Recalcular metrics para mostrar
            try:
                sigs, qual = get_signals(df, p)
                trs, eq    = run_backtest(df, sigs, qual, p)
                m          = calc_metrics(trs, eq, days_period=days)
                print(f"  [Trial {trial.number}] NUEVO MEJOR: "
                      f"{m['trades']}T ({m.get('trades_month',0):.1f}T/mes) | "
                      f"WR {m['winrate']:.1f}% | CAGR {m.get('cagr',m['pnl_pct']):+.1f}%/año | "
                      f"PF {m['profit_factor']:.2f} | DD {m['max_dd']:.1f}%")
            except Exception:
                print(f"  [Trial {trial.number}] NUEVO MEJOR score: {trial.value:.4f}")

    print(f"\n[OPTUNA] Corriendo {n_trials} trials...\n")
    study.optimize(objective, n_trials=n_trials, callbacks=[callback],
                   show_progress_bar=False, n_jobs=1)

    # ── Resultados ────────────────────────────────────────────────────────────
    best_trial = study.best_trial
    best_params = best_trial.params

    print(f"\n{'='*65}")
    print(f"  RESULTADO BAYESIAN {tf.upper()}")
    print(f"{'='*65}")
    print(f"  Trials totales (historico): {len(study.trials)}")
    print(f"  Mejor score: {best_trial.value:.4f}")

    # Calcular metricas finales del ganador
    try:
        sigs, qual = get_signals(df, best_params)
        trs, eq    = run_backtest(df, sigs, qual, best_params)
        m_best     = calc_metrics(trs, eq, days_period=days)
        print(f"  Trades: {m_best['trades']} ({m_best.get('trades_month',0):.1f}T/mes)")
        print(f"  WR: {m_best['winrate']:.1f}%")
        print(f"  CAGR: {m_best.get('cagr', m_best['pnl_pct']):+.1f}%/año")
        print(f"  Sharpe: {m_best['sharpe']:.2f}")
        print(f"  MaxDD: {m_best['max_dd']:.1f}%")
        print(f"  PF: {m_best['profit_factor']:.2f}")

        # Guardar en modelos solo si mejora el actual
        model_dir = OUTPUT_DIR / "models" / tf
        model_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = model_dir / "config.json"
        cur_score = -9999
        if cfg_path.exists():
            try:
                with open(cfg_path) as _f: cur_score = json.load(_f).get("score", -9999)
            except: pass
        if best_trial.value > cur_score:
            with open(cfg_path, "w") as f:
                json.dump({
                    "tf": tf, "params": best_params,
                    "metrics": {k: round(v,4) if isinstance(v,float) else v
                                for k,v in m_best.items()},
                    "score": best_trial.value,
                    "study": study_name,
                    "trials_total": len(study.trials),
                }, f, indent=2)
            print(f"  [SAVED] models/{tf}/config.json (score {best_trial.value:.4f} > {cur_score:.4f})")
        else:
            print(f"  [SKIP] Sin mejora: {best_trial.value:.4f} <= actual {cur_score:.4f}")

    except Exception as e:
        print(f"  Error calculando metricas finales: {e}")
        m_best = {}

    # Importancia de parametros (Optuna)
    try:
        imp = optuna.importance.get_param_importances(study)
        print(f"\n  Top 5 parametros mas importantes:")
        for i, (k, v) in enumerate(list(imp.items())[:5], 1):
            print(f"  {i}. {k}: {v:.3f}")
    except Exception:
        pass

    return best_params, m_best, study


def compare_with_random(tf, bayesian_m, random_csv=None):
    """Compara resultados Bayesian vs Random Search."""
    print(f"\n  BAYESIAN vs RANDOM SEARCH ({tf}):")
    print(f"  {'Metrica':<20} {'Bayesian':>12} {'Random':>12}")
    print(f"  {'-'*46}")

    rand_m = {}
    if random_csv and os.path.exists(random_csv):
        import pandas as pd
        df_r = pd.read_csv(random_csv)
        if not df_r.empty:
            best_r = df_r.sort_values("score", ascending=False).iloc[0]
            rand_m = {"trades": best_r.get("trades",0),
                      "winrate": best_r.get("winrate",0),
                      "cagr": best_r.get("cagr", best_r.get("pnl_pct",0)),
                      "profit_factor": best_r.get("profit_factor",0)}

    for key, fmt in [("trades","%d"),("winrate","%.1f%%"),("cagr","%+.1f%%"),("profit_factor","%.2f")]:
        b = bayesian_m.get(key, 0)
        r = rand_m.get(key, "N/A")
        r_str = fmt % r if isinstance(r, (int,float)) else r
        print(f"  {key:<20} {fmt%b:>12} {r_str:>12}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="15m")
    parser.add_argument("--trials", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    params, metrics, study = run_bayesian_search(
        tf=args.tf,
        n_trials=args.trials,
        resume=not args.no_resume
    )
