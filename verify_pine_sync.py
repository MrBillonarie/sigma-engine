#!/usr/bin/env python3
"""
verify_pine_sync.py — Valida que f_strat del Pine coincida con los champion JSONs del VPS.

Reporta: matches, mismatches, Pine-only (no hay JSON), VPS-only (no en Pine).

Uso:
    python verify_pine_sync.py [--pine /ruta/al/pine] [--models /ruta/models]
"""
import re
import json
import argparse
from pathlib import Path

PINE_DEFAULT   = "/opt/sigma/results/pine_scripts/SIGMA_v13_COMPLETO.pine"
MODELS_DEFAULT = "/opt/sigma/models"

TF_MIN_TO_NAME = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}


def parse_pine_f_strat(pine_path: Path) -> dict:
    """Extrae {(SYMBOL, tf_name): strategy} desde f_strat() del Pine."""
    text = pine_path.read_text(encoding="utf-8")
    # Localizar la función f_strat
    start = text.find("f_strat(")
    if start < 0:
        raise RuntimeError("f_strat() no encontrada en Pine")
    end = text.find("// Función 2", start)
    block = text[start:end] if end > 0 else text[start:start + 4000]

    pat = re.compile(
        r'if\s+str\.contains\(_tk,"([A-Z]+)"\)\s+and\s+_tf_m==(\d+)\s*\n'
        r'\s*_r\s*:=\s*"([a-z_0-9]+)"',
        re.IGNORECASE,
    )
    out = {}
    for m in pat.finditer(block):
        sym = m.group(1).upper()
        tf_m = int(m.group(2))
        tf_name = TF_MIN_TO_NAME.get(tf_m, str(tf_m))
        strat = m.group(3)
        out[(sym, tf_name)] = strat
    return out


def read_vps_champions(models_dir: Path) -> dict:
    """Lee el champion VENCEDOR de cada slot (sym, tf) usando la API /api/signals.

    La API devuelve TODOS los modelos candidatos por slot, cada uno con el flag
    explicito 'is_champion'. El campeon real no es necesariamente el de mayor
    score canonico -- puede estar demotido por gates de robustness/regimen que
    el score crudo no refleja (encontrado 2026-06-19: PL/1h reportaba tema_cross
    score=0.939 CAGR=6387% como "campeon" via max-score, cuando el campeon real
    marcado por is_champion=true era volume_exhaustion score=0.42 CAGR=27%).
    Fallback a CAGR si la API no responde.
    """
    import urllib.request
    try:
        r = urllib.request.urlopen("http://localhost:8080/api/signals", timeout=15)
        data = json.loads(r.read())
        models = data.get("models", [])
        out = {}
        for m in models:
            if not m.get("is_champion"):
                continue
            sym = (m.get("sym") or "").upper()
            tf  = m.get("tf") or ""
            strat = m.get("strategy") or ""
            if not (sym and tf and strat):
                continue
            out[(sym, tf)] = {
                "strategy": strat,
                "cagr": m.get("cagr", 0),
                "score": m.get("score", 0) or 0,
                "grade": m.get("grade", "?"),
            }
        if out:
            return out
    except Exception as e:
        print(f"WARN: API fallback to CAGR-based: {e}")

    # Fallback: CAGR-based (less accurate)
    groups = {}
    for tf_dir in sorted(models_dir.iterdir()):
        if not tf_dir.is_dir() or tf_dir.name == "archive":
            continue
        tf = tf_dir.name
        for jf in sorted(tf_dir.glob("*.json")):
            try:
                d = json.loads(jf.read_text())
                sym = d.get("symbol", "").replace("/USDT", "").replace("/USD", "").upper()
                strat = d.get("strategy", "")
                cagr = (d.get("metrics_oos") or {}).get("cagr", 0) or 0
                if not sym or not strat:
                    continue
                key = (sym, tf)
                prev = groups.get(key)
                if prev is None or cagr > prev["cagr"]:
                    groups[key] = {"strategy": strat, "cagr": cagr}
            except Exception:
                pass
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pine", default=PINE_DEFAULT)
    ap.add_argument("--models", default=MODELS_DEFAULT)
    args = ap.parse_args()

    pine_path = Path(args.pine)
    models_dir = Path(args.models)
    if not pine_path.exists():
        print(f"FATAL: no existe {pine_path}")
        return 1
    if not models_dir.exists():
        print(f"FATAL: no existe {models_dir}")
        return 1

    pine_map = parse_pine_f_strat(pine_path)
    vps_map = read_vps_champions(models_dir)

    print(f"Pine slots:   {len(pine_map)}")
    print(f"VPS champions: {len(vps_map)}")
    print()

    matches, mismatches, only_pine, only_vps = [], [], [], []

    all_keys = set(pine_map) | set(vps_map)
    for key in sorted(all_keys):
        sym, tf = key
        p_strat = pine_map.get(key)
        v = vps_map.get(key)
        v_strat = v["strategy"] if v else None
        v_cagr  = v["cagr"] if v else None
        if p_strat and v_strat:
            if p_strat == v_strat:
                matches.append((sym, tf, p_strat, v_cagr))
            else:
                mismatches.append((sym, tf, p_strat, v_strat, v_cagr))
        elif p_strat and not v_strat:
            only_pine.append((sym, tf, p_strat))
        elif v_strat and not p_strat:
            only_vps.append((sym, tf, v_strat, v_cagr))

    # Output
    print(f"PINE vs VPS · {len(matches)} matches · {len(mismatches)} mismatch · {len(only_pine)} solo en Pine · {len(only_vps)} solo en VPS")
    print()
    if matches:
        print("MATCHES:")
        for sym, tf, strat, cagr in matches:
            print(f"  ✓ {sym:4s} {tf:4s} {strat:30s} CAGR_OOS={cagr}")
    if mismatches:
        print("\nMISMATCHES:")
        for sym, tf, p, v, c in mismatches:
            print(f"  ✗ {sym:4s} {tf:4s} pine={p}  vs  vps={v}  CAGR_OOS={c}")
    if only_pine:
        print("\nSOLO EN PINE (no JSON en VPS):")
        for sym, tf, strat in only_pine:
            print(f"  - {sym:4s} {tf:4s} {strat}")
    if only_vps:
        print("\nSOLO EN VPS (no en Pine):")
        for sym, tf, strat, cagr in only_vps:
            print(f"  + {sym:4s} {tf:4s} {strat:30s} CAGR_OOS={cagr}")

    # Exit code: 0 if perfectly in sync, 1 if anything off
    return 0 if (not mismatches and not only_pine and not only_vps) else 1


if __name__ == "__main__":
    import sys as _s
    _s.exit(main())
