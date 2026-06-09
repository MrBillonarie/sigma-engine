#!/usr/bin/env python3
"""
SIGMA Motor 2 — Commodities Pipeline
Optimiza XAU y XAG en 1h/4h de forma continua.
Motor independiente del crypto pipeline (Motor 1).
"""
import sys, os, subprocess, time, json
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine')
os.chdir('/opt/sigma')

from pathlib import Path
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
ASSETS   = ['XAU', 'XAG', 'WTI', 'HG', 'NG', 'PL']
TFS      = ['4h', '1h', '15m', '5m']
MAX_PARALLEL = 2

SYMBOL_MAP = {
    'XAU': 'XAU/USD',
    'XAG': 'XAG/USD',
    'WTI': 'WTI/USD',
    'HG':  'HG/USD',
    'NG':  'NG/USD',
    'PL':  'PL/USD',
}
CSV_PATHS = {
    'XAU': '/opt/sigma/models/data_XAU_{tf}_max.csv',
    'XAG': '/opt/sigma/models/data_XAG_{tf}_max.csv',
    'WTI': '/opt/sigma/models/data_WTI_{tf}_max.csv',
    'HG':  '/opt/sigma/models/data_HG_{tf}_max.csv',
    'NG':  '/opt/sigma/models/data_NG_{tf}_max.csv',
    'PL':  '/opt/sigma/models/data_PL_{tf}_max.csv',
}

MODEL_DIR        = Path('/opt/sigma/models')
ASSET_PIPELINE   = '/opt/sigma/engine/optimization/asset_pipeline.py'
PYTHON           = '/opt/sigma_env/bin/python'
LOG_FILE         = Path('/opt/sigma/results/reports/commodities_pipeline.log')
OUTPUT_DIR       = Path('/opt/sigma')


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] [MOTOR2] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f)


def get_best_model(asset, tf):
    """(oos_cagr, age_days) del mejor champion del slot, o (None, 0)."""
    tf_dir = MODEL_DIR / tf
    if not tf_dir.exists():
        return None, 0
    sym = asset.lower()
    best_cagr, best_age = None, 0
    for f in tf_dir.glob(f'{sym}_*.json'):
        try:
            d = json.loads(f.read_text())
            cagr = d.get('oos_cagr') or d.get('cagr') or d.get('annual_return')
            if cagr is not None:
                ts_raw = d.get('timestamp') or d.get('created_at', '')
                try:
                    age = (datetime.now() - datetime.fromisoformat(ts_raw[:19])).days
                except Exception:
                    age = 0
                if best_cagr is None or cagr > best_cagr:
                    best_cagr, best_age = cagr, age
        except Exception:
            pass
    return best_cagr, best_age


def build_queue():
    """Todos los slots, ordenados por prioridad (peor primero)."""
    slots = []
    for asset in ASSETS:
        for tf in TFS:
            csv = CSV_PATHS[asset].format(tf=tf)
            if not Path(csv).exists():
                log(f'[SKIP] {asset} {tf}: CSV no existe ({csv})')
                continue
            cagr, age = get_best_model(asset, tf)
            # Sin modelo = maxima prioridad | Viejo = mayor prioridad
            score = (cagr if cagr is not None else -999) - age * 0.5
            slots.append((score, asset, tf))
    slots.sort()  # peor primero
    return [(a, tf) for _, a, tf in slots]


def run():
    log('=' * 60)
    log('SIGMA MOTOR 2 — Commodities Pipeline iniciando')
    log(f'  Activos: {ASSETS} | TFs: {TFS} | max_parallel={MAX_PARALLEL}')
    log('=' * 60)

    running = {}
    cycle   = 0

    while True:
        cycle += 1

        # Reap procesos terminados
        for key in list(running):
            if running[key].poll() is not None:
                rc = running[key].returncode
                log(f'[DONE] {key} | rc={rc}')
                del running[key]

        if len(running) >= MAX_PARALLEL:
            time.sleep(30)
            continue

        queue = build_queue()
        log(f'--- Ciclo {cycle} | Running={len(running)} | Queue={len(queue)} ---')

        for asset, tf in queue:
            key = f'{asset}_{tf}'
            if key in running:
                continue
            if len(running) >= MAX_PARALLEL:
                break

            csv  = CSV_PATHS[asset].format(tf=tf)
            sym  = SYMBOL_MAP[asset]
            cagr, age = get_best_model(asset, tf)
            status = 'sin modelo' if cagr is None else f'OOS {cagr:+.1f}% ({age}d)'
            log(f'[START] {asset} {tf} ({status})')

            cmd = [
                PYTHON, ASSET_PIPELINE,
                '--symbol', sym,
                '--tf',     tf,
                '--trials', '600',
                '--csv_path', csv,
            ]
            log_f = LOG_FILE.parent / f'commodities_{asset}_{tf}.log'
            with open(log_f, 'a') as lf:
                proc = subprocess.Popen(
                    cmd, stdout=lf, stderr=lf,
                    cwd=str(OUTPUT_DIR)
                )
            running[key] = proc

        time.sleep(60)


if __name__ == '__main__':
    run()
