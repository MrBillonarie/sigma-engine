"""
SIGMA ENGINE — Post-Search Pipeline
Corre automaticamente cuando termina cualquier search.
1. Guarda el ganador en models/
2. Corre Walk-Forward validation
3. Lanza Bayesian search (que aprende del resultado)
4. Actualiza el dashboard
5. Notifica con beeps + popup
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import subprocess
import winsound
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent.parent.parent


def run_post_pipeline(tf, results_csv=None, best_params=None, best_metrics=None):
    print(f"\n{'='*60}")
    print(f"  POST-SEARCH PIPELINE — {tf.upper()}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # 1. Guardar mejor config en models/
    if best_params and best_metrics:
        model_dir = OUTPUT_DIR / "models" / tf
        model_dir.mkdir(parents=True, exist_ok=True)
        with open(model_dir / "config.json", "w") as f:
            json.dump({
                "tf": tf,
                "params": best_params,
                "metrics": {k: round(v, 4) if isinstance(v, float) else v
                            for k, v in best_metrics.items()},
                "source": "random_search",
                "timestamp": datetime.now().isoformat(),
            }, f, indent=2)
        print(f"  [1/5] Modelo guardado: models/{tf}/config.json")
        m = best_metrics
        print(f"        {m.get('trades',0)}T | WR {m.get('winrate',0):.1f}% | "
              f"CAGR {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
              f"PF {m.get('profit_factor',0):.2f} | DD {m.get('max_dd',0):.1f}%")
    else:
        print(f"  [1/5] Sin params para guardar — usando config existente")

    # 2. Walk-Forward Validation
    print(f"\n  [2/5] Corriendo Walk-Forward validation...")
    wf_script = OUTPUT_DIR / "engine" / "optimization" / "walk_forward.py"
    if wf_script.exists():
        try:
            result = subprocess.run(
                ["python", "-u", "-X", "utf8", str(wf_script)],
                cwd=str(OUTPUT_DIR),
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                print(f"  Walk-Forward completado")
                # Parsear veredicto del output
                for line in result.stdout.split('\n'):
                    if 'VEREDICTO' in line or 'EDGE' in line or 'Score' in line:
                        print(f"        {line.strip()}")
            else:
                print(f"  Walk-Forward error: {result.stderr[-100:]}")
        except Exception as e:
            print(f"  Walk-Forward timeout/error: {e}")
    else:
        print(f"  walk_forward.py no encontrado")

    # 3. Bayesian Search (aprende del resultado)
    print(f"\n  [3/5] Lanzando Bayesian search (aprende del historial)...")
    bay_script = OUTPUT_DIR / "engine" / "optimization" / "bayesian_search.py"
    if bay_script.exists():
        # Lanzar en background (no esperar)
        subprocess.Popen(
            ["python", "-u", "-X", "utf8", str(bay_script), "--tf", tf, "--trials", "300"],
            cwd=str(OUTPUT_DIR)
        )
        print(f"  Bayesian search lanzado en background (300 trials)")
    else:
        print(f"  bayesian_search.py no encontrado")

    # 4. Actualizar dashboard
    print(f"\n  [4/5] Actualizando dashboard...")
    dash_script = OUTPUT_DIR / "engine" / "live" / "dashboard.py"
    if dash_script.exists():
        try:
            subprocess.run(
                ["python", "-X", "utf8", str(dash_script)],
                cwd=str(OUTPUT_DIR), timeout=30
            )
        except Exception:
            pass
    print(f"  Dashboard actualizado: results/charts/dashboard.html")

    # 5. Notificar
    print(f"\n  [5/5] Notificando...")
    try:
        for _ in range(4): winsound.Beep(1200, 300)
        m = best_metrics or {}
        msg = (f"POST-PIPELINE {tf.upper()} COMPLETADO\\n\\n"
               f"Modelo guardado en models/{tf}/\\n"
               f"Walk-Forward ejecutado\\n"
               f"Bayesian search lanzado en background\\n\\n"
               f"Resultado: {m.get('trades',0)}T | WR {m.get('winrate',0):.1f}%\\n"
               f"CAGR: {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
               f"PF: {m.get('profit_factor',0):.2f}\\n"
               f"DD: {m.get('max_dd',0):.1f}%\\n\\n"
               f"Ver dashboard: results/charts/dashboard.html")
        subprocess.Popen([
            "powershell", "-WindowStyle", "Hidden", "-Command",
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}", "SIGMA Pipeline", "OK", "Information")'
        ])
    except Exception:
        pass

    print(f"\n  Pipeline completado. {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="15m")
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    # Leer mejor config del CSV si existe
    best_p = best_m = None
    if args.csv and os.path.exists(args.csv):
        import pandas as pd
        df = pd.read_csv(args.csv)
        if not df.empty:
            best = df.sort_values("score", ascending=False).iloc[0]
            best_m = {k: best[k] for k in ["trades","winrate","pnl_pct","profit_factor",
                                             "max_dd","sharpe"] if k in best}
            best_p = {k[2:]: best[k] for k in best.index if k.startswith("p_")}

    run_post_pipeline(args.tf, args.csv, best_p, best_m)
