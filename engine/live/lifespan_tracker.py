#!/usr/bin/env python3
"""
Lifespan Tracker - predice cuando un modelo esta por morir, no despues.
Analiza: edad, trayectoria del score, mismatch de regimen.
Health: HEALTHY / WARNING / DECLINING / CRITICAL

Cron semanal recomendado (o diario si hay muchos modelos).
"""
import json, time
from pathlib import Path
from datetime import datetime

BASE      = Path("/opt/sigma")
MODEL_DIR = BASE / "models"
OUT_FILE  = BASE / "results/reports/lifespan_report.json"
REGIME_C  = BASE / "results/regime_cache.json"

# Vida esperada por timeframe (semanas). Calibrar con datos reales con el tiempo.
EXPECTED_LIFE_WEEKS = {
    "4h":  12,   # modelos 4h duran ~3 meses
    "1h":   8,   # modelos 1h duran ~2 meses
    "15m":  6,   # modelos 15m duran ~6 semanas
    "5m":   4,   # modelos 5m duran ~4 semanas
    "1d":  20,   # modelos 1d duran ~5 meses
}
DEFAULT_LIFE_WEEKS = 8


def _age_days(model):
    ts_raw = model.get("timestamp") or model.get("created_at", "")
    if not ts_raw:
        return 0
    try:
        dt = datetime.fromisoformat(str(ts_raw)[:19])
        return max((datetime.now() - dt).days, 0)
    except Exception:
        return 0


def _score_trajectory(model):
    """
    Retorna (score_initial, score_current, slope_per_eval, n_evals).
    slope < 0 = degradando.
    """
    history = model.get("score_history", [])
    current = (model.get("robustness_final") or
               model.get("canonical_score") or
               model.get("oos_score") or 0)
    if len(history) < 2:
        return current, current, 0.0, 1

    def _extract(x):
        return x.get("score", 0) if isinstance(x, dict) else float(x)

    initial = _extract(history[0])
    latest  = _extract(history[-1])
    n       = len(history)
    slope   = (latest - initial) / max(n - 1, 1)
    return initial, latest, slope, n


def _regime_mismatch(model, current_regime):
    """True si el modelo fue entrenado en un regimen diferente al actual."""
    train_regime = (model.get("regime_at_train") or
                    model.get("market_regime") or "UNKNOWN").upper()
    if train_regime in ("UNKNOWN", ""):
        return False
    return train_regime != current_regime.upper()


def _health_status(age_days_val, life_weeks, score_slope, mismatch):
    """Evalua salud del modelo en una escala de riesgo acumulado."""
    age_weeks = age_days_val / 7.0
    life_pct  = age_weeks / max(life_weeks, 1)

    risk = 0
    if life_pct > 0.85:    risk += 3   # muy viejo para su TF
    elif life_pct > 0.60:  risk += 1
    if score_slope < -0.05: risk += 2  # score cayendo rapido
    elif score_slope < 0:   risk += 1
    if mismatch:            risk += 2  # regimen diferente al de entrenamiento

    if risk >= 5:  return "CRITICAL"
    if risk >= 3:  return "DECLINING"
    if risk >= 1:  return "WARNING"
    return "HEALTHY"


def _weeks_remaining(age_days_val, life_weeks, score_slope):
    """Estimado de semanas restantes."""
    age_weeks     = age_days_val / 7.0
    raw_remaining = max(life_weeks - age_weeks, 0)
    # Score cayendo = acortar estimado
    if score_slope < -0.05:
        factor = max(0.3, 1 + score_slope * 5)
    elif score_slope < 0:
        factor = max(0.6, 1 + score_slope * 2)
    else:
        factor = 1.0
    return max(round(raw_remaining * factor, 1), 0)


def scan_models():
    current_regime = "UNKNOWN"
    try:
        rc = json.load(open(REGIME_C))
        current_regime = rc.get("BTC", {}).get("regime", "UNKNOWN").upper()
    except Exception:
        pass

    reports = []
    for tf_dir in sorted(MODEL_DIR.iterdir()):
        if not tf_dir.is_dir():
            continue
        tf         = tf_dir.name
        life_weeks = EXPECTED_LIFE_WEEKS.get(tf, DEFAULT_LIFE_WEEKS)

        for f in tf_dir.glob("*.json"):
            try:
                m = json.loads(f.read_text())
            except Exception:
                continue
            # Solo champions con metricas OOS
            if not m.get("metrics_oos") and not m.get("oos_score"):
                continue

            age              = _age_days(m)
            s_ini, s_cur, slope, n_evals = _score_trajectory(m)
            mismatch         = _regime_mismatch(m, current_regime)
            health           = _health_status(age, life_weeks, slope, mismatch)
            weeks_left       = _weeks_remaining(age, life_weeks, slope)

            reports.append({
                "model":               f.stem,
                "tf":                  tf,
                "age_days":            age,
                "age_weeks":           round(age / 7, 1),
                "expected_life_weeks": life_weeks,
                "life_pct_used":       round(age / 7 / max(life_weeks, 1) * 100, 1),
                "score_initial":       round(s_ini, 4),
                "score_current":       round(s_cur, 4),
                "score_slope":         round(slope, 5),
                "n_score_evals":       n_evals,
                "regime_mismatch":     mismatch,
                "health":              health,
                "weeks_remaining":     weeks_left,
            })

    # Peores primero
    rank = {"CRITICAL": 0, "DECLINING": 1, "WARNING": 2, "HEALTHY": 3}
    reports.sort(key=lambda x: (rank[x["health"]], x["weeks_remaining"]))
    return reports, current_regime


def run():
    reports, regime = scan_models()

    by_health = {}
    for r in reports:
        by_health.setdefault(r["health"], []).append(r)

    output = {
        "updated_at":     time.strftime("%Y-%m-%d %H:%M:%S"),
        "current_regime": regime,
        "n_models":       len(reports),
        "summary": {
            "HEALTHY":   len(by_health.get("HEALTHY", [])),
            "WARNING":   len(by_health.get("WARNING", [])),
            "DECLINING": len(by_health.get("DECLINING", [])),
            "CRITICAL":  len(by_health.get("CRITICAL", [])),
        },
        "models": reports,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(output, open(OUT_FILE, "w"), indent=2, default=str)

    s = output["summary"]
    print(f"Modelos: {len(reports)} | HEALTHY:{s['HEALTHY']} "
          f"WARNING:{s['WARNING']} DECLINING:{s['DECLINING']} CRITICAL:{s['CRITICAL']}")
    print(f"Regimen actual: {regime}")
    if by_health.get("CRITICAL"):
        print("CRITICAL:")
        for m in by_health["CRITICAL"][:5]:
            print(f"  {m['model']} ({m['tf']}) age={m['age_days']}d "
                  f"slope={m['score_slope']} left={m['weeks_remaining']}w")
    print(f"Guardado: {OUT_FILE}")


if __name__ == "__main__":
    run()
