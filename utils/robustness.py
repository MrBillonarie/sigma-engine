"""SIGMA Robustness Scoring — STRICT anti-overfit version (2026-05-19).

User explicit: "debe ser un impacto real nada de overfit".

Strategy: combinacion de score ponderado + HARD GATES no negociables.
Si un modelo falla un hard gate → forzado a PAPER_ONLY o BLOCKED independiente del score.

Hard gates:
1. consistency < 0.30 (IS/OOS gap grande) → MAX PAPER_ONLY (overfit signal)
2. consistency < 0.10 (IS/OOS gap brutal)  → BLOCKED
3. dd_score < 0.30 (DD > 50%)              → BLOCKED
4. n < 0.50 (menos de 15 trades histor.)   → MAX PAPER_ONLY
5. WFT FAIL (pct < 50%) confirmado         → MAX PAPER_ONLY

Esto previene exactamente lo que el user pidio: nada de overfit pasa a live.
"""

WEIGHTS = {
    "wft": 0.30, "consistency": 0.25, "mc": 0.15, "n": 0.15, "dd": 0.15,
}

THR_PASS_LIVE  = 0.65
THR_PAPER_ONLY = 0.45

# Hard gate thresholds (anti-overfit)
HARD_CONS_BLOCK    = 0.10   # consistency menor que esto -> BLOCKED
HARD_CONS_PAPER    = 0.30   # consistency menor que esto -> max PAPER_ONLY
HARD_DD_BLOCK      = 0.30   # dd_score menor que esto -> BLOCKED (DD > 50%)
HARD_N_PAPER       = 0.50   # n menor que esto -> max PAPER_ONLY (< 15 trades)
HARD_WFT_FAIL      = 0.50   # WFT pct positive < 0.50 confirmado -> max PAPER_ONLY
HARD_CAGR_IMPOSSIBLE = 800.0  # CAGR OOS > 800% -> magnitud imposible para un edge real, BLOCKED
HARD_WR_SUSPECT      = 95.0  # WR > 95% con n>=20 -> casi seguro artefacto de backtest, max PAPER_ONLY

# Defaults para datos faltantes — penalty, NO neutral
WFT_NA_DEFAULT  = 0.30  # WFT no computado = penalty (era 0.50)
MC_NA_DEFAULT   = 0.30  # MC no computado = penalty (era 0.50)


def robustness_score(model_dict: dict) -> dict:
    """Calcula score 0-1 + factores + hard gates. Devuelve action final.

    Returns dict con: wft, consistency, mc, n, dd, final, action, gates_failed, factors_str
    """
    m_oos = model_dict.get("metrics_oos", {}) or {}
    m_is  = model_dict.get("metrics_is", {}) or {}
    val   = model_dict.get("validation", {}) or {}

    wft_pct = (val.get("walk_forward", {}) or {}).get("pct_positive", None)
    # 2026-05-31: tambien leer wft field (cron semanal, mas reciente)
    _wft_rr = (model_dict.get("wft") or {}).get("oos_win_rate", None)
    if _wft_rr is not None:
        wft_pct = min(wft_pct, _wft_rr) if wft_pct is not None else _wft_rr
    wft = WFT_NA_DEFAULT if wft_pct is None else max(0, min(1, wft_pct / 100.0))
    wft_na = wft_pct is None
    wft_failed = (not wft_na) and (wft_pct < (HARD_WFT_FAIL * 100))

    cagr_is = float(m_is.get("cagr", 0) or 0)
    cagr_oos = float(m_oos.get("cagr", 0) or 0)
    if cagr_is and cagr_oos:
        if cagr_is <= 0:
            # IS negativo: estrategia sin edge claro en training → penalizar moderado
            consistency = 0.20
        elif cagr_oos >= cagr_is:
            # OOS >= IS: conservador en IS, mejor en OOS → anti-overfit (recompensa)
            consistency = 1.0
        else:
            # IS > OOS: overfit clásico → penalizar por gap
            gap = cagr_is - cagr_oos
            consistency = max(0, 1 - gap / max(abs(cagr_is), 30))
    else:
        consistency = 0.30

    mc_raw = (val.get("monte_carlo", {}) or {}).get("p_pos", None)
    mc = MC_NA_DEFAULT if mc_raw is None else max(0, min(1, mc_raw / 100.0))

    trades = float(m_oos.get("trades", 0) or 0)
    n = min(trades / 30.0, 1.0)

    dd_abs = abs(float(m_oos.get("dd", 0) or 0))
    if dd_abs <= 20:   dd_score = 1.0
    elif dd_abs <= 35: dd_score = 0.7
    elif dd_abs <= 50: dd_score = 0.4
    else:              dd_score = 0.1

    # Weighted final (informational)
    final = (wft * WEIGHTS["wft"]
             + consistency * WEIGHTS["consistency"]
             + mc * WEIGHTS["mc"]
             + n * WEIGHTS["n"]
             + dd_score * WEIGHTS["dd"])
    final = round(final, 3)

    # Score-based action (preliminary)
    if final >= THR_PASS_LIVE:
        action = "PASS_LIVE"
    elif final >= THR_PAPER_ONLY:
        action = "PAPER_ONLY"
    else:
        action = "BLOCKED"

    # HARD GATES — overrride independent de score
    gates_failed = []
    if consistency < HARD_CONS_BLOCK:
        action = "BLOCKED"
        gates_failed.append(f"CONS_CRITICAL({consistency:.2f}<{HARD_CONS_BLOCK})")
    elif consistency < HARD_CONS_PAPER:
        if action == "PASS_LIVE":
            action = "PAPER_ONLY"
        gates_failed.append(f"OVERFIT_RISK({consistency:.2f}<{HARD_CONS_PAPER})")

    if dd_score < HARD_DD_BLOCK:
        action = "BLOCKED"
        gates_failed.append(f"DD_EXCESSIVE({dd_score:.2f}<{HARD_DD_BLOCK})")

    if n < HARD_N_PAPER:
        if action == "PASS_LIVE":
            action = "PAPER_ONLY"
        gates_failed.append(f"LOW_N({n:.2f}<{HARD_N_PAPER})")

    if wft_failed:
        if action == "PASS_LIVE":
            action = "PAPER_ONLY"
        gates_failed.append(f"WFT_FAIL({wft:.2f})")

    # CAGR/WR magnitud imposible -> casi siempre bug de backtest, no edge real
    wr_oos = float(m_oos.get("wr", 0) or 0)
    if abs(cagr_oos) > HARD_CAGR_IMPOSSIBLE:
        action = "BLOCKED"
        gates_failed.append(f"CAGR_IMPOSSIBLE({cagr_oos:.0f}%>{HARD_CAGR_IMPOSSIBLE:.0f}%)")
    elif wr_oos > HARD_WR_SUSPECT and trades >= 20:
        if action == "PASS_LIVE":
            action = "PAPER_ONLY"
        gates_failed.append(f"WR_SUSPECT({wr_oos:.1f}%>{HARD_WR_SUSPECT:.0f}%)")

    return {
        "wft": round(wft, 3),
        "wft_na": wft_na,
        "consistency": round(consistency, 3),
        "mc": round(mc, 3),
        "n": round(n, 3),
        "dd": round(dd_score, 3),
        "final": final,
        "action": action,
        "gates_failed": gates_failed,
        "factors_str": (f"wft={wft:.2f}{'*' if wft_na else ''} "
                        f"cons={consistency:.2f} mc={mc:.2f} "
                        f"n={n:.2f} dd={dd_score:.2f}"),
    }


def robustness_kelly_multiplier(action: str) -> float:
    """Multiplicador de Kelly basado en action. Conservador anti-overfit.
    PASS_LIVE = 1.0x (Kelly normal)
    PAPER_ONLY = 0.0x (no live)
    BLOCKED = 0.0x (ni paper segun caller)
    """
    return {"PASS_LIVE": 1.0, "PAPER_ONLY": 0.0, "BLOCKED": 0.0}.get(action, 0.0)


if __name__ == "__main__":
    import json, os
    snap = json.load(open("/opt/sigma/results/reports/port_snapshot.json"))
    champs = snap.get("champions", {})
    counts = {"PASS_LIVE": 0, "PAPER_ONLY": 0, "BLOCKED": 0}
    print(f"{'Slot':12s} {'Strategy':28s} {'Score':6s} {'Action':12s} {'Hard Gates Failed':40s}")
    print("-" * 130)
    for slot, val in sorted(champs.items()):
        sym, tf = slot.split("|")
        strat = val.split("|")[0]
        fp = f"/opt/sigma/models/{tf}/{sym.lower()}_{strat}.json"
        if not os.path.exists(fp): continue
        d = json.load(open(fp))
        r = robustness_score(d)
        counts[r["action"]] += 1
        gates_str = ", ".join(r["gates_failed"]) if r["gates_failed"] else "-"
        print(f"{slot:12s} {strat:28s} {r['final']:>5.3f}  {r['action']:12s} {gates_str[:60]}")
    print()
    print(f"Resumen anti-overfit STRICT:")
    print(f"  PASS_LIVE:  {counts['PASS_LIVE']:>2d}/16  (apto real money con Kelly normal)")
    print(f"  PAPER_ONLY: {counts['PAPER_ONLY']:>2d}/16  (paper trading OK, no live)")
    print(f"  BLOCKED:    {counts['BLOCKED']:>2d}/16  (ni paper - DD critico o overfit brutal)")

