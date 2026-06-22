#!/usr/bin/env python3
"""Fase 2 del plan champions_regime (2026-06-21): busqueda Optuna DEDICADA
para un candidato countertrend especifico (long-en-bear o short-en-bull),
ya identificado como prometedor por la Fase 1 (countertrend_diagnostic.json).

Un solo job = un solo (symbol, tf, strategy, regime). Pensado para correr
como subprocess independiente, lanzado por countertrend_trainer.py, con el
mismo prlimit/aislamiento de RAM que usa continuous_trainer.py para el
entrenamiento normal.

No toca optimize_strategy() (la funcion normal, usada por TODO el sistema) --
duplica un objective() simplificado, reutilizando build_search_space/backtest/
metrics/score SIN modificarlos, con su propio study_name y storage SQLite
aislados (countertrend_{regime} suffix) para que jamas se mezcle con los
estudios agregados existentes ni con el warm-start normal.
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, "/opt/sigma")
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

from engine.optimization.asset_pipeline import (
    load_asset_csv, fetch_asset, add_features, build_search_space,
    backtest, metrics, score, SIG_FN, SIG_FN_SHORT,
)
from utils.regime_backtest import (
    regime_tagged_backtest, contiguous_segments, filter_segments_by_duration,
    trades_per_segment, segment_summary, regime_days,
)
from utils.quant import selection_bias_test

MODELS_DIR = Path("/opt/sigma/models")
OUT_DIR = Path("/opt/sigma/models/countertrend")
CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "BNB", "LTC"}
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
MIN_SEGMENT_DURATION_DAYS = 14


def resolve_fn(name):
    return SIG_FN.get(name) or SIG_FN_SHORT.get(name)


def load_price_df(asset, tf):
    if asset in CRYPTO_ASSETS:
        df = fetch_asset(f"{asset}/USDT", tf=tf)
        df.index.name = "timestamp"
        return df
    p1 = MODELS_DIR / f"data_{asset}_{tf}_max.csv"
    if p1.exists():
        return load_asset_csv(str(p1))
    return None


def run(symbol, tf, strategy, regime, n_trials):
    asset = symbol.split("/")[0].upper()
    sig_fn = resolve_fn(strategy)
    if sig_fn is None:
        print(f"[countertrend_objective] sin funcion sig_ para {strategy}", flush=True)
        return

    df = load_price_df(asset, tf)
    if df is None or len(df) < 300:
        print(f"[countertrend_objective] sin datos de precio para {asset}/{tf}", flush=True)
        return
    df = add_features(df)

    tradeable_col = "tradeable_long" if regime == "bear" else "tradeable_short"
    target_col = f"regime_{regime}"
    df_override = df.copy()
    df_override[tradeable_col] = True
    mask = df_override[target_col]
    days_in_regime = regime_days(df_override, target_col, TF_MINUTES[tf])

    def objective(trial):
        p = build_search_space(trial, strategy)
        rp = trial.suggest_float("risk_pct", 2.0, 5.0, step=0.1)
        try:
            sig, sl, tp = sig_fn(df_override, p)
            sig = sig.where(mask, 0)
            if (sig != 0).sum() < 15:
                return -9999
            dt, eq = backtest(df_override, sig, sl, tp, rp)
            m = metrics(dt, eq, days_in_regime, min_t=10)
            return float(score(m, min_t=10)) if m else -9999
        except Exception:
            return -9999

    def cb(study, trial):
        if trial.number > 0 and trial.number % 30 == 0:
            best = study.best_value if study.best_value and study.best_value > -9999 else 0
            print(f"  [{symbol} {tf} {strategy} ct-{regime}] T{trial.number}/{n_trials} | mejor={best:.3f}", flush=True)

    sym_clean = asset.lower()
    study_name = f"{sym_clean}_{tf}_{strategy}_countertrend_{regime}"
    optuna_dir = MODELS_DIR / "optuna_per_study"
    optuna_dir.mkdir(parents=True, exist_ok=True)
    storage_path = str(optuna_dir / f"{study_name}.db")
    storage_url = f"sqlite:///{storage_path}?timeout=30"
    try:
        import sqlite3 as _s3
        _wc = _s3.connect(storage_path, timeout=10)
        _wc.execute("PRAGMA journal_mode=WAL")
        _wc.close()
    except Exception:
        pass

    study = optuna.create_study(
        direction="maximize", study_name=study_name, storage=storage_url,
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=20),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=0),
    )
    study.set_user_attr("regime_mask_mode", "countertrend_bypass")
    study.optimize(objective, n_trials=n_trials, callbacks=[cb])

    if study.best_value is None or study.best_value <= -9999:
        print(f"[countertrend_objective] {symbol}/{tf}/{strategy} ct-{regime}: sin resultado valido", flush=True)
        return

    best_params = {k: v for k, v in study.best_params.items() if k != "risk_pct"}
    risk_pct = study.best_params.get("risk_pct", 3.3)

    # Re-correr el mejor trial con regime_tagged_backtest para sacar el
    # desglose de robustez multi-ciclo (igual criterio que Fase 1).
    sig, sl, tp = sig_fn(df_override, best_params)
    sig = sig.where(mask, 0)
    trades = regime_tagged_backtest(df_override, sig, sl, tp, risk_pct=risk_pct)
    target_trades = [t for t in trades if t["regime"] == regime]
    raw_segs = contiguous_segments(df_override[target_col])
    real_segs = filter_segments_by_duration(raw_segs, TF_MINUTES[tf], MIN_SEGMENT_DURATION_DAYS)
    by_seg = trades_per_segment(target_trades, real_segs)
    seg_sum = segment_summary(by_seg)

    n = len(target_trades)
    wins = sum(1 for t in target_trades if t["won"])
    pnl = float(sum(t["pnl"] for t in target_trades))

    trial_values = [t.value for t in study.trials if t.value is not None]
    bias = selection_bias_test(trial_values, best_value=study.best_value)

    result = {
        "symbol": symbol, "tf": tf, "strategy": strategy, "regime": regime,
        "direction": "long" if regime == "bear" else "short",
        "params": best_params, "risk_pct": round(risk_pct, 2),
        "n_trials": len(study.trials), "best_score": round(study.best_value, 4),
        "n_trades": n,
        "wr": round(wins / n * 100, 1) if n else 0,
        "pnl_total": round(pnl, 2),
        "n_segments_qualified": seg_sum["n_qualified"],
        "n_segments_total": seg_sum["n_segments_total"],
        "selection_bias": bias,
        "trained_at": __import__("datetime").datetime.now().isoformat(),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{sym_clean}_{tf}_{strategy}_{regime}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"[countertrend_objective] {symbol}/{tf}/{strategy} ct-{regime} -> "
          f"score={result['best_score']} n={n} wr={result['wr']}% pnl=${pnl:.2f} "
          f"segs={seg_sum['n_qualified']}/{seg_sum['n_segments_total']} "
          f"bias={bias.get('verdict','?')} -> {out_path}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--regime", required=True, choices=["bear", "bull"])
    ap.add_argument("--trials", type=int, default=250)
    a = ap.parse_args()
    run(a.symbol, a.tf, a.strategy, a.regime, a.trials)
