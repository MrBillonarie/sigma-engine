"""
SIGMA ENGINE — Entry Point
Uso:
  python main.py --mode optimize  --tf 15m
  python main.py --mode validate  --tf 15m
  python main.py --mode backtest  --tf 15m
  python main.py --mode report
  python main.py --mode status
"""

import argparse
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core import (fetch_ohlcv, fetch_multi_tf, build_features,
                  get_signals, run_backtest, calc_metrics,
                  print_metrics, RiskManager, stop_rules_summary)

CONFIG_PATH = ROOT / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)


# ─── MODOS ────────────────────────────────────────────────────────────────────
def mode_optimize(tf, n_samples=None):
    """Random search de parametros para un TF."""
    print(f"\n{'='*65}")
    print(f"  SIGMA ENGINE — OPTIMIZE [{tf.upper()}]")
    print(f"{'='*65}")

    tf_cfg   = CFG["timeframes"].get(tf, {})
    n        = n_samples or CFG["optimization"]["random_search_samples"]

    # Importar el random search del directorio optimization/
    opt_path = ROOT / "optimization" / "random_search.py"
    if not opt_path.exists():
        # Fallback: buscar en BACKTESSTING
        bt_path = ROOT.parent / "BACKTESSTING" / "sigma_random_search.py"
        if bt_path.exists():
            print(f"  [WARN] optimization/random_search.py no existe.")
            print(f"  Usa: python {bt_path} directamente")
        else:
            print("  [ERROR] No se encontro random_search.py")
            print("  Ejecuta primero: cp ../BACKTESSTING/sigma_random_search.py optimization/")
        return

    import importlib.util
    spec = importlib.util.spec_from_file_location("random_search", opt_path)
    rs   = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    rs.main()


def mode_validate(tf):
    """Walk-Forward + Monte Carlo para un TF."""
    print(f"\n{'='*65}")
    print(f"  SIGMA ENGINE — VALIDATE [{tf.upper()}]")
    print(f"{'='*65}")

    wf_path = ROOT / "optimization" / "walk_forward.py"
    if not wf_path.exists():
        wf_path = ROOT.parent / "BACKTESSTING" / "walk_forward.py"

    if not wf_path.exists():
        print("  [ERROR] walk_forward.py no encontrado")
        return

    import importlib.util
    spec = importlib.util.spec_from_file_location("walk_forward", wf_path)
    wf   = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wf)
    wf.main()


def mode_backtest(tf):
    """Backtest rapido con parametros del model guardado."""
    print(f"\n{'='*65}")
    print(f"  SIGMA ENGINE — BACKTEST [{tf.upper()}]")
    print(f"{'='*65}")

    model_path = ROOT / "models" / tf / "config.json"
    if not model_path.exists():
        print(f"  [WARN] No hay modelo guardado para {tf}.")
        print(f"  Ejecuta primero: python main.py --mode optimize --tf {tf}")
        params = None
    else:
        with open(model_path) as f:
            params = json.load(f).get("params", None)
        print(f"  Usando modelo: {model_path}")

    tf_cfg = CFG["timeframes"].get(tf, {})
    htf1   = tf_cfg.get("htf1", "1h")
    htf2   = tf_cfg.get("htf2", "4h")

    print(f"\n[DATA] Descargando datos {tf}...")
    df_base = fetch_ohlcv(tf=tf)
    df_htf1 = fetch_ohlcv(tf=htf1, days=tf_cfg.get("days_history", 365)*2)
    df_htf2 = fetch_ohlcv(tf=htf2, days=tf_cfg.get("days_history", 365)*3)

    print("[FEATURES] Calculando indicadores...")
    df = build_features(df_base, {htf1: df_htf1, htf2: df_htf2})
    df.dropna(subset=["close", "atr", "ema50"], inplace=True)
    print(f"  {len(df)} velas listas")

    print("[SIGNALS] Generando señales...")
    signals, quality = get_signals(df, params)
    n_sig = (signals != 0).sum()
    print(f"  {n_sig} señales generadas")

    print("[BACKTEST] Corriendo...")
    trades, equity = run_backtest(df, signals, quality, params)
    days = (df.index[-1] - df.index[0]).days
    m = calc_metrics(trades, equity, name=f"SIGMA {tf.upper()}", days_period=days)
    print()
    print_metrics(m, label=tf.upper())

    # Guardar si es el mejor
    if m["trades"] >= tf_cfg.get("min_trades_valid", 20):
        out_dir = ROOT / "models" / tf
        out_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = out_dir / "metrics.json"
        with open(metrics_path, "w") as f:
            json.dump({k: round(v, 4) if isinstance(v, float) else v
                       for k, v in m.items()}, f, indent=2)
        print(f"\n  [SAVED] {metrics_path}")


def mode_report():
    """Resumen de todos los modelos guardados."""
    print(f"\n{'='*70}")
    print(f"  SIGMA ENGINE — REPORT CROSS-TF")
    print(f"{'='*70}")

    models_dir = ROOT / "models"
    if not models_dir.exists():
        print("  Sin modelos guardados todavia.")
        return

    print(f"\n  {'TF':<6} {'T/mes':>6} {'WR%':>7} {'CAGR%':>8} {'PF':>6} {'DD%':>7} {'Sharpe':>8}")
    print(f"  {'-'*55}")

    for tf_dir in sorted(models_dir.iterdir()):
        if not tf_dir.is_dir(): continue
        mf = tf_dir / "metrics.json"
        if not mf.exists(): continue
        with open(mf) as f:
            m = json.load(f)
        print(f"  {tf_dir.name:<6} {m.get('trades_month',0):>5.1f} "
              f"{m.get('winrate',0):>6.1f}% "
              f"{m.get('cagr', m.get('pnl_pct',0)):>7.1f}% "
              f"{m.get('profit_factor',0):>6.2f} "
              f"{m.get('max_dd',0):>6.1f}% "
              f"{m.get('sharpe',0):>8.2f}")

    print(f"  {'='*55}")

    # Pine Scripts disponibles
    ps_dir = ROOT / "results" / "pine_scripts"
    if ps_dir.exists():
        pine_files = list(ps_dir.glob("*.pine"))
        if pine_files:
            print(f"\n  Pine Scripts disponibles ({len(pine_files)}):")
            for pf in sorted(pine_files):
                print(f"    - {pf.name}")


def mode_status():
    """Estado del sistema y stop rules."""
    print(f"\n{'='*65}")
    print(f"  SIGMA ENGINE — STATUS")
    print(f"{'='*65}")

    print(f"\n  Identidad: {CFG['identity']['name']} v{CFG['identity']['version']}")
    print(f"  Simbolo:   {CFG['identity']['symbol']} en {CFG['identity']['exchange']}")
    print(f"  Estrategia:{CFG['identity']['base_strategy']}")
    print(f"  Fase:      {CFG['capital']['phase']} — {CFG['capital']['phase_description']}")
    print(f"  Capital:   ${CFG['capital']['initial']:,.0f} {CFG['capital']['currency']}")

    print()
    stop_rules_summary()

    print(f"\n  Validacion para operar:")
    v = CFG["validation_thresholds"]
    print(f"  WR minima:      {v['min_wr_operable']}%")
    print(f"  PF minimo:      {v['min_pf_operable']}")
    print(f"  Sharpe minimo:  {v['min_sharpe_operable']}")
    print(f"  DD maximo:      {v['max_dd_operable']}%")
    print(f"  Trades minimos: {v['min_trades_statistical']}")

    print(f"\n  Estructura:")
    dirs = ["core", "optimization", "models", "results/charts",
            "results/reports", "results/pine_scripts", "live"]
    for d in dirs:
        p = ROOT / d
        exists = "OK" if p.exists() else "--"
        files  = len(list(p.glob("*"))) if p.exists() else 0
        print(f"  {exists} {d:<35} ({files} archivos)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="SIGMA ENGINE — Motor de trading cuantitativo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --mode status
  python main.py --mode optimize --tf 15m
  python main.py --mode validate --tf 15m
  python main.py --mode backtest --tf 1h
  python main.py --mode report
        """
    )
    parser.add_argument("--mode", required=True,
                        choices=["optimize","validate","backtest","report","status",
                                 "genetic","stability","heatmap","correlate","full"],
                        help="Modo de operacion")
    parser.add_argument("--tf", default="15m",
                        choices=["1m","5m","15m","1h","4h","1d"],
                        help="Timeframe (default: 15m)")
    parser.add_argument("--samples", type=int, default=None,
                        help="Muestras para optimize (default: desde config)")

    args = parser.parse_args()

    if   args.mode == "optimize":   mode_optimize(args.tf, args.samples)
    elif args.mode == "validate":   mode_validate(args.tf)
    elif args.mode == "backtest":   mode_backtest(args.tf)
    elif args.mode == "report":     mode_report()
    elif args.mode == "status":     mode_status()
    elif args.mode == "genetic":
        sys.path.insert(0, str(ROOT / "optimization"))
        from optimization.genetic import run_genetic
        run_genetic(args.tf)
    elif args.mode == "stability":
        sys.path.insert(0, str(ROOT / "analysis"))
        from analysis.stability import full_stability_report
        full_stability_report(args.tf)
    elif args.mode == "heatmap":
        sys.path.insert(0, str(ROOT / "analysis"))
        from analysis.heatmap import run_heatmap_analysis
        run_heatmap_analysis(args.tf)
    elif args.mode == "correlate":
        sys.path.insert(0, str(ROOT / "analysis"))
        from analysis.correlation import analyze_correlation
        analyze_correlation()
    elif args.mode == "full":
        # Pipeline completo: genetic → stability → heatmap → pine
        print(f"\n[FULL PIPELINE] {args.tf.upper()}")
        from optimization.genetic import run_genetic
        run_genetic(args.tf)
        from analysis.stability import full_stability_report
        full_stability_report(args.tf)
        from analysis.heatmap import run_heatmap_analysis
        run_heatmap_analysis(args.tf)
        from live.pine_generator import generate_production_pine
        import json
        model = json.load(open(ROOT/"models"/args.tf/"config.json"))
        pine  = generate_production_pine(args.tf, model.get("params"), model.get("metrics"))
        path  = ROOT/"results"/"pine_scripts"/f"SIGMA_{args.tf.upper()}_PRODUCTION.pine"
        open(path,"w",encoding="utf-8").write(pine)
        print(f"\n[FULL] Completado. Pine Script: {path.name}")


if __name__ == "__main__":
    main()
