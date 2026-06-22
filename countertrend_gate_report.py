#!/usr/bin/env python3
"""Driver de la Fase 3: escanea models/countertrend/*.json (salida de
countertrend_objective.py), aplica utils/countertrend_gate.py a cada uno,
calcula la metrica de transparencia de transicion de regimen, y escribe
results/reports/countertrend_gate.json.

Disenado para re-correrse cada vez que aparezcan candidatos nuevos --
"rellenar con data" significa solo correr countertrend_objective.py para un
slot nuevo y despues re-correr este reporte; el protocolo de evaluacion no
cambia.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, "/opt/sigma")

from engine.optimization.asset_pipeline import load_asset_csv, fetch_asset, add_features, SIG_FN, SIG_FN_SHORT
from utils.regime_backtest import (
    regime_tagged_backtest, contiguous_segments, filter_segments_by_duration,
    transition_zone_breakdown,
)
from utils.countertrend_gate import evaluate

MODELS_DIR = Path("/opt/sigma/models")
CT_DIR = MODELS_DIR / "countertrend"
OUT_FILE = Path("/opt/sigma/results/reports/countertrend_gate.json")
CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "BNB", "LTC"}
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
MIN_SEGMENT_DURATION_DAYS = 14


def resolve_fn(name):
    return SIG_FN.get(name) or SIG_FN_SHORT.get(name)


def load_price_df(asset, tf):
    if asset in CRYPTO_ASSETS:
        df = fetch_asset(f"{asset}/USDT", tf=tf)
        df.index.name = "timestamp"
        return df
    p1 = MODELS_DIR / f"data_{asset}_{tf}_max.csv"
    if p1.exists():
        return load_asset_csv(str(p1))
    return None


def compute_transition_breakdown(result):
    symbol, tf, strategy, regime = result["symbol"], result["tf"], result["strategy"], result["regime"]
    asset = symbol.split("/")[0].upper()
    sig_fn = resolve_fn(strategy)
    df = load_price_df(asset, tf)
    if df is None or sig_fn is None:
        return None
    df = add_features(df)
    tradeable_col = "tradeable_long" if regime == "bear" else "tradeable_short"
    target_col = f"regime_{regime}"
    df_override = df.copy()
    df_override[tradeable_col] = True
    mask = df_override[target_col]

    sig, sl, tp = sig_fn(df_override, result["params"])
    sig = sig.where(mask, 0)
    trades = regime_tagged_backtest(df_override, sig, sl, tp, risk_pct=result.get("risk_pct", 3.3))
    target_trades = [t for t in trades if t["regime"] == regime]

    raw_segs = contiguous_segments(df_override[target_col])
    real_segs = filter_segments_by_duration(raw_segs, TF_MINUTES[tf], MIN_SEGMENT_DURATION_DAYS)
    return transition_zone_breakdown(target_trades, real_segs)


def run():
    report = {}
    for jf in sorted(CT_DIR.glob("*.json")):
        try:
            result = json.loads(jf.read_text())
        except Exception:
            continue
        slot_key = f"{result['symbol'].split('/')[0]}|{result['tf']}|{result['strategy']}|{result['regime']}"
        gate = evaluate(result)
        try:
            transition = compute_transition_breakdown(result)
        except Exception as e:
            transition = {"error": str(e)}
        report[slot_key] = {
            "n_trades": result.get("n_trades"),
            "wr": result.get("wr"),
            "pnl_total": result.get("pnl_total"),
            "n_segments_qualified": result.get("n_segments_qualified"),
            "n_segments_total": result.get("n_segments_total"),
            "selection_bias_verdict": (result.get("selection_bias") or {}).get("verdict"),
            "gate_verdict": gate["verdict"],
            "gate_reasons": gate["reasons"],
            "transition_zone": transition,
            "source_file": str(jf),
        }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(report, indent=2))

    print(f"[countertrend_gate_report] {len(report)} candidatos evaluados\n")
    for slot, r in report.items():
        print(f"  {slot:30s} veredicto={r['gate_verdict']:12s} n={r['n_trades']:>4} wr={r['wr']:>5}% "
              f"pnl=${r['pnl_total']:>9} segs={r['n_segments_qualified']}/{r['n_segments_total']} "
              f"bias={r['selection_bias_verdict']}")
        for reason in r["gate_reasons"]:
            print(f"      - {reason}")
        tz = r.get("transition_zone") or {}
        if isinstance(tz, dict) and "transition" in tz:
            t, e = tz["transition"], tz["established"]
            print(f"      [transparencia] transicion: n={t['n']} wr={t['wr']} pnl=${t['pnl']} | "
                  f"establecido: n={e['n']} wr={e['wr']} pnl=${e['pnl']}")
        print()

    return report


if __name__ == "__main__":
    run()
