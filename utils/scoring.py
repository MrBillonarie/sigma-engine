"""SIGMA canonical scoring - fuente unica de "que tan bueno es un modelo".

Antes de este modulo (2026-05-14), watcher y dashboard tenian implementaciones
divergentes de score que causaban flicker en port_snapshot.json.

2026-05-16: agregada opcion tf= para freq_penalty (alineacion con Pine generator).

Uso:
    from utils.scoring import canonical_score, grade_from_score
    s = canonical_score(model_json_dict)
    s = canonical_score(model_json_dict, tf="15m")  # con freq_penalty TF-aware
    grade = grade_from_score(s)
"""

def canonical_score(d: dict, *, strict: bool = True, tf: str = None) -> float:
    """Score canonico del modelo (0.0 - 1.0+).

    Pesos: CAGR 35% + Calmar 25% + Frecuencia 20% + WR 15% + PF 5%

    strict=True (default) replica los filtros de dashboard._compute_score:
      - trades < 10  -> descalifica (-9999)
      - cagr <= 0    -> descalifica (-9999)
      - trades/year < 3 -> descalifica (-9999)
    strict=False acepta cualquier modelo.

    tf=str opcional activa freq_penalty TF-aware (alineado con Pine generator):
      - "5m"/"1m": min_ty=50, scalping necesita alta frecuencia
      - "15m":     min_ty=20
      - otros:     min_ty=5
    Si ty<min_ty, s_freq se multiplica por (ty/min_ty), penalizacion gradual.

    Acepta dd con o sin signo: internamente toma abs().
    """
    try:
        m = d.get("metrics_oos") or {}
        t = float(m.get("trades", 0) or 0)
        wr = float(m.get("wr", m.get("winrate", 0)) or 0)
        cagr = float(m.get("cagr", 0) or 0)
        dd_raw = float(m.get("dd", m.get("max_dd", 0)) or 0)
        dd = abs(dd_raw)
        pf = float(m.get("pf", m.get("profit_factor", 1)) or 1)

        ty_field = m.get("trades_year")
        if ty_field is None:
            tm = m.get("trades_month", 0) or 0
            ty_field = tm * 12 if tm else 0
        try:
            ty = float(ty_field) if ty_field else 0.0
        except (TypeError, ValueError):
            ty = 0.0
        if ty <= 0 and t > 0:
            oos_days = float(d.get("oos_days", 365) or 365)
            ty = t * 365.0 / max(oos_days, 1)

        if strict:
            if t < 10 or cagr <= 0:
                return -9999.0
            if ty < 3:
                return -9999.0
            if wr <= 0 and cagr > 0:
                wr = 50.0

        # freq_penalty TF-aware (opcional)
        freq_penalty = 1.0
        if tf:
            min_ty = 50 if tf in ("5m", "1m") else 20 if tf == "15m" else 5
            freq_penalty = min(ty / min_ty, 1.0) if min_ty > 0 else 1.0

        s_cagr = min(cagr, 100) / 100 * 0.35
        s_cal = (min(cagr / dd, 8) / 8 * 0.25) if dd > 0 else 0.0
        s_freq = min(ty / 12.0, 1.0) * 0.20 * freq_penalty
        s_wr = min(max(wr / 100 - 0.50, 0) / 0.35, 1.0) * 0.15
        s_pf = min(pf, 3) / 3 * 0.05
        return round(s_cagr + s_cal + s_freq + s_wr + s_pf, 4)
    except Exception:
        return -9999.0 if strict else 0.0


def grade_from_score(score: float) -> str:
    """Mapeo score -> grade. Mismo umbral que dashboard."""
    if score is None or score < 0:
        return ""
    if score >= 0.70:
        return "A+"
    if score >= 0.55:
        return "A"
    if score >= 0.40:
        return "B"
    if score >= 0.25:
        return "C"
    return "D"


GRADE_A_THRESHOLD = 0.55
