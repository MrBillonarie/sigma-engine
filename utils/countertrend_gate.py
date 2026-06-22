"""utils/countertrend_gate.py -- Fase 3 del plan champions_regime (2026-06-21):
PROTOCOLO formal de promocion para candidatos countertrend (long-en-bear /
short-en-bull) generados por countertrend_objective.py.

Codifica exactamente el criterio que se aplico "a mano" sobre los primeros
4 candidatos el 2026-06-21 -- para que cualquier candidato futuro, generado
por cualquier corrida nueva de countertrend_objective.py, se evalue con la
MISMA vara sin que alguien tenga que re-juzgar caso por caso. La idea es
que de aqui en adelante el trabajo sea solo "rellenar con data" (correr mas
busquedas dedicadas) y dejar que este protocolo decida -- no inventar un
criterio nuevo cada vez.

Este modulo SOLO calcula un veredicto. No escribe a champions_countertrend
ni a ningun snapshot de produccion -- conectar un veredicto PASS a la
ejecucion en vivo es una decision separada (Fase 5), deliberadamente NO
incluida aqui.
"""

MIN_TRADES_FLOOR = 30          # 2x el umbral normal de un champion comun (metrics()/score() usan min_t=15)
MIN_SEGMENTS_QUALIFIED = 2     # rentable en al menos 2 ciclos de regimen distintos, no uno solo
BLOCKING_BIAS_VERDICTS = {"SELECTION_NOISE_LIKELY", "INSUFFICIENT_TRIALS", "ZERO_VARIANCE", "UNKNOWN"}
WEAK_BIAS_VERDICTS = {"WEAK_SIGNAL"}


def evaluate(result: dict) -> dict:
    """result = un JSON de models/countertrend/*.json (salida de
    countertrend_objective.py). Retorna {'verdict': PASS|CONDICIONAL|BLOCKED,
    'reasons': [...]}."""
    reasons = []
    verdict = "PASS"

    n = result.get("n_trades", 0)
    if n < MIN_TRADES_FLOOR:
        reasons.append(f"n_trades={n} < piso {MIN_TRADES_FLOOR} (2x el umbral normal de un champion comun)")
        verdict = "BLOCKED"

    n_qual = result.get("n_segments_qualified", 0)
    if n_qual < MIN_SEGMENTS_QUALIFIED:
        reasons.append(
            f"solo {n_qual} ciclo(s) de regimen calificado(s), se exigen >= {MIN_SEGMENTS_QUALIFIED} "
            f"(riesgo de ajuste a un solo ciclo historico, no edge repetible)"
        )
        verdict = "BLOCKED"

    pnl = result.get("pnl_total", 0)
    if pnl <= 0:
        reasons.append(f"PnL agregado no positivo (${pnl}) -- pasar el filtro de ciclos no basta si el neto es perdedor")
        verdict = "BLOCKED"

    bias_info = result.get("selection_bias") or {}
    bias = bias_info.get("verdict", "UNKNOWN")
    if bias in BLOCKING_BIAS_VERDICTS:
        reasons.append(f"selection_bias_test = {bias} -- el mejor trial no se distingue de ruido de busqueda")
        verdict = "BLOCKED"
    elif bias in WEAK_BIAS_VERDICTS and verdict == "PASS":
        verdict = "CONDICIONAL"
        reasons.append(f"selection_bias_test = {bias} -- señal real pero no fuerte, requiere paper trading extendido antes de confirmar")

    if verdict == "PASS":
        reasons.append("pasa los 4 criterios del protocolo -- aun requiere semanas de paper trading antes de capital real (Fase 5/6, no incluidas aqui)")

    return {"verdict": verdict, "reasons": reasons}
