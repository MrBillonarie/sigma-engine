"""
SIGMA 4H — Aggressive Optimizer
Target: CAGR 5-8% con riesgo controlado (DD < 15%)
Diferencia vs bayesian_search.py:
  - risk_pct: 1.5-4.0% (vs 0.3-1.5%)
  - Score reformulado: premia CAGR 5-8% mas agresivamente
  - Permite mas trades (relaja filtros de frecuencia)
  - min_trades: 25 (razonable para 4H en 8.7yr)
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

from core.database import save_run, get_best, init_db

OUTPUT_DIR = Path(__file__).parent.parent.parent
MIN_TRADES = 25
N_TRIALS   = 600

STUDY_NAME = "sigma_4h_aggressive_v1"


def score_4h_target(m, target_cagr_lo=5.0, target_cagr_hi=8.0):
    """
    Score orientado a CAGR 5-8%, equilibrando riesgo.
    Penaliza CAGR < 2% y CAGR > 15% (probable overfit).
    Penaliza DD < -20% (demasiado riesgo para cuenta real).
    """
    if m["trades"] < MIN_TRADES:
        return -9999 + m["trades"]

    t_month = m.get("trades_month", 0)
    if t_month < 0.4:
        return -4000 + m["trades"]

    cagr   = m.get("cagr", m["pnl_pct"])
    dd     = m.get("max_dd", -99)
    wr     = m["winrate"]
    pf     = m["profit_factor"]
    sharpe = m["sharpe"]

    # Rechazar configs que pierden o con DD catastrofico
    if cagr < 0:
        return -1000 + cagr
    if dd < -25:
        return -2000

    # Normalizar CAGR: bonus si en rango 5-8%, penalizar fuera
    if cagr < 2.0:
        cagr_score = cagr / 2.0 * 0.10   # zona mala
    elif cagr < target_cagr_lo:
        cagr_score = 0.10 + (cagr - 2) / (target_cagr_lo - 2) * 0.20
    elif cagr <= target_cagr_hi:
        cagr_score = 0.30 + (cagr - target_cagr_lo) / (target_cagr_hi - target_cagr_lo) * 0.25  # sweet spot
    elif cagr <= 15:
        cagr_score = 0.55 + (cagr - target_cagr_hi) / (15 - target_cagr_hi) * 0.05  # ok pero sospechoso
    else:
        cagr_score = 0.55 - (cagr - 15) * 0.05  # muy alto = overfit

    # DD score: optimo -5 a -10%
    dd_abs = abs(dd)
    if dd_abs < 3:
        dd_score = 0.15   # demasiado bajo = pocas trades o trampa
    elif dd_abs <= 10:
        dd_score = 0.25   # rango ideal
    elif dd_abs <= 15:
        dd_score = 0.18   # aceptable
    elif dd_abs <= 20:
        dd_score = 0.10   # alto
    else:
        dd_score = 0.0    # inaceptable

    # Calidad de señal
    wr_score    = max(0, (wr - 50) / 30) * 0.15    # 50-80% -> 0-0.15
    pf_score    = min(pf / 3.0, 1.0) * 0.10        # PF 0-3 -> 0-0.10
    sharpe_score= max(min(sharpe / 3.0, 1.0), 0) * 0.08  # Sharpe 0-3 -> 0-0.08

    # Bonus por frecuencia razonable para 4H (1-3 trades/mes)
    freq_bonus = 0.03 if 0.8 <= t_month <= 3.0 else 0.01

    return cagr_score + dd_score + wr_score + pf_score + sharpe_score + freq_bonus


def run():
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    init_db()

    print("\n" + "="*65)
    print("  SIGMA 4H — AGGRESSIVE OPTIMIZER")
    print("  Target: CAGR 5-8% | risk_pct 1.5-4% | 600 trials")
    print("="*65)

    print("[DATA] Cargando 4H (8.7 anos)...")
    df_4h = fetch_ohlcv(tf="4h", days=3200)
    df_1d = fetch_ohlcv(tf="1d", days=6400)
    df    = build_features(df_4h, {"1d": df_1d, "1d2": df_1d})
    df.dropna(subset=["close", "atr", "ema50"], inplace=True)
    days  = (df.index[-1] - df.index[0]).days
    print(f"  {len(df)} velas | {days} dias ({days/365.25:.1f} anos)")

    # Cargar mejor config existente como referencia
    known = get_best("4h")
    if known:
        print(f"  Mejor existente: score={known.get('score',0):.4f} | "
              f"WR={known.get('winrate',0):.1f}% | "
              f"CAGR={known.get('cagr',0):+.1f}%")

    def objective(trial):
        params = {
            "use_elite_ict": trial.suggest_categorical("use_elite_ict", [True, True, False]),
            "use_elite":     trial.suggest_categorical("use_elite",     [True, True, False]),
            "use_execute":   trial.suggest_categorical("use_execute",   [True, True, False]),
            "use_trend":     trial.suggest_categorical("use_trend",     [True, False]),
            "use_range":     trial.suggest_categorical("use_range",     [True, False]),
            "use_sess_b":    trial.suggest_categorical("use_sess_b",    [True, False]),
            "use_asia":      trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":  trial.suggest_categorical("allow_friday",  [True, False]),
            "allow_monday":  trial.suggest_categorical("allow_monday",  [False, False, True]),
            "req_htf2":      trial.suggest_categorical("req_htf2",      [True, True, False]),
            "adx_min":       trial.suggest_int("adx_min",   8,  25),
            "hurst_t":       trial.suggest_float("hurst_t", 0.48, 0.65, step=0.01),
            "adx_t":         trial.suggest_int("adx_t",    15,  38),
            "hurst_r":       trial.suggest_float("hurst_r", 0.40, 0.54, step=0.01),
            "adx_r":         trial.suggest_int("adx_r",    10,  24),
            "temp_min":      trial.suggest_int("temp_min",  5,  22),
            "temp_max":      trial.suggest_int("temp_max", 72,  98),
            "sl_elite_ict":  trial.suggest_float("sl_elite_ict", 0.8, 2.5, step=0.1),
            "tp_elite_ict":  trial.suggest_float("tp_elite_ict", 1.5, 5.0, step=0.25),
            "sl_elite":      trial.suggest_float("sl_elite",     0.8, 2.5, step=0.1),
            "tp_elite":      trial.suggest_float("tp_elite",     1.5, 5.0, step=0.25),
            "sl_execute":    trial.suggest_float("sl_execute",   1.0, 3.0, step=0.1),
            "tp_execute":    trial.suggest_float("tp_execute",   1.5, 5.0, step=0.25),
            "use_trail":     trial.suggest_categorical("use_trail", [True, True, False]),
            "trail_mult":    trial.suggest_float("trail_mult",   0.8, 3.0, step=0.1),
            "max_bars_in_trade": trial.suggest_categorical("max_bars_in_trade", [0, 0, 20, 40, 60]),
            # CLAVE: risk_pct alto para target 5-8% CAGR
            "risk_pct":      trial.suggest_float("risk_pct", 1.0, 4.0, step=0.1),
            "qty_tp1":       trial.suggest_float("qty_tp1",  0.30, 0.65, step=0.05),
            "signal_cooldown": trial.suggest_int("signal_cooldown", 2, 15),
            "use_funding_filter": trial.suggest_categorical("use_funding_filter", [True, False]),
            "use_oi_filter":      trial.suggest_categorical("use_oi_filter",      [True, False]),
        }

        try:
            signals, quality = get_signals(df, params)
            if (signals != 0).sum() < MIN_TRADES // 2:
                return -999

            trades, equity = run_backtest(df, signals, quality, params)
            m = calc_metrics(trades, equity, days_period=days)
            s = score_4h_target(m)

            if m["trades"] >= MIN_TRADES // 2:
                save_run("4h", "bayesian_aggressive", params, m, s)

            return s
        except Exception:
            return -999

    storage = f"sqlite:///{OUTPUT_DIR}/models/sigma_optuna.db"
    try:
        study = optuna.load_study(study_name=STUDY_NAME, storage=storage)
        print(f"  Cargando estudio: {len(study.trials)} trials previos")
    except Exception:
        study = optuna.create_study(
            study_name=STUDY_NAME, storage=storage,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=77, n_startup_trials=80)
        )

    # Warm start desde la mejor config conservadora conocida
    if known and known.get("params") and len(study.trials) == 0:
        warm_p = dict(known["params"])
        warm_p["risk_pct"] = 2.0  # escalar para target agresivo
        try:
            study.enqueue_trial(warm_p)
        except Exception:
            pass

    best_seen = [-9999]

    def cb(study, trial):
        if trial.value and trial.value > best_seen[0]:
            best_seen[0] = trial.value
            p = trial.params
            try:
                sigs, qual = get_signals(df, p)
                trs, eq    = run_backtest(df, sigs, qual, p)
                m          = calc_metrics(trs, eq, days_period=days)
                print(f"  [Trial {trial.number}] MEJOR: "
                      f"{m['trades']}T ({m.get('trades_month',0):.1f}T/mes) | "
                      f"WR {m['winrate']:.1f}% | CAGR {m.get('cagr',0):+.1f}%/a | "
                      f"DD {m['max_dd']:.1f}% | risk={p['risk_pct']:.1f}%")
            except Exception:
                print(f"  [Trial {trial.number}] nuevo mejor score: {trial.value:.4f}")

    study.optimize(objective, n_trials=N_TRIALS, callbacks=[cb], n_jobs=1)

    # Resultado final
    bt = study.best_trial
    p  = bt.params

    sigs, qual = get_signals(df, p)
    trs, eq    = run_backtest(df, sigs, qual, p)
    m          = calc_metrics(trs, eq, days_period=days)

    print("\n" + "="*65)
    print("  RESULTADO 4H AGGRESSIVE")
    print("="*65)
    print(f"  Trades: {m['trades']} ({m.get('trades_month',0):.1f}T/mes)")
    print(f"  WR: {m['winrate']:.1f}%")
    print(f"  CAGR: {m.get('cagr',0):+.1f}%/ano")
    print(f"  Sharpe: {m['sharpe']:.2f}")
    print(f"  MaxDD: {m['max_dd']:.1f}%")
    print(f"  PF: {m['profit_factor']:.2f}")
    print(f"  risk_pct: {p['risk_pct']:.1f}%")
    print(f"  Score: {bt.value:.4f}")

    # Guardar si mejora la config conservadora (solo si CAGR >= 3%)
    cagr_final = m.get("cagr", 0)
    if cagr_final >= 3.0:
        out_dir = OUTPUT_DIR / "models" / "4h"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "tf": "4h",
            "mode": "aggressive",
            "params": p,
            "metrics": {k: round(float(v), 4) if isinstance(v, (int, float)) else v
                        for k, v in m.items()},
            "score": bt.value,
            "study": STUDY_NAME,
            "trials_total": len(study.trials),
            "target": "CAGR 5-8%",
        }
        path = out_dir / "config_aggressive.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  [SAVED] {path}")
    else:
        print(f"  [SKIP] CAGR {cagr_final:.1f}% < 3% — no guardado")

    print("="*65)


if __name__ == "__main__":
    run()
