#!/usr/bin/env python3
"""sprint_status.py - monitor del sprint de cobertura.

Uso:
    /opt/sigma_env/bin/python /opt/sigma/sprint_status.py
"""
import sys, json, os, subprocess, time
from pathlib import Path
sys.path.insert(0, "/opt/sigma")

NL = chr(10)

def c(s, col):
    codes = {"g":32,"r":31,"y":33,"b":34,"c":36,"m":35}
    return "\033[" + str(codes.get(col,0)) + "m" + str(s) + "\033[0m"

print(c("=" * 70, "b"))
print(c("  SIGMA SPRINT STATUS  " + time.strftime("%H:%M:%S"), "c"))
print(c("=" * 70, "b"))

# 1. Trials activos
print(c(NL + "[TRIALS ACTIVOS]", "y"))
r = subprocess.run(["ps","-eo","pid,etime,cmd"], capture_output=True, text=True)
trials = []
for line in r.stdout.split(NL):
    if "asset_pipeline.py" in line and "grep" not in line:
        parts = line.split(None, 2)
        if len(parts) >= 3:
            pid, etime, cmd = parts[0], parts[1], parts[2]
            ts = cmd.split()
            sym = tf = focus = "?"
            for i,t in enumerate(ts):
                if t == "--symbol" and i+1 < len(ts): sym = ts[i+1].replace("/USDT","")
                if t == "--tf" and i+1 < len(ts): tf = ts[i+1]
                if t == "--focus" and i+1 < len(ts): focus = ts[i+1]
            trials.append((pid, etime, sym, tf, focus))
print("  Total: " + str(len(trials)))
for pid, etime, sym, tf, focus in trials:
    print("    " + sym.ljust(6) + " " + tf.ljust(4) + " focus=" + focus.ljust(7) + " uptime=" + etime + " pid=" + pid)

# 2. Recursos
print(c(NL + "[RECURSOS]", "y"))
mem = subprocess.run(["free","-m"], capture_output=True, text=True).stdout.split(NL)[1].split()
print("  RAM total/used/available: " + mem[1] + "/" + mem[2] + "/" + mem[6] + " MB")
up = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
print("  " + up)

# 3. Cobertura actual
print(c(NL + "[COBERTURA UNIVERSO]", "y"))
try:
    from utils.strategies import SHORT_STRATEGIES
    SYMS = ["BTC","ETH","SOL","BNB","LTC"]
    TFS = ["15m","1h","4h"]
    DIRS = ["long","short"]
    covered = set()
    for tf in TFS:
        for fp in Path("/opt/sigma/models/" + tf).glob("*.json"):
            try:
                d = json.load(open(fp))
                m = d.get("metrics_oos") or {}
                if (m.get("cagr",0) or 0) <= 0: continue
                sym = (d.get("symbol","") or "").replace("/USDT","").replace("/USD","").upper()
                strat = d.get("strategy","")
                if not (sym and strat): continue
                direction = "short" if strat in SHORT_STRATEGIES else "long"
                covered.add((sym, tf, direction))
            except: pass
    total = len(SYMS) * len(TFS) * len(DIRS)
    gaps = [(s,t,d) for s in SYMS for t in TFS for d in DIRS if (s,t,d) not in covered]
    cov_pct = len(covered)/total*100
    cov_c = "g" if cov_pct >= 80 else "y" if cov_pct >= 65 else "r"
    print("  " + c(str(len(covered)) + "/" + str(total) + " slots (" + ("%.0f" % cov_pct) + "%)", cov_c))
    if gaps:
        print("  GAPS (" + str(len(gaps)) + "):")
        for s,t,d in gaps:
            print("    " + s + " " + t + " " + d)
except Exception as e:
    print("  err: " + str(e))

# 4. Champions actuales con robustness
print(c(NL + "[CHAMPIONS Y ROBUSTNESS]", "y"))
try:
    snap = json.load(open("/opt/sigma/results/reports/port_snapshot.json"))
    sc = json.load(open("/opt/sigma/results/signals_cache.json"))
    rob_by_slot = {}
    for m in sc.get("models",[]):
        key = m["sym"] + "|" + m["tf"]
        rob_by_slot.setdefault(key, []).append((m.get("strategy",""), m.get("robustness_action","?")))
    pass_live = paper_only = blocked = unknown = 0
    for slot, val in sorted(snap.get("champions",{}).items()):
        strat = val.split("|")[0]
        rob = "?"
        for s, a in rob_by_slot.get(slot, []):
            if s == strat:
                rob = a
                break
        if rob == "PASS_LIVE": pass_live += 1
        elif rob == "PAPER_ONLY": paper_only += 1
        elif rob == "BLOCKED": blocked += 1
        else: unknown += 1
    print("  PASS_LIVE:  " + str(pass_live))
    print("  PAPER_ONLY: " + str(paper_only))
    print("  BLOCKED:    " + str(blocked))
    print("  unknown:    " + str(unknown))
    print("  port_cagr (HONESTO operational): " + str(snap.get("port_cagr",0)) + "%")
    print("  port_cagr_pass_live: " + str(snap.get("port_cagr_pass_live",0)) + "%")
except Exception as e:
    print("  err: " + str(e))

# 5. Ultimos champions notificados a TG
print(c(NL + "[TG NOTIFICATIONS - ultimas 4h]", "y"))
try:
    log = Path("/opt/sigma/results/reports/tg_champion_sent.log")
    if log.exists():
        lines = log.read_text().splitlines()
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(hours=4)
        recent = []
        for ln in lines:
            try:
                ts_str = ln.split()[0]
                ts = datetime.fromisoformat(ts_str.replace("Z","").split(".")[0])
                if ts >= cutoff:
                    recent.append(ln)
            except: pass
        print("  Notificados: " + str(len(recent)))
        for ln in recent[-6:]:
            print("    " + ln[:130])
except Exception as e:
    print("  err: " + str(e))

print(c(NL + "=" * 70, "b"))
