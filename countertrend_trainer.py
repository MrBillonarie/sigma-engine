#!/usr/bin/env python3
"""Fase 2 del plan champions_regime (2026-06-21): dispara countertrend_objective.py
para los candidatos confirmados por Fase 1, respetando el cap global de
paralelismo (utils/parallel_guard.py) -- mismo patron que
engine/live/continuous_trainer.py: prlimit de RAM por proceso, chequeo de
slots libres antes de cada lanzamiento, sin saltarse el cap compartido con
master_pipeline/gap_auto_launcher/adaptive_push_launcher.
"""
import subprocess
import time
import sys
from pathlib import Path

sys.path.insert(0, "/opt/sigma")
from utils.parallel_guard import global_slots_available

# (symbol, tf, strategy, regime, n_trials) -- ronda 2 (2026-06-21, post-fix
# de regimen EMA200 semanal): candidatos nuevos destrabados en 15m/5m,
# multi-ciclo + rentables en el diagnostico Fase 1 re-corrido. Trial count
# mas bajo que la ronda 1 porque 15m/5m tienen 4-12x mas velas por backtest.
JOBS = [
    ("BNB/USDT", "15m", "lower_high_structure_short", "bull", 150),
    ("ETH/USDT", "15m", "breakout",                   "bear", 150),
    ("ETH/USDT", "15m", "lower_high_structure_short", "bull", 150),
    ("LTC/USDT", "15m", "psar_flip",                  "bear", 150),
    ("SOL/USDT", "15m", "lower_high_structure_short", "bull", 150),
    ("ETH/USDT", "5m",  "volatility_breakout",         "bear", 120),
    ("SOL/USDT", "5m",  "supertrend_short",            "bull", 120),
]

LOG_DIR = Path("/opt/sigma/results/reports")
PY = "/opt/sigma_env/bin/python"
RAM_LIMIT_BYTES = 2684354560  # 2.5GB, igual que continuous_trainer.py


def launch_job(symbol, tf, strategy, regime, trials):
    asset = symbol.split("/")[0].lower()
    log_path = LOG_DIR / f"countertrend_{asset}_{tf}_{strategy}_{regime}.log"
    cmd = [
        "prlimit", f"--as={RAM_LIMIT_BYTES}", "--",
        PY, "-u", "/opt/sigma/countertrend_objective.py",
        "--symbol", symbol, "--tf", tf, "--strategy", strategy,
        "--regime", regime, "--trials", str(trials),
    ]
    f = open(log_path, "a")
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
    return proc, f


def run():
    pending = list(JOBS)
    running = []  # list of (proc, file_handle, job_desc)
    print(f"[countertrend_trainer] {len(pending)} jobs pendientes, cap global = "
          f"{global_slots_available()} slots libres ahora", flush=True)
    while pending or running:
        still_running = []
        for proc, fh, desc in running:
            if proc.poll() is None:
                still_running.append((proc, fh, desc))
            else:
                fh.close()
                print(f"[countertrend_trainer] terminado: {desc} (exit={proc.returncode})", flush=True)
        running = still_running

        while pending and global_slots_available() > 0:
            job = pending.pop(0)
            proc, fh = launch_job(*job)
            desc = f"{job[0]} {job[1]} {job[2]} ct-{job[3]}"
            running.append((proc, fh, desc))
            print(f"[countertrend_trainer] lanzado: {desc} (pid={proc.pid})", flush=True)
            time.sleep(3)  # evita rafaga de spawns simultaneos

        if pending or running:
            time.sleep(30)

    print("[countertrend_trainer] todos los jobs countertrend terminaron", flush=True)


if __name__ == "__main__":
    run()
