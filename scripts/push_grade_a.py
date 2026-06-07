#!/usr/bin/env python3
"""
push_grade_a.py — Push focalizado de Optuna sobre slots con grade B cercanos a A.
Corre run_pipeline con n_trials elevado para incrementar prob de encontrar mejor config.

Uso:
    python push_grade_a.py LTC 4h        # 1 slot
    python push_grade_a.py LTC 4h 600    # 1 slot con custom trials (default 600)
"""
import sys
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine/optimization')

from datetime import datetime


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def main():
    if len(sys.argv) < 3:
        print("Uso: push_grade_a.py <SYM> <TF> [trials]")
        sys.exit(1)
    sym = sys.argv[1].upper()
    tf  = sys.argv[2].lower()
    trials = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    symbol = f'{sym}/USDT' if '/' not in sym else sym

    log(f'PUSH GRADE A — {symbol} {tf}  trials={trials}')
    log('Esto re-evalua TODAS las estrategias para ese slot con trials elevado.')
    log('Si Optuna encuentra una config mejor, el champion se actualiza automaticamente.')

    from asset_pipeline import run_pipeline
    try:
        run_pipeline(symbol, tf, n_trials=trials, loop=False, max_cycles=1)
        log(f'OK — {symbol} {tf} push completo')
    except Exception as e:
        log(f'ERROR: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()
