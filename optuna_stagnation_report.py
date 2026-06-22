#!/usr/bin/env python3
"""Reporte de estancamiento Optuna -- escanea models/optuna_per_study/*.db,
clasifica cada study con utils/optuna_stagnation.py, y escribe
results/reports/optuna_stagnation.json. Solo lectura, no cambia nada."""
import sys
import json
from pathlib import Path

sys.path.insert(0, "/opt/sigma")
from utils.optuna_stagnation import stagnation_info, OPTUNA_DIR


def run():
    studies = []
    for dbp in sorted(OPTUNA_DIR.glob("*.db")):
        info = stagnation_info(dbp)
        if info is None or info.get("status") != "OK":
            continue
        studies.append({"study": dbp.stem, **info})

    stagnant = [s for s in studies if s["stagnant"]]
    active = [s for s in studies if not s["stagnant"]]
    total_trials = sum(s["n_trials"] for s in studies)
    wasted_trials = sum(int(s["n_trials"] * 0.4) for s in stagnant)

    report = {
        "n_studies_judged": len(studies),
        "n_stagnant": len(stagnant),
        "n_active": len(active),
        "pct_stagnant": round(len(stagnant) / len(studies) * 100, 1) if studies else 0,
        "total_trials_in_judged_studies": total_trials,
        "estimated_wasted_trials": wasted_trials,
        "pct_wasted": round(wasted_trials / total_trials * 100, 1) if total_trials else 0,
        "top_20_most_wasted": sorted(
            [s for s in stagnant], key=lambda s: -s["n_trials"]
        )[:20],
    }

    out = Path("/opt/sigma/results/reports/optuna_stagnation.json")
    out.write_text(json.dumps(report, indent=2))
    print(f"studies juzgados: {report['n_studies_judged']}")
    print(f"estancados: {report['n_stagnant']} ({report['pct_stagnant']}%)")
    print(f"trials totales: {report['total_trials_in_judged_studies']:,}")
    print(f"trials estimados desperdiciados: {report['estimated_wasted_trials']:,} ({report['pct_wasted']}%)")
    print()
    print("top 20 studies con mas trials desperdiciados (mas CPU recuperable si se detiene su re-entrenamiento):")
    for s in report["top_20_most_wasted"]:
        print(f"  {s['study']:50s} n_trials={s['n_trials']:>5} best={s['best_score']} mejora_ult40%={s['improvement_last_window']}")


if __name__ == "__main__":
    run()
