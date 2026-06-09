#!/usr/bin/env python3
"""
Risk Budget Monitor — portfolio volatility tracker.

Uses the correct formula for sequential (not simultaneous) trades:
  annual_vol = trade_pnl_std × sqrt(trades_per_year)

Targets for a crypto-focused system (higher than multi-asset funds):
  Target: 30% annual vol
  OVER_BUDGET: > 50% (too aggressive, reduce sizing)
  UNDER_BUDGET: < 15% (underusing risk budget, can increase)
"""
import json, math, time
from pathlib import Path

BASE     = Path("/opt/sigma")
TS_FILE  = BASE / "results/trade_state.json"
OUT_FILE = BASE / "results/reports/risk_budget.json"

VOL_TARGET_ANNUAL_PCT = 30.0   # 30% annual — realistic for crypto momentum
VOL_BUDGET_HIGH_PCT   = 50.0   # > 50% → too aggressive
VOL_BUDGET_LOW_PCT    = 15.0   # < 15% → underusing risk budget


def _std(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    mean = sum(vals) / n
    return math.sqrt(sum((v - mean)**2 for v in vals) / (n - 1))


def compute():
    try:
        ts = json.load(open(TS_FILE))
    except Exception as e:
        return {"error": str(e)}

    hist = ts.get("history", []) or []
    port = ts.get("portfolio", {}) or {}
    equity  = port.get("equity", 10000)
    initial = port.get("initial_capital", 10000)

    pnls = [t.get("pnl_pct", 0) or 0
            for t in hist
            if t.get("status") in ("TP_HIT", "SL_HIT", "CLOSED", "MANUAL_CLOSE")]
    n = len(pnls)

    if n < 3:
        result = {
            "computed_at": time.strftime("%Y-%m-%d %H:%M"),
            "status": "INSUFFICIENT_DATA",
            "n_trades": n,
            "vol_target_annual_pct": VOL_TARGET_ANNUAL_PCT,
        }
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        json.dump(result, open(OUT_FILE, "w"), indent=2)
        return result

    # Days elapsed from first trade
    days_elapsed = 30.0  # default
    try:
        from datetime import datetime as _dt
        for t in hist:
            ts_raw = t.get("opened_at") or t.get("open_time")
            if ts_raw:
                dt = _dt.fromisoformat(str(ts_raw).split(".")[0].replace("T", " "))
                start_ts = dt.timestamp()
                days_elapsed = max((time.time() - start_ts) / 86400.0, 1.0)
                break
    except Exception:
        pass

    # Core formula: annual_vol = trade_std × sqrt(trades_per_year)
    trade_std        = _std(pnls)
    trades_per_year  = n / max(days_elapsed / 365.0, 1/365.0)
    annual_vol_pct   = trade_std * math.sqrt(trades_per_year)

    # Recent 10-trade trend
    recent           = pnls[-10:] if len(pnls) >= 10 else pnls
    recent_std       = _std(recent)
    recent_tpy       = len(recent) / max(days_elapsed / 365.0 * (len(recent)/n), 1/365.0)
    recent_annual_pct= recent_std * math.sqrt(recent_tpy)

    # Budget status
    if annual_vol_pct > VOL_BUDGET_HIGH_PCT:
        status = "OVER_BUDGET"
        rec = f"Reduce kelly ≈{(1 - VOL_TARGET_ANNUAL_PCT/annual_vol_pct)*100:.0f}% (→ ~{3.3*VOL_TARGET_ANNUAL_PCT/annual_vol_pct:.1f}%)"
    elif annual_vol_pct < VOL_BUDGET_LOW_PCT:
        status = "UNDER_BUDGET"
        rec = f"Can increase kelly ≈{(VOL_TARGET_ANNUAL_PCT/annual_vol_pct - 1)*100:.0f}% (→ ~{3.3*VOL_TARGET_ANNUAL_PCT/annual_vol_pct:.1f}%)"
    else:
        status = "ON_TARGET"
        rec = "No change needed"

    vol_mult   = VOL_TARGET_ANNUAL_PCT / max(annual_vol_pct, 0.1)
    kelly_adj  = round(min(max(3.3 * vol_mult, 1.0), 8.0), 2)
    utilization= annual_vol_pct / VOL_TARGET_ANNUAL_PCT * 100

    result = {
        "computed_at":    time.strftime("%Y-%m-%d %H:%M"),
        "status":         status,
        "recommendation": rec,
        "portfolio": {
            "equity":      equity,
            "return_pct":  round((equity / initial - 1) * 100, 2),
            "n_trades":    n,
            "days_elapsed":round(days_elapsed, 1),
        },
        "vol_metrics": {
            "trade_std_pct":          round(trade_std, 3),
            "trades_per_year":        round(trades_per_year, 1),
            "annual_vol_pct":         round(annual_vol_pct, 1),
            "recent_10t_annual_pct":  round(recent_annual_pct, 1),
            "vol_target_pct":         VOL_TARGET_ANNUAL_PCT,
            "vol_budget_high_pct":    VOL_BUDGET_HIGH_PCT,
            "vol_budget_low_pct":     VOL_BUDGET_LOW_PCT,
            "utilization_pct":        round(utilization, 1),
        },
        "kelly_guidance": {
            "current_pct":             3.3,
            "vol_multiplier":          round(vol_mult, 3),
            "vol_adjusted_suggestion": kelly_adj,
            "note": "Suggestion based purely on vol budget. Apply only at 30+ trade gate."
        }
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT_FILE, "w"), indent=2)
    return result


if __name__ == "__main__":
    r = compute()
    vm = r.get("vol_metrics", {})
    kg = r.get("kelly_guidance", {})
    print(f"Status:        {r['status']}")
    print(f"Annual vol:    {vm.get('annual_vol_pct'):.1f}%  (target {vm.get('vol_target_pct')}%,  hi-limit {vm.get('vol_budget_high_pct')}%)")
    print(f"Utilization:   {vm.get('utilization_pct'):.0f}%")
    print(f"Recent trend:  {vm.get('recent_10t_annual_pct'):.1f}%  (last 10 trades)")
    print(f"Recommendation:{r['recommendation']}")
    print(f"Kelly now:     {kg['current_pct']}%  →  vol-adjusted: {kg['vol_adjusted_suggestion']}%")
