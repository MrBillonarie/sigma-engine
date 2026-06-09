"""
Performance tracker — live vs backtest comparison per strategy.
Hedge fund style: expected WR/CAGR vs actual, Wilson CI, gate progress.
"""
import json, os, time, math, glob, sys
from pathlib import Path

BASE     = Path("/opt/sigma")
TS_FILE  = BASE / "results/trade_state.json"
OUT_FILE = BASE / "results/reports/performance_tracker.json"
LIVE_GATE = 30   # trades needed before live consideration

# Load short strategies without importing asset_pipeline (avoids numpy)
sys.path.insert(0, str(BASE))
try:
    from utils.strategies import SHORT_STRATEGIES as _SHORT
    SHORT_STRATS = set(_SHORT)
except Exception:
    SHORT_STRATS = set()


def _load_expectations():
    from utils.robustness import robustness_score
    exp = {}
    for jf in sorted(glob.glob(str(BASE / "models/*/*.json"))):
        if "archive" in jf:
            continue
        try:
            d     = json.load(open(jf))
            strat = d.get("strategy","")
            sym   = (d.get("symbol","") or "").replace("/USDT","").replace("/USD","")
            tf    = jf.split("/")[-2]
            if not strat or not sym:
                continue
            r = robustness_score(d)
            if r["action"] not in ("PASS_LIVE","PAPER_ONLY"):
                continue
            m    = d.get("metrics_oos",{}) or {}
            key  = f"{sym}/{tf}/{strat}"
            direction = "short" if strat in SHORT_STRATS else "long"
            if key not in exp or r["final"] > exp[key].get("_rob",0):
                exp[key] = {
                    "sym":sym,"tf":tf,"strat":strat,"direction":direction,
                    "bt_wr":    m.get("wr",0)   or 0,
                    "bt_cagr":  m.get("cagr",0) or 0,
                    "bt_trades":m.get("trades",0) or 0,
                    "bt_dd":    m.get("dd",0)   or 0,
                    "action":   r["action"],
                    "grade":    r.get("grade","?"),
                    "_rob":     r["final"],
                }
        except Exception:
            pass
    return exp


def _wilson_ci(wins, n, z=1.645):
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    m = (z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / d
    return max(0, c-m), min(1, c+m)


# ─── Live Gate Criteria (go/no-go for activating binance_executor) ────────────
LIVE_GATE_CRITERIA = {
    "min_wr":        55.0,   # portfolio WR ≥ 55%
    "max_dd":       -15.0,   # max drawdown ≤ -15%
    "min_pf":         1.2,   # profit factor ≥ 1.2
    "min_trades":    30,     # gate trades
    "min_days":      21,     # days running
    "min_beating":    1,     # ≥1 strategy beating backtest with CI
    "min_equity":  9000.0,   # don't go live if equity < $9,000 (deep DD)
}

# ─── Telegram helpers ─────────────────────────────────────────────────────────
def _tg_send(msg):
    """Send message to Telegram group. Silent on error."""
    import urllib.request as _ur, json as _j2, os as _o2
    try:
        from pathlib import Path as _P
        tok = (_P("/opt/sigma/config/tg_token.txt").read_text().strip()
               if _P("/opt/sigma/config/tg_token.txt").exists()
               else _o2.environ.get("SIGMA_TG_TOKEN",""))
        if not tok:
            return
        _ur.urlopen(_ur.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=_j2.dumps({"chat_id":"-1003787411069","text":msg,
                            "parse_mode":"Markdown","disable_web_page_preview":True}).encode(),
            headers={"Content-Type":"application/json"}), timeout=10)
    except Exception:
        pass


def _cooldown_ok(key, hours=6):
    """Returns True if last alert for key was >hours ago."""
    import time as _t
    from pathlib import Path as _P
    f = _P(f"/opt/sigma/results/reports/alert_cooldown_{key}.txt")
    if f.exists():
        try:
            last = float(f.read_text().strip())
            if _t.time() - last < hours * 3600:
                return False
        except Exception:
            pass
    f.write_text(str(_t.time()))
    return True


# ─── Kelly real calculator ────────────────────────────────────────────────────
def _calc_kelly_real(history):
    """Full Kelly + Half Kelly from live trade history."""
    wins  = [t.get("pnl_pct", 0) for t in history if (t.get("pnl_pct") or 0) > 0]
    losses= [abs(t.get("pnl_pct", 0)) for t in history if (t.get("pnl_pct") or 0) < 0]
    n     = len(wins) + len(losses)
    if n < 10 or not wins or not losses:
        return None
    wr       = len(wins) / n
    avg_win  = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    pf       = sum(wins) / max(sum(losses), 0.001)
    # Kelly: f* = (WR/avg_loss - (1-WR)/avg_win) — not standard; use standard:
    # f* = p/a - q/b where p=wr, q=1-wr, a=avg_loss, b=avg_win
    kelly_full = (wr / avg_loss - (1 - wr) / avg_win) if avg_win > 0 else 0
    kelly_full = max(0, kelly_full) * 100  # to %
    kelly_half = kelly_full / 2
    return {
        "n": n, "wr_pct": round(wr*100,1),
        "avg_win_pct": round(avg_win,3), "avg_loss_pct": round(avg_loss,3),
        "profit_factor": round(pf,3),
        "kelly_full_pct": round(kelly_full,2),
        "kelly_half_pct": round(kelly_half,2),
        "recommended_pct": round(min(kelly_half, 5.0), 2),  # cap at 5%
    }


# ─── Gate evaluator ──────────────────────────────────────────────────────────
def _eval_live_gate(result, kelly_data=None):
    """Evaluate go/no-go criteria. Returns dict with pass/fail per criterion."""
    p    = result["portfolio"]
    g    = result["gate_summary"]
    strats = result.get("strategies", [])
    n    = p.get("total_trades", 0)
    wr   = p.get("portfolio_wr") or 0
    dd   = p.get("max_dd_pct", 0) or 0
    pf   = p.get("profit_factor") or 0
    eq   = p.get("equity", 10000)
    days = p.get("days_elapsed", 0) or 0
    beating = g.get("beating_backtest", 0)

    crit = LIVE_GATE_CRITERIA
    checks = {
        "trades":    {"val": n,       "req": f">= {crit['min_trades']}",   "ok": n >= crit["min_trades"]},
        "days":      {"val": days,    "req": f">= {crit['min_days']}d",    "ok": days >= crit["min_days"]},
        "wr":        {"val": f"{wr:.1f}%", "req": f">= {crit['min_wr']}%", "ok": wr >= crit["min_wr"]},
        "max_dd":    {"val": f"{dd:.1f}%", "req": f">= {crit['max_dd']}%", "ok": dd >= crit["max_dd"]},
        "pf":        {"val": f"{pf:.2f}", "req": f">= {crit['min_pf']}",  "ok": pf >= crit["min_pf"]},
        "equity":    {"val": f"${eq:,.0f}", "req": f">= ${crit['min_equity']:,.0f}", "ok": eq >= crit["min_equity"]},
        "beating_bt":{"val": beating, "req": f">= {crit['min_beating']}",  "ok": beating >= crit["min_beating"]},
    }
    passed = [k for k,v in checks.items() if v["ok"]]
    failed = [k for k,v in checks.items() if not v["ok"]]
    verdict = "GO" if not failed else "NO-GO"
    return {"verdict": verdict, "passed": passed, "failed": failed, "checks": checks}


def _send_gate_alert(result, gate_eval, kelly_data):
    """Send Telegram alert when 30-trade gate is crossed."""
    v = gate_eval["verdict"]
    p = result["portfolio"]
    emoji = "✅" if v == "GO" else "⛔"
    lines = [
        f"{emoji} *LIVE GATE EVALUACIÓN — {v}*",
        "",
        f"Se alcanzaron {p.get('total_trades',0)} trades en papel.",
        "",
        "*Criterios:*",
    ]
    for k, chk in gate_eval["checks"].items():
        icon = "✅" if chk["ok"] else "❌"
        lines.append(f"  {icon} {k}: {chk['val']} (req {chk['req']})")
    if kelly_data:
        lines += [
            "",
            f"*Kelly calculado ({kelly_data['n']} trades):*",
            f"  WR: {kelly_data['wr_pct']}% | PF: {kelly_data['profit_factor']}",
            f"  Kelly full: {kelly_data['kelly_full_pct']:.1f}%",
            f"  Kelly half: {kelly_data['kelly_half_pct']:.1f}%",
            f"  *Recomendado: {kelly_data['recommended_pct']:.1f}%*",
        ]
    if v == "GO":
        lines += ["", "Sistema listo para considerar activación live."]
    else:
        lines += ["", f"Criterios fallidos: {', '.join(gate_eval['failed'])}"]
        lines += ["Continuar en papel hasta cumplir todos los criterios."]
    _tg_send("\n".join(lines))


def _send_divergence_alerts(strategies):
    """Alert if any strategy's live WR is statistically below backtest (2σ)."""
    import math as _m
    alerts = []
    for s in strategies:
        n = s.get("live_n", 0)
        if n < 5:
            continue
        live_wr = (s.get("live_wr") or 0) / 100
        bt_wr   = (s.get("bt_wr") or 0) / 100
        if bt_wr <= 0:
            continue
        # 2σ below backtest: threshold = bt_wr - 2*sqrt(bt_wr*(1-bt_wr)/n)
        sigma = _m.sqrt(bt_wr * (1 - bt_wr) / n)
        threshold = bt_wr - 2 * sigma
        if live_wr < threshold:
            alerts.append({
                "key": s["key"],
                "live_wr": round(live_wr*100,1),
                "bt_wr":   round(bt_wr*100,1),
                "threshold": round(threshold*100,1),
                "n": n,
                "sigma": round(sigma*100,2),
            })
    if alerts and _cooldown_ok("divergence_alert", hours=12):
        lines = ["⚠️ *ALERTA: Divergencia Live vs Backtest (2σ)*", ""]
        for a in alerts:
            lines.append(
                f"  {a['key']}\n"
                f"  WR live: {a['live_wr']}% vs BT: {a['bt_wr']}% "
                f"(umbral: {a['threshold']}%, n={a['n']})"
            )
        lines += ["", "Monitorear — puede ser varianza normal o degradación real."]
        _tg_send("\n".join(lines))
    return alerts


def compute():
    ts   = json.load(open(TS_FILE))
    hist = ts.get("history",[]) or []
    port = ts.get("portfolio",{}) or {}
    equity  = port.get("equity",10000)
    initial = port.get("initial_capital",10000)

    exp = _load_expectations()

    # Aggregate live trades per (sym/tf/strat)
    live: dict = {}
    for t in hist:
        strat = t.get("strategy","")
        sym   = (t.get("sym","") or t.get("symbol","") or "").replace("/USDT","").replace("/USD","")
        tf    = t.get("tf","")
        pnl   = t.get("pnl_pct",0) or 0
        status= t.get("status","")
        if not strat:
            continue
        k = f"{sym}/{tf}/{strat}"
        if k not in live:
            live[k] = {"wins":0,"losses":0,"pnl":[],"sym":sym,"tf":tf,"strat":strat}
        if status == "TP_HIT":
            live[k]["wins"]   += 1
        elif status == "SL_HIT":
            live[k]["losses"] += 1
        live[k]["pnl"].append(pnl)

    # Days since start (approx from first trade)
    start_ts = None
    for t in hist:
        ts_raw = t.get("entry_time") or t.get("open_time") or t.get("ts")
        if ts_raw:
            try:
                import datetime
                if isinstance(ts_raw, (int,float)):
                    start_ts = ts_raw
                else:
                    start_ts = datetime.datetime.fromisoformat(str(ts_raw)).timestamp()
                break
            except Exception:
                pass
    days_elapsed = (time.time() - start_ts) / 86400 if start_ts else 30

    all_keys = sorted(set(list(exp.keys()) + list(live.keys())))
    strategies = []

    for key in all_keys:
        e = exp.get(key, {})
        l = live.get(key, {})

        wins   = l.get("wins", 0)
        losses = l.get("losses", 0)
        n      = wins + losses
        pnl_list = l.get("pnl", [])
        live_wr  = wins / n * 100 if n > 0 else None
        live_pnl = sum(pnl_list)

        ci_lo, ci_hi = _wilson_ci(wins, n)
        bt_wr = e.get("bt_wr", 0)
        wr_delta = round(live_wr - bt_wr, 1) if live_wr is not None else None
        wr_within_ci = (ci_lo*100 <= bt_wr <= ci_hi*100) if n > 0 else None

        # Beat backtest? (live WR exceeds expected with CI lower bound > BT WR)
        beating = (ci_lo * 100 > bt_wr) if n >= 5 else None

        rate     = n / max(days_elapsed, 1)
        progress = min(100, n / LIVE_GATE * 100)
        days_to  = max(0, LIVE_GATE - n) / rate if rate > 0 else None

        strategies.append({
            "key": key,
            "sym": e.get("sym", l.get("sym","")),
            "tf":  e.get("tf",  l.get("tf","")),
            "strat": e.get("strat", l.get("strat","")),
            "direction": e.get("direction","?"),
            "action":  e.get("action","?"),
            "grade":   e.get("grade","?"),
            "bt_wr":    e.get("bt_wr",0),
            "bt_cagr":  e.get("bt_cagr",0),
            "bt_trades":e.get("bt_trades",0),
            "bt_dd":    e.get("bt_dd",0),
            "live_n":      n,
            "live_wins":   wins,
            "live_losses": losses,
            "live_wr":     round(live_wr,1) if live_wr is not None else None,
            "live_pnl":    round(live_pnl,2),
            "live_avg_pnl":round(live_pnl/n,2) if n>0 else None,
            "wr_delta":    wr_delta,
            "wr_ci_lo":    round(ci_lo*100,1),
            "wr_ci_hi":    round(ci_hi*100,1),
            "wr_within_ci":wr_within_ci,
            "beating_bt":  beating,
            "progress_pct":round(progress,1),
            "trades_needed":max(0,LIVE_GATE-n),
            "days_to_gate": round(days_to,1) if days_to is not None else None,
        })

    total_n    = sum(r["live_n"] for r in strategies)
    total_wins = sum(r["live_wins"] for r in strategies)
    port_wr    = total_wins/total_n*100 if total_n else None

    result = {
        "computed_at": time.strftime("%Y-%m-%d %H:%M"),
        "portfolio": {
            "equity":       equity,
            "initial":      initial,
            "return_pct":   round((equity/initial-1)*100,2),
            "total_trades": total_n,
            "total_wins":   total_wins,
            "total_losses": total_n - total_wins,
            "portfolio_wr": round(port_wr,1) if port_wr else None,
            "days_elapsed": round(days_elapsed,1),
        },
        "gate_summary": {
            "live_gate":        LIVE_GATE,
            "strategies_ready": len([r for r in strategies if r["live_n"]>=LIVE_GATE]),
            "tracking":         len([r for r in strategies if 0<r["live_n"]<LIVE_GATE]),
            "no_data":          len([r for r in strategies if r["live_n"]==0]),
            "beating_backtest": len([r for r in strategies if r.get("beating_bt")]),
        },
        "strategies": sorted(strategies, key=lambda x: -x["live_n"]),
    }
    # ── Kelly real ──────────────────────────────────────────────────────────
    _kelly = _calc_kelly_real(hist)

    # ── Gate evaluation (always computed) ───────────────────────────────────
    _port = result["portfolio"]
    _wins_list  = [t.get("pnl_pct",0) for t in hist if (t.get("pnl_pct") or 0) > 0]
    _loss_list  = [abs(t.get("pnl_pct",0)) for t in hist if (t.get("pnl_pct") or 0) < 0]
    _pf = (sum(_wins_list) / max(sum(_loss_list), 0.001)) if _wins_list else 0
    _port["profit_factor"] = round(_pf, 3)
    _port["max_dd_pct"]    = json.load(open(str(BASE / "results/reports/portfolio_risk.json"))).get("portfolio",{}).get("max_dd_pct", 0) if (BASE / "results/reports/portfolio_risk.json").exists() else 0
    _gate_eval = _eval_live_gate(result, _kelly)

    result["gate_evaluation"] = _gate_eval
    result["kelly"] = _kelly

    # ── Send alerts ──────────────────────────────────────────────────────────
    _n = _port.get("total_trades", 0)
    _gate_file = BASE / "results/reports/gate_alert_sent.txt"
    if _n >= LIVE_GATE and not _gate_file.exists():
        _send_gate_alert(result, _gate_eval, _kelly)
        _gate_file.write_text(str(_n))
    elif _n >= LIVE_GATE and _gate_file.exists():
        # Re-send if criteria changed and we crossed another 10-trade milestone
        try:
            _last_n = int(_gate_file.read_text().strip())
            if _n >= _last_n + 10 and _cooldown_ok("gate_recheck", hours=24):
                _send_gate_alert(result, _gate_eval, _kelly)
                _gate_file.write_text(str(_n))
        except Exception:
            pass

    _divs = _send_divergence_alerts(result["strategies"])
    result["divergence_alerts"] = [a["key"] for a in _divs]

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT_FILE,"w"), indent=2)
    return result


if __name__ == "__main__":
    r = compute()
    p = r["portfolio"]
    g = r["gate_summary"]
    print(f"Portfolio: ${p['equity']:.0f} | return={p['return_pct']:+.1f}% | WR={p['portfolio_wr']}% | {p['total_trades']} trades over {p['days_elapsed']:.0f} days")
    print(f"Gate ({g['live_gate']} trades): ready={g['strategies_ready']} tracking={g['tracking']} no_data={g['no_data']} beating_bt={g['beating_backtest']}")
    print()
    print(f"  {'KEY':<35} {'N':>3} {'LWR':>6} {'BWR':>6} {'DELTA':>7} {'CI_90':>12} {'BEAT':>5} {'DAYS':>6}")
    print(f"  {'-'*90}")
    for s in r["strategies"]:
        if s["live_n"] > 0:
            lwr   = f"{s['live_wr']:.0f}%" if s['live_wr'] is not None else "?"
            bwr   = f"{s['bt_wr']:.0f}%"
            delta = f"{s['wr_delta']:+.0f}%" if s['wr_delta'] is not None else "?"
            ci    = f"{s['wr_ci_lo']:.0f}-{s['wr_ci_hi']:.0f}%"
            beat  = "YES" if s.get("beating_bt") else ("?" if s["live_n"]<5 else "NO")
            days  = f"{s['days_to_gate']:.0f}d" if s['days_to_gate'] else "done"
            print(f"  {s['key']:<35} {s['live_n']:>3} {lwr:>6} {bwr:>6} {delta:>7} {ci:>12} {beat:>5} {days:>6}")
