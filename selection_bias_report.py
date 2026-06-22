#!/usr/bin/env python3
"""Selection-bias / multiple-testing diagnostico — capa INFORMATIVA, no toca
el champion gate ni el sizing de Kelly.

Origen 2026-06-20: utils/robustness.py ya filtra overfit IS/OOS por modelo
individual, pero ningun lugar del sistema corrige por el hecho de que cada
champion es "el mejor de N intentos" de una busqueda Optuna (N tipico
1000-7000 trials por slot, ver models/optuna_per_study/*.db). Con N grande,
el mejor valor encontrado ya es alto por pura varianza de muestreo aunque
ninguna configuracion tenga skill diferenciado real -- hay que comparar
contra ESE piso, no contra cero.

Usa utils.quant.selection_bias_test (extreme value approx, Bailey & Lopez
de Prado 2014) sobre la distribucion empirica de scores Optuna de cada
estudio que respalda al champion actual de cada slot.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/opt/sigma")
from utils.quant import selection_bias_test

BASE      = Path("/opt/sigma")
SNAP_FILE = BASE / "results/reports/port_snapshot.json"
STUDY_DIR = BASE / "models/optuna_per_study"
OUT_FILE  = BASE / "results/reports/selection_bias.json"


def _trial_values(db_path):
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT tv.value FROM trial_values tv "
            "JOIN trials t ON tv.trial_id = t.trial_id "
            "WHERE t.state = 'COMPLETE'"
        )
        vals = [r[0] for r in cur.fetchall()]
        conn.close()
        return vals
    except Exception:
        return []


def run():
    if not SNAP_FILE.exists():
        print("[selection_bias] port_snapshot.json no existe, abortando")
        return {}

    snap = json.loads(SNAP_FILE.read_text())
    champions = snap.get("champions", {})

    results = {}
    for slot, val in sorted(champions.items()):
        sym, tf = slot.split("|")
        strat = val.split("|")[0]
        # Commodities usan sufijo "usd" en el nombre de archivo del estudio
        # (hgusd_, ngusd_, plusd_, wtiusd_, xagusd_, xauusd_); crypto no.
        candidates = [
            STUDY_DIR / f"{sym.lower()}_{tf}_{strat}.db",
            STUDY_DIR / f"{sym.lower()}usd_{tf}_{strat}.db",
        ]
        db_path = next((p for p in candidates if p.exists()), None)
        if db_path is None:
            results[slot] = {"strategy": strat, "status": "NO_STUDY_DB"}
            continue
        vals = _trial_values(db_path)
        test = selection_bias_test(vals)
        test["strategy"] = strat
        results[slot] = test

    n_noise    = sum(1 for r in results.values() if r.get("verdict") == "SELECTION_NOISE_LIKELY")
    n_weak     = sum(1 for r in results.values() if r.get("verdict") == "WEAK_SIGNAL")
    n_moderate = sum(1 for r in results.values() if r.get("verdict") == "MODERATE_SIGNAL")
    n_standout = sum(1 for r in results.values() if r.get("verdict") == "GENUINE_STANDOUT")
    n_no_db    = sum(1 for r in results.values() if r.get("status") == "NO_STUDY_DB")

    out = {
        "computed_at": __import__("datetime").datetime.utcnow().isoformat(),
        "note": ("Diagnostico de seleccion multiple (best-of-N Optuna). "
                 "Capa informativa -- NO afecta el champion gate ni el live execution."),
        "summary": {
            "n_slots": len(results),
            "selection_noise_likely": n_noise,
            "weak_signal": n_weak,
            "moderate_signal": n_moderate,
            "genuine_standout": n_standout,
            "no_study_db": n_no_db,
        },
        "slots": results,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2))

    print(f"[selection_bias] {len(results)} slots analizados")
    print(f"  GENUINE_STANDOUT:       {n_standout}")
    print(f"  MODERATE_SIGNAL:        {n_moderate}")
    print(f"  WEAK_SIGNAL:            {n_weak}")
    print(f"  SELECTION_NOISE_LIKELY: {n_noise}")
    print(f"  NO_STUDY_DB:            {n_no_db}")
    print()
    for slot, r in sorted(results.items()):
        v = r.get("verdict", r.get("status"))
        n = r.get("n_trials", "-")
        z = r.get("z_above_expected_luck", "-")
        print(f"  {slot:10s} {r.get('strategy',''):28s} n={str(n):>6s} z={str(z):>7s} {v}")

    return out


if __name__ == "__main__":
    run()
