#!/usr/bin/env python3
"""
sigma-paper-trader daemon — periódicamente dispara /api/stats para que web_server
evalúe candidatos y dispatche paper trades. Más frecuente que el cron de 2 min.
"""
import time, sys, urllib.request, urllib.error
from datetime import datetime

INTERVAL = 60  # segundos
URL = "http://localhost:8080/api/stats"
LOG = "/opt/sigma/results/reports/paper_trader_daemon.log"

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            print(line, file=f)
    except Exception:
        pass

log("=== paper_trader_daemon START ===")
consec_errs = 0
_tick = 0
while True:
    try:
        r = urllib.request.urlopen(URL, timeout=15)
        r.read()  # consume
        consec_errs = 0
    except Exception as e:
        consec_errs += 1
        if consec_errs % 5 == 1:  # logear solo cada 5 errores consecutivos
            log(f"WARN /api/stats error #{consec_errs}: {type(e).__name__}: {e}")
    _tick += 1
    if _tick % 60 == 0:  # heartbeat cada 60 iteraciones (audit 2026-05-13)
        log(f"heartbeat tick={_tick} errs={consec_errs}")
    time.sleep(INTERVAL)
