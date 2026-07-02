#!/usr/bin/env python3
"""
SIGMA Motor 3 — Stocks Pipeline (S&P 500)
Optimiza AAPL/NVDA/TSLA/JPM/XOM en 15m/1h/4h/1d.
Motor independiente de M1 (crypto) y M2 (commodities).
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
    def global_slots_available(cap=4):
        return cap

# ── Config ────────────────────────────────────────────────────────────────────
ASSETS = ['AAPL', 'NVDA', 'TSLA', 'JPM', 'XOM']

# 15m solo tiene 60 días de historia → menos slots pero gate más exigente
# Se activa solo cuando hay datos suficientes (≥2000 filas = ~50 días de 15m)
def _has_data(asset, tf, min_rows=1400):
    p = Path(f'/opt/sigma/models/data_{asset}_{tf}_max.csv')
    if not p.exists():
        return False
    try:
        with open(p) as f:
            return sum(1 for _ in f) - 1 >= min_rows
    except Exception:
        return False

ASSET_TFS = {
    asset: (
        ['1d', '4h', '1h'] +
        (['15m'] if _has_data(asset, '15m', min_rows=1400) else [])
    )
    for asset in ASSETS
}

MAX_PARALLEL = int(os.getenv("M3_PARALLEL", "3"))

# Trials por TF — 15m conservador (poco data), 1d/4h/1h máxima calidad
TRIALS_BY_TF = {
    '15m': 150,   # conservador: solo 60 días disponibles
    '1h':  400,
    '4h':  400,
    '1d':  500,
}

CSV_PATHS = {
    asset: f'/opt/sigma/models/data_{asset}_{{tf}}_max.csv'
    for asset in ASSETS
}

SYMBOL_MAP = {asset: f'{asset}/USD' for asset in ASSETS}

MODEL_DIR      = Path('/opt/sigma/models')
ASSET_PIPELINE = '/opt/sigma/engine/optimization/asset_pipeline.py'
PYTHON         = '/opt/sigma_env/bin/python'
LOG_FILE       = Path('/opt/sigma/results/reports/stocks_pipeline.log')


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] [MOTOR3] {msg}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        print(line, file=f, flush=True)


def get_best_model(asset, tf):
    """Retorna el mejor modelo guardado para este slot, si existe."""
    pattern = f'{asset}_*_{tf}_*.json'
    candidates = list(MODEL_DIR.glob(pattern))
    if not candidates:
        return None
    def score(p):
        try:
            d = json.loads(p.read_text())
            return d.get('canonical_score', d.get('robustness_final', 0))
        except Exception:
            return 0
    return max(candidates, key=score)


def build_cmd(asset, tf):
    symbol   = SYMBOL_MAP[asset]
    csv_path = CSV_PATHS[asset].format(tf=tf)
    trials   = TRIALS_BY_TF.get(tf, 200)
    return [
        PYTHON, '-u', ASSET_PIPELINE,
        '--symbol',   symbol,
        '--tf',       tf,
        '--trials',   str(trials),
        '--csv_path', csv_path,
        '--loop',
    ]


def run_cycle(running_procs):
    """Lanza nuevos procesos hasta llenar los slots disponibles."""
    slots = min(global_slots_available(), MAX_PARALLEL - len(running_procs))
    if slots <= 0:
        return

    # Construir cola: slots vacíos primero, luego mejora continua
    queue = []
    for asset in ASSETS:
        for tf in ASSET_TFS.get(asset, []):
            key = f'{asset}_{tf}'
            if key not in running_procs:
                has_model = get_best_model(asset, tf) is not None
                queue.append((key, asset, tf, has_model))

    # Priorizar slots sin modelo
    queue.sort(key=lambda x: (x[3], random.random()))

    launched = 0
    for key, asset, tf, _ in queue:
        if launched >= slots:
            break
        csv = CSV_PATHS[asset].format(tf=tf)
        if not Path(csv).exists():
            log(f'  SKIP {asset}/{tf}: sin datos ({csv})')
            continue
        cmd = build_cmd(asset, tf)
        log(f'  LAUNCH {asset}/{tf} ({TRIALS_BY_TF.get(tf,200)} trials)')
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        running_procs[key] = proc
        launched += 1


def main():
    log('=== SIGMA Motor 3 — Stocks Pipeline arrancando ===')
    log(f'  Activos: {ASSETS}')
    log(f'  TFs por activo: {ASSET_TFS}')
    log(f'  MAX_PARALLEL={MAX_PARALLEL} | TRIALS={TRIALS_BY_TF}')

    running_procs = {}

    while True:
        # Limpiar procesos terminados
        done = [k for k, p in running_procs.items() if p.poll() is not None]
        for k in done:
            log(f'  DONE {k}')
            del running_procs[k]

        # Lanzar nuevos
        run_cycle(running_procs)

        log(f'  Activos: {len(running_procs)} procesos | '
            f'global_slots={global_slots_available()}')
        time.sleep(60)


if __name__ == '__main__':
    main()
