#!/usr/bin/env python3
"""SIGMA Health Check — snapshot del estado del sistema.

Uso:
    python3 /opt/sigma/health_check.py
    python3 /opt/sigma/health_check.py --json

Reporta: servicios systemd, freshness de archivos, integridad de modelos,
coverage del universo, pipelines activos, paper trading state.

Cero side effects — solo lectura.
"""
import sys, json, time, os, subprocess
sys.path.insert(0, "/opt/sigma")
from pathlib import Path
from datetime import datetime

BASE = Path("/opt/sigma")
NOW = time.time()


def color(s, c):
    codes = {"g": 32, "r": 31, "y": 33, "b": 34, "c": 36, "m": 35}
    return "\033[" + str(codes.get(c, 0)) + "m" + str(s) + "\033[0m"


def check_services():
    services = [
        "sigma-web.service", "sigma-trainer.service", "sigma-pipeline.service",
        "sigma-telegram.service", "sigma-paper-trader.service",
    ]
    out = []
    for svc in services:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            status = (r.stdout or "").strip()
            out.append((svc, status))
        except Exception as e:
            out.append((svc, "error:" + type(e).__name__))
    return out


def check_freshness():
    files = [
        ("port_snapshot.json", "results/reports/port_snapshot.json", 3600),
        ("signals_cache.json", "results/signals_cache.json", 600),
        ("trade_state.json",   "results/trade_state.json",     86400),
        ("master_pipeline.log","results/reports/master_pipeline.log", 14400),
        ("watchdog.log",       "results/reports/watchdog.log",  900),
    ]
    out = []
    for name, rel, thr in files:
        p = BASE / rel
        if p.exists():
            age = NOW - p.stat().st_mtime
            out.append((name, int(age), thr, age > thr))
        else:
            out.append((name, -1, thr, True))
    return out


def check_portfolio():
    try:
        d = json.load(open(BASE / "results/reports/port_snapshot.json"))
        return {
            "port_cagr": d.get("port_cagr"),
            "port_cagr_with_kelly": d.get("port_cagr_with_kelly"),
            "n_grade_a": d.get("n_grade_a"),
            "total_trades": d.get("total_trades"),
            "champions": len(d.get("champions", {})),
            "trigger": d.get("trigger"),
        }
    except Exception as e:
        return {"error": str(e)}


def check_paper_trading():
    try:
        d = json.load(open(BASE / "results/trade_state.json"))
        p = d.get("portfolio", {})
        hist = [t for t in d.get("history", [])
                if t.get("status") in ("SL_HIT", "TP_HIT", "CLOSED", "MANUAL_CLOSE")]
        n_wins = sum(1 for t in hist if (t.get("pnl_pct", 0) or 0) > 0)
        equity = p.get("equity", 10000)
        initial = p.get("initial_capital", 10000)
        start = p.get("start_date", "2026-05-10")
        try:
            days = (datetime.now() - datetime.fromisoformat(start)).days
        except Exception:
            days = 0
        return {
            "equity": equity, "initial": initial,
            "return_pct": (equity - initial) / initial * 100,
            "days_running": days,
            "trades_closed": len(hist),
            "wins": n_wins, "losses": len(hist) - n_wins,
            "wr_pct": n_wins / max(len(hist), 1) * 100,
            "open_positions": len(d.get("open", {})),
            "circuit_breaker": d.get("circuit_breaker", {}),
            "max_dd_pct": p.get("max_dd_pct", 0),
        }
    except Exception as e:
        return {"error": str(e)}


def check_coverage():
    try:
        from utils.strategies import SHORT_STRATEGIES
    except Exception:
        SHORT_STRATEGIES = frozenset()
    SYMS = ["BTC", "ETH", "SOL", "BNB", "LTC", "XAU"]
    TFS = ["15m", "1h", "4h"]
    DIRS = ["long", "short"]
    cov = {}
    for tf in TFS:
        d = BASE / "models" / tf
        if not d.exists(): continue
        for p in d.glob("*.json"):
            try:
                data = json.load(open(p))
                sym = (data.get("symbol", "") or "").replace("/USDT", "").replace("/USD", "").upper()
                strat = data.get("strategy", "")
                m = data.get("metrics_oos") or {}
                if not (sym and strat and m.get("cagr", 0) > 0): continue
                direction = "short" if strat in SHORT_STRATEGIES else "long"
                cov.setdefault((sym, tf, direction), []).append(strat)
            except Exception:
                continue
    covered = sum(1 for sym in SYMS for tf in TFS for dr in DIRS if (sym, tf, dr) in cov)
    total = len(SYMS) * len(TFS) * len(DIRS)
    gaps = [(sym, tf, dr) for sym in SYMS for tf in TFS for dr in DIRS
            if (sym, tf, dr) not in cov]
    return {"covered": covered, "total": total, "gaps": gaps}


def check_running_pipelines():
    try:
        r = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True, timeout=5)
        active = []
        for line in (r.stdout or "").splitlines():
            if "asset_pipeline.py" in line and "grep" not in line:
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    pid = parts[0]
                    cmd = parts[1]
                    parts2 = cmd.split()
                    sym = tf = focus = ""
                    for i, t in enumerate(parts2):
                        if t == "--symbol" and i+1 < len(parts2): sym = parts2[i+1]
                        if t == "--tf" and i+1 < len(parts2): tf = parts2[i+1]
                        if t == "--focus" and i+1 < len(parts2): focus = parts2[i+1]
                    active.append({"pid": pid, "sym": sym, "tf": tf, "focus": focus})
        return active
    except Exception as e:
        return [{"error": str(e)}]


def render_text(result):
    NL = "\n"
    out = []
    out.append(color("=" * 60, "b"))
    out.append(color("  SIGMA HEALTH CHECK  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "c"))
    out.append(color("=" * 60, "b"))

    out.append(color(NL + "[SERVICIOS]", "y"))
    for svc, status in result["services"]:
        cc = "g" if status == "active" else "r"
        line = "  " + color(svc.ljust(38), cc) + " " + color(status, cc)
        out.append(line)

    out.append(color(NL + "[FRESHNESS DE ARCHIVOS]", "y"))
    for name, age_sec, thr, stale in result["freshness"]:
        cc = "r" if stale else "g"
        age_str = "MISSING" if age_sec < 0 else (str(age_sec // 60) + "m " + str(age_sec % 60) + "s")
        out.append("  " + color(name.ljust(28), cc) + " " + age_str.ljust(14) + " (umbral " + str(thr // 60) + "min)")

    out.append(color(NL + "[PORTAFOLIO]", "y"))
    p = result["portfolio"]
    if "error" in p:
        out.append("  " + color("ERROR: " + p["error"], "r"))
    else:
        pcagr = p.get("port_cagr") or 0
        pkelly = p.get("port_cagr_with_kelly") or 0
        out.append("  port_cagr           " + color(("%.2f%%" % pcagr), "c"))
        out.append("  port_cagr_w_kelly   " + color(("%.2f%%" % pkelly), "c"))
        out.append("  n_grade_a           " + str(p.get("n_grade_a")))
        out.append("  total_trades        " + str(p.get("total_trades")))
        out.append("  champions           " + str(p.get("champions")) + "/" + str(p.get("n_grade_a","?")))
        out.append("  last trigger        " + repr(p.get("trigger", "?")))

    out.append(color(NL + "[PAPER TRADING]", "y"))
    pt = result["paper"]
    if "error" in pt:
        out.append("  " + color("ERROR: " + pt["error"], "r"))
    else:
        ret = pt["return_pct"]
        ret_c = "g" if ret >= 0 else "r"
        out.append("  equity              " + color(("$%.2f" % pt["equity"]), "c") + " (inicio $" + ("%.0f" % pt["initial"]) + ")")
        out.append("  retorno             " + color(("%+.2f%%" % ret), ret_c) + " en " + str(pt["days_running"]) + "d")
        out.append("  trades cerrados     " + str(pt["trades_closed"]) + " (" + str(pt["wins"]) + "W / " + str(pt["losses"]) + "L, WR " + ("%.1f%%" % pt["wr_pct"]) + ")")
        out.append("  posiciones abiertas " + str(pt["open_positions"]))
        cb = pt.get("circuit_breaker", {})
        if cb.get("paused") or cb.get("funding_emergency"):
            out.append("  " + color("CIRCUIT BREAKER:", "r") + " " + str(cb))
        out.append("  max_dd_pct          " + str(pt.get("max_dd_pct", 0)) + "%")
        gate_t = "OK " if pt["trades_closed"] >= 30 else "-- "
        gate_d = "OK " if pt["days_running"] >= 21 else "-- "
        out.append("  gate trades         [" + gate_t + "] " + str(pt["trades_closed"]) + "/30")
        out.append("  gate dias           [" + gate_d + "] " + str(pt["days_running"]) + "/21")

    out.append(color(NL + "[COBERTURA M1:5x4x2 + M2:2x2x2]", "y"))
    cov = result["coverage"]
    cov_pct = cov["covered"] / cov["total"] * 100
    cov_c = "g" if cov_pct >= 90 else "y" if cov_pct >= 70 else "r"
    out.append("  " + color((str(cov["covered"]) + "/" + str(cov["total"]) + " slots (" + ("%.0f" % cov_pct) + "%)"), cov_c))
    for g in cov["gaps"][:10]:
        out.append("    " + color("GAP", "r") + " " + g[0] + " " + g[1] + " " + g[2])

    out.append(color(NL + "[PIPELINES ACTIVOS]", "y"))
    for proc in result["pipelines"]:
        if "error" in proc:
            out.append("  " + color("ERROR: " + proc["error"], "r"))
        else:
            out.append("  pid=" + str(proc["pid"]).ljust(7) + " " + proc["sym"].ljust(12) + " " + proc["tf"].ljust(4) + " focus=" + proc["focus"])
    if not result["pipelines"]:
        out.append("  " + color("Ningun pipeline activo", "y"))

    out.append(color(NL + "=" * 60, "b"))
    return NL.join(out)


def check_recent_trades(hours=12):
    try:
        d = json.load(open(BASE / "results/trade_state.json"))
    except Exception as e:
        return {"error": str(e)}
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=hours)
    hist = d.get("history", [])
    closed_recent = []
    opened_recent = []
    for t in hist:
        opened_at = t.get("opened_at", "")
        closed_at = t.get("closed_at", "")
        try:
            if opened_at and datetime.fromisoformat(opened_at.replace("Z","").split(".")[0]) >= cutoff:
                opened_recent.append(t)
            if closed_at and datetime.fromisoformat(closed_at.replace("Z","").split(".")[0]) >= cutoff:
                closed_recent.append(t)
        except Exception:
            pass
    open_now = list(d.get("open", {}).values())
    open_now = [t for t in open_now if t.get("status") == "open"]
    return {
        "open_now": open_now,
        "opened_recent": opened_recent,
        "closed_recent": closed_recent,
        "hours_window": hours,
    }


def check_recent_champions(hours=12):
    log_path = BASE / "results/reports/tg_champion_sent.log"
    if not log_path.exists():
        return []
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []
    for line in log_path.read_text().splitlines():
        try:
            ts_str = line.split()[0]
            ts = datetime.fromisoformat(ts_str.replace("Z","").split(".")[0])
            if ts >= cutoff:
                recent.append(line.strip())
        except Exception:
            continue
    return recent


def render_overnight(result):
    NL = chr(10)
    out = []
    out.append(color("=" * 60, "b"))
    out.append(color("  SIGMA OVERNIGHT REPORT  " + datetime.now().strftime("%Y-%m-%d %H:%M"), "c"))
    out.append(color("=" * 60, "b"))

    p = result["paper"]
    if "error" not in p:
        ret = p["return_pct"]
        ret_c = "g" if ret >= 0 else "r"
        out.append(color(NL + "[EQUITY]", "y"))
        out.append("  equity:    " + color("$" + ("%.2f" % p["equity"]), "c") + "  (inicio $" + ("%.0f" % p["initial"]) + ")")
        out.append("  retorno:   " + color(("%+.2f%%" % ret), ret_c) + "  en " + str(p["days_running"]) + "d")

    rt = result.get("recent_trades", {})
    out.append(color(NL + "[TRADES (" + str(rt.get("hours_window", 12)) + "h)]", "y"))
    out.append("  abiertos:  " + str(len(rt.get("opened_recent", []))))
    out.append("  cerrados:  " + str(len(rt.get("closed_recent", []))))
    out.append("  open ahora: " + str(len(rt.get("open_now", []))))

    for t in rt.get("open_now", []):
        sym = t.get("sym","?"); tf = t.get("tf","?"); dr = t.get("direction","?")
        strat = t.get("strategy","?"); entry = t.get("entry",0)
        sl = t.get("sl",0); tp = t.get("tp",0)
        out.append("    " + color("OPEN  ", "y") + sym + " " + tf + " " + dr + " " + strat + "  entry=$" + ("%.2f" % entry) + "  SL=$" + ("%.2f" % sl) + "  TP=$" + ("%.2f" % tp))

    for t in rt.get("closed_recent", []):
        sym = t.get("sym","?"); tf = t.get("tf","?"); dr = t.get("direction","?")
        strat = t.get("strategy","?"); status = t.get("status","?")
        pnl_pct = t.get("pnl_pct", 0) or 0
        pnl_dol = t.get("pnl_dollar", 0) or 0
        c_p = "g" if pnl_pct > 0 else "r"
        ts_short = (t.get("closed_at","") or "")[:16]
        out.append("    " + color("CLOSED", c_p) + " " + ts_short + " " + sym + " " + tf + " " + dr + " " + strat + "  " + status + "  " + color(("%+.2f%%" % pnl_pct), c_p) + "  ($" + ("%+.2f" % pnl_dol) + ")")

    rc = result.get("recent_champions", [])
    out.append(color(NL + "[CHAMPIONS NUEVOS (" + str(rt.get("hours_window", 12)) + "h)]", "y"))
    out.append("  total: " + str(len(rc)))
    for line in rc[-10:]:
        out.append("    " + line[:120])

    cov = result["coverage"]
    cov_pct = cov["covered"] / cov["total"] * 100
    cov_c = "g" if cov_pct >= 90 else "y" if cov_pct >= 70 else "r"
    out.append(color(NL + "[COBERTURA M1:5x4x2 + M2:2x2x2]", "y"))
    out.append("  " + color((str(cov["covered"]) + "/" + str(cov["total"]) + " slots (" + ("%.0f" % cov_pct) + "%)"), cov_c))
    if cov["gaps"]:
        for g in cov["gaps"][:6]:
            out.append("    " + color("GAP", "r") + " " + g[0] + " " + g[1] + " " + g[2])

    services = result["services"]
    bad = [s for s, st in services if st != "active"]
    out.append(color(NL + "[SERVICIOS]", "y"))
    if not bad:
        out.append("  " + color("Todos los 5 servicios activos", "g"))
    else:
        out.append("  " + color("CAIDOS: " + ", ".join(s for s, _ in bad), "r"))

    out.append(color(NL + "=" * 60, "b"))
    return NL.join(out)


def main():
    json_mode = "--json" in sys.argv
    overnight_mode = "--overnight" in sys.argv
    hours_window = 12
    for arg in sys.argv:
        if arg.startswith("--hours="):
            try: hours_window = int(arg.split("=")[1])
            except: pass

    result = {
        "timestamp": datetime.now().isoformat(),
        "services": check_services(),
        "freshness": check_freshness(),
        "portfolio": check_portfolio(),
        "paper": check_paper_trading(),
        "coverage": check_coverage(),
        "pipelines": check_running_pipelines(),
    }
    if overnight_mode:
        result["recent_trades"] = check_recent_trades(hours=hours_window)
        result["recent_champions"] = check_recent_champions(hours=hours_window)

    if json_mode:
        print(json.dumps(result, indent=2, default=str))
    elif overnight_mode:
        print(render_overnight(result))
    else:
        print(render_text(result))


if __name__ == "__main__":
    main()
