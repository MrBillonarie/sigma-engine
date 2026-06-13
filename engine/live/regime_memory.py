#!/usr/bin/env python3
"""
Regime Memory - aprende que modelos funcionan en cada regimen de mercado.
Cron diario. Construye lookup: estrategia + regimen -> performance historica.
Cuando el mercado cambia de regimen, sabemos que esperar de cada modelo.
"""
import json, time
from pathlib import Path
from datetime import datetime

BASE      = Path("/opt/sigma")
TS_FILE   = BASE / "results/trade_state.json"
OUT_FILE  = BASE / "results/reports/regime_memory.json"
REGIME_C  = BASE / "results/regime_cache.json"

REGIMES = ["BULL", "BEAR", "NEUTRAL", "UNKNOWN"]


def _wr(wins, n):
    return round(wins / n * 100, 1) if n > 0 else 0.0


def build_regime_memory(history):
    """
    Construye: {strategy_key: {regime: {wr, avg_pnl, n, last_seen}}}
    strategy_key = "{sym}_{tf}_{strategy}"
    """
    mem = {}

    for t in history:
        status = t.get("status", "")
        if status not in ("TP_HIT", "SL_HIT", "CLOSED", "MANUAL_CLOSE"):
            continue

        sym      = t.get("sym", "UNK").split("/")[0].upper()
        tf       = t.get("tf", "?")
        strategy = t.get("strategy", "unknown")
        regime   = t.get("regime_at_close", "UNKNOWN").upper()
        if regime not in REGIMES:
            regime = "UNKNOWN"
        pnl   = t.get("pnl_pct", 0) or 0
        won   = pnl > 0
        closed = t.get("closed_at", "")

        key = f"{sym}_{tf}_{strategy}"
        if key not in mem:
            mem[key] = {r: {"n": 0, "wins": 0, "sum_pnl": 0.0, "last_seen": ""}
                        for r in REGIMES}

        mem[key][regime]["n"]       += 1
        mem[key][regime]["wins"]    += int(won)
        mem[key][regime]["sum_pnl"] += pnl
        if closed > mem[key][regime]["last_seen"]:
            mem[key][regime]["last_seen"] = closed

    result = {}
    for key, regimes in mem.items():
        result[key] = {}
        best_regime = None
        best_score  = -999
        for regime, d in regimes.items():
            n = d["n"]
            if n == 0:
                continue
            wr      = _wr(d["wins"], n)
            avg_pnl = round(d["sum_pnl"] / n, 4)
            # Score: WR ponderado por confianza estadistica
            conf  = min(n / 10.0, 1.0)
            score = (wr / 100 * 0.6 + (avg_pnl / 0.05 + 0.5) * 0.4) * conf
            if score > best_score:
                best_score  = score
                best_regime = regime
            result[key][regime] = {
                "n":         n,
                "wr":        wr,
                "avg_pnl":   avg_pnl,
                "last_seen": d["last_seen"],
                "score":     round(score, 3),
            }
        if best_regime:
            result[key]["_best_regime"] = best_regime
            result[key]["_best_score"]  = round(best_score, 3)

    return result


def get_regime_leaderboard(memory, current_regime, top_n=10):
    """Que modelos han funcionado mejor historicamente en el regimen actual."""
    ranked = []
    for key, regimes in memory.items():
        r = regimes.get(current_regime)
        if r and r["n"] >= 3:
            ranked.append({
                "model":   key,
                "regime":  current_regime,
                "wr":      r["wr"],
                "avg_pnl": r["avg_pnl"],
                "n":       r["n"],
                "score":   r["score"],
            })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


def run():
    try:
        state = json.load(open(TS_FILE))
    except Exception as e:
        print(f"ERROR loading trade_state: {e}")
        return

    history  = state.get("history", [])
    n_closed = sum(1 for t in history
                   if t.get("status") in ("TP_HIT", "SL_HIT", "CLOSED", "MANUAL_CLOSE"))

    memory   = build_regime_memory(history)
    n_models = len(memory)

    current_regime = "UNKNOWN"
    try:
        rc = json.load(open(REGIME_C))
        current_regime = rc.get("BTC", {}).get("regime", "UNKNOWN").upper()
    except Exception:
        pass

    leaderboard = get_regime_leaderboard(memory, current_regime)

    output = {
        "updated_at":        time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_trades_used":     n_closed,
        "n_models_tracked":  n_models,
        "current_regime":    current_regime,
        "leaderboard":       leaderboard,
        "memory":            memory,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(output, open(OUT_FILE, "w"), indent=2, default=str)

    print(f"Modelos tracked: {n_models} | Regimen: {current_regime}")
    print(f"Top en {current_regime}:")
    for m in leaderboard[:3]:
        print(f"  {m['model']}: WR={m['wr']}% avg={m['avg_pnl']:.3f} n={m['n']}")
    print(f"Guardado: {OUT_FILE}")


if __name__ == "__main__":
    run()
