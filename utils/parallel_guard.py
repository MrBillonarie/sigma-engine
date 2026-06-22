"""Guard de paralelismo compartido entre master_pipeline, gap_auto_launcher,
adaptive_push_launcher, continuous_trainer y el pipeline de commodities (Motor 2)
-- ninguno coordinaba con los otros: cada uno solo contaba sus propios procesos
hijos, asi que la suma de todos podia superar los 8 nucleos sin que ninguno lo
notara (incidente 2026-06-17, recurrencia parcial 2026-06-19/20 -- ver auditoria
2026-06-20 que encontro continuous_trainer y adaptive_push_launcher sin cablear
a este guard). Esta funcion cuenta TODOS los procesos de optimizacion pesada del
sistema, sin importar quien los lanzo. push_grade_a.py corre run_pipeline()
in-process (no como subprocess de asset_pipeline.py), asi que su propio cmdline
debe matchear tambien o queda invisible para el resto de los launchers.
"""
import subprocess

GLOBAL_TRAINING_CAP = 7  # 8 nucleos, 1 reservado para sigma-web (live) + OS + watchdog

_PATTERNS = ('asset_pipeline.py', 'push_grade_a.py', 'countertrend_objective.py')


def count_training_processes():
    try:
        r = subprocess.run(['ps', '-eo', 'cmd'], capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    return sum(
        1 for line in r.stdout.splitlines()
        if any(p in line for p in _PATTERNS) and 'grep' not in line
    )


def global_slots_available(cap=GLOBAL_TRAINING_CAP):
    return max(0, cap - count_training_processes())
