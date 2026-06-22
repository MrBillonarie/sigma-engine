"""utils/optuna_stagnation.py -- diagnostico de estancamiento de studies
Optuna (2026-06-21). Hallazgo: de 1820 studies con >=100 trials, 60.8% no
mejoran su mejor score en el ultimo 40% de sus intentos -- ~27.3% de TODOS
los trials corridos historicamente se gastaron en busquedas ya convergidas.

Esta funcion es de SOLO LECTURA -- no decide nada por si sola, no cancela
ni pausa ningun job en curso. Cualquier launcher (continuous_trainer,
master_pipeline, gap_auto_launcher, adaptive_push_launcher) puede llamarla
ANTES de encolar mas trials para un (symbol, tf, strategy) ya entrenado, y
usar el resultado para preferir slots que SI siguen mejorando.
"""
import sqlite3
from pathlib import Path

OPTUNA_DIR = Path("/opt/sigma/models/optuna_per_study")
MIN_TRIALS_TO_JUDGE = 100
STAGNATION_WINDOW = 0.4       # ultimo 40% de los trials
STAGNATION_THRESHOLD = 0.0005  # mejora de score por debajo de esto = estancado


def _study_path(symbol, tf, strategy, suffix=None):
    sym = symbol.split("/")[0].lower()
    name = f"{sym}_{tf}_{strategy}" + (f"_{suffix}" if suffix else "")
    return OPTUNA_DIR / f"{name}.db"


def stagnation_info(db_path, min_trials=MIN_TRIALS_TO_JUDGE):
    """Lee un study .db y retorna info de estancamiento, o None si no hay
    suficientes trials para juzgar."""
    if not Path(db_path).exists():
        return None
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        cur = con.cursor()
        cur.execute(
            "SELECT tv.value FROM trial_values tv JOIN trials t ON tv.trial_id = t.trial_id "
            "WHERE t.state = 'COMPLETE' ORDER BY t.trial_id"
        )
        rows = cur.fetchall()
        con.close()
    except Exception:
        return None

    vals = [r[0] for r in rows if r[0] is not None and r[0] > -9000]
    n = len(vals)
    if n < min_trials:
        return {"status": "INSUFFICIENT_TRIALS", "n_trials": n}

    best_so_far = []
    b = -9999
    for v in vals:
        b = max(b, v)
        best_so_far.append(b)

    cut = int(n * (1 - STAGNATION_WINDOW))
    improvement = best_so_far[-1] - best_so_far[cut]
    stagnant = improvement < STAGNATION_THRESHOLD

    return {
        "status": "OK",
        "n_trials": n,
        "best_score": round(best_so_far[-1], 4),
        "improvement_last_window": round(improvement, 5),
        "stagnant": stagnant,
    }


def is_stagnant(symbol, tf, strategy, suffix=None) -> bool:
    """True si el study ya tiene >= MIN_TRIALS_TO_JUDGE y no mejoro su mejor
    score en el ultimo 40% de sus intentos. False si no hay datos suficientes
    para juzgar (mejor seguir entrenando que asumir estancamiento sin evidencia)."""
    info = stagnation_info(_study_path(symbol, tf, strategy, suffix))
    if info is None or info.get("status") != "OK":
        return False
    return info["stagnant"]
