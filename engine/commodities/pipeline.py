#!/usr/bin/env python3
"""
SIGMA Motor 2 — Commodities Pipeline
Optimiza XAU y XAG en 1h/4h de forma continua.
Motor independiente del crypto pipeline (Motor 1).
"""
import sys, os, subprocess, time, json, random
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine')
os.chdir('/opt/sigma')

from pathlib import Path
from datetime import datetime, timezone

try:
    from utils.parallel_guard import global_slots_available
except Exception:
    def global_slots_available(cap=7):
        return cap

# ── Config ────────────────────────────────────────────────────────────────────
def _has_15m_data(asset, min_rows=50000):  # 50k: solo XAU/XAG tienen 15m real (491k/479k rows)
    """True si tenemos suficientes datos 15m para este activo."""
    path = Path(f'/opt/sigma/models/data_{asset}_15m_max.csv')
    if not path.exists():
        return False
    try:
        # Count rows fast (no parse)
        with open(path) as f:
            rows = sum(1 for _ in f) - 1  # minus header
        return rows >= min_rows
    except Exception:
        return False


ASSET_TFS = {
    'XAU': ['1d', '4h', '1h', '15m'],
    'XAG': ['1d', '4h', '1h', '15m'],
    'WTI': ['1d', '4h', '1h'] + (['15m'] if _has_15m_data('WTI') else []),
    'NG':  ['1d', '4h', '1h'] + (['15m'] if _has_15m_data('NG')  else []),
    'PL':  ['1d', '4h', '1h'] + (['15m'] if _has_15m_data('PL')  else []),
    'HG':  ['1d', '4h', '1h'] + (['15m'] if _has_15m_data('HG')  else []),
}
ASSETS = list(ASSET_TFS.keys())
TFS    = ['4h', '1h', '1d']
MAX_PARALLEL = int(os.getenv("M2_PARALLEL", "2"))  # 2 workers M2: balance M1/M2 (load control)

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
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f, flush=True)


def get_best_model(asset, tf):
    """(oos_cagr, age_days) del mejor champion del slot, o (None, 0)."""
    tf_dir = MODEL_DIR / tf
    if not tf_dir.exists():
        return None, 0
    sym = asset.lower()
    best_cagr, best_age = None, 0
    for f in list(tf_dir.glob(f'{sym}_*.json')) + list(tf_dir.glob(f'{sym}usd_*.json')):
        try:
            d = json.loads(f.read_text())
            m_oos = d.get('metrics_oos') or {}
            cagr = m_oos.get('cagr') or d.get('oos_cagr') or d.get('cagr') or d.get('annual_return')
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
    for asset, _tfs in ASSET_TFS.items():
        for tf in _tfs:
            csv = CSV_PATHS[asset].format(tf=tf)
            if not Path(csv).exists():
                log(f'[SKIP] {asset} {tf}: CSV no existe ({csv})')
                continue
            cagr, age = get_best_model(asset, tf)
            # Sin modelo = maxima prioridad | Viejo = mayor prioridad
            score = (cagr if cagr is not None else -999) - age * 0.5
            slots.append((score, random.random(), asset, tf))  # random tiebreaker: rotacion equitativa entre sin-modelo
    slots.sort()  # peor primero
    return [(a, tf) for _, _, a, tf in slots]


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

        # Tope global compartido con master_pipeline y gap_auto_launcher
        # (ninguno veia los procesos del otro -- ver utils/parallel_guard.py)
        if global_slots_available() <= 0:
            log('Tope global de paralelismo alcanzado (M1/gap_auto ocupan el resto) - pausa 30s')
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
            if global_slots_available() <= 0:
                break

            csv  = CSV_PATHS[asset].format(tf=tf)
            sym  = SYMBOL_MAP[asset]
            cagr, age = get_best_model(asset, tf)
            status = 'sin modelo' if cagr is None else f'OOS {cagr:+.1f}% ({age}d)'
            log(f'[START] {asset} {tf} ({status})')

            cmd = [
                'nice', '-n', '5',   # prioridad menor que Motor1 (nice 0)
                PYTHON, ASSET_PIPELINE,
                '--symbol', sym,
                '--tf',     tf,
                '--trials', '200',  # M2: 50 trials anti-OOM (menos datos disponibles)
                '--csv_path', csv,
            ]
            log_f = LOG_FILE.parent / f'commodities_{asset}_{tf}.log'
            with open(log_f, 'a') as lf:
                # prlimit: cap 3.5GB virtual para evitar OOM

                if isinstance(cmd, list): cmd = ["prlimit", "--as=3758096384", "--"] + cmd

                proc = subprocess.Popen(
                    cmd, stdout=lf, stderr=lf,
                    cwd=str(OUTPUT_DIR)
                )
            running[key] = proc

        time.sleep(60)


if __name__ == '__main__':
    # PID lock — evita instancias duplicadas
    PID_FILE = Path('/tmp/motor2_pipeline.pid')
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            import os as _os
            _os.kill(old_pid, 0)  # si no lanza, el proceso sigue vivo
            print(f'[MOTOR2] Ya corre instancia PID {old_pid}. Saliendo.', flush=True)
            import sys as _sys; _sys.exit(0)
        except (ProcessLookupError, ValueError):
            pass  # proceso muerto, continuar
    import os as _os
    PID_FILE.write_text(str(_os.getpid()))
    try:
        run()
    finally:
        PID_FILE.unlink(missing_ok=True)
