#!/usr/bin/env python3
"""
adaptive_push_launcher.py — Auto-pusher de grade A para slots grade B.

Cada vez que corre (cron */60 min):
1. Lee load average del VPS
2. Verifica si ya hay un push activo (max 1 a la vez)
3. Identifica el slot grade B con score más alto (closest to A) que NO se ha pusheado en las ultimas 12h
4. Si load < UMBRAL y no hay push activo y hay candidato → lanza push en background
5. Registra el push en state file para no repetir

State: /opt/sigma/state/adaptive_push.json
Log:   /opt/sigma/results/reports/adaptive_push_launcher.log
"""
import os, sys, json, time, subprocess, urllib.request
from pathlib import Path
from datetime import datetime

if '/opt/sigma' not in sys.path:
    sys.path.insert(0, '/opt/sigma')
try:
    from utils.parallel_guard import global_slots_available
except Exception as _e_guard:
    print(f'[WARN] No se pudo importar utils.parallel_guard: {_e_guard}. Sin tope global.')
    def global_slots_available(cap=7):
        return cap

# --- SIGMA VITRINA push hook ---
def _vitrina_log_push(kind, sym, tf, **kw):
    try:
        from utils.decisions import log_decision
        log_decision(kind=kind, slot=f'{sym}|{tf}',
                     payload={'sym': sym, 'tf': tf, **kw},
                     meta={'source': 'adaptive_push_launcher'})
    except Exception:
        pass


LOAD_MAX = 11.0  # 2026-06-16: master_pipeline base load es 10-12, ajustar para que push pueda entrar
TRIALS          = 300  # 2026-06-19: bajado de 600 -- mismo presupuesto total/dia pero recambio 2x mas rapido, libera CPU antes para gap_auto_launcher
COOLDOWN_HOURS  = 12       # no re-pushear el mismo slot por X horas
STATE_FILE      = Path('/opt/sigma/state/adaptive_push.json')
LOG_FILE        = Path('/opt/sigma/results/reports/adaptive_push_launcher.log')
PUSH_SCRIPT     = Path('/opt/sigma/scripts/push_grade_a.py')


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')
    print(f'[{ts}] {msg}', flush=True)


def get_load_avg():
    """Returns the 5-min load average."""
    with open('/proc/loadavg') as f:
        parts = f.read().split()
    return float(parts[1])  # 5-min average


MAX_PUSH_HOURS = 4

def is_push_running():
    try:
        out = subprocess.check_output(['pgrep', '-f', 'push_grade_a'], text=True).strip()
        if not out:
            return False
        pids = [int(p) for p in out.split() if p.strip()]
        import time as _t, signal
        for pid in pids:
            try:
                with open(f'/proc/{pid}/stat') as f:
                    starttime_ticks = int(f.read().split()[21])
                hz = os.sysconf('SC_CLK_TCK')
                with open('/proc/uptime') as f:
                    uptime_s = float(f.read().split()[0])
                age_h = (uptime_s - starttime_ticks / hz) / 3600
                if age_h > MAX_PUSH_HOURS:
                    log(f'[TIMEOUT] push PID {pid} lleva {age_h:.1f}h - matando')
                    os.kill(pid, signal.SIGTERM)
                    _t.sleep(2)
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return False
            except (FileNotFoundError, ProcessLookupError, ValueError):
                pass
        return True
    except subprocess.CalledProcessError:
        return False

def load_state():
    if not STATE_FILE.exists():
        return {'recent_pushes': {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {'recent_pushes': {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_signals():
    try:
        r = urllib.request.urlopen('http://localhost:8080/api/signals', timeout=15)
        return json.loads(r.read())
    except Exception as e:
        log(f'fetch signals err: {e}')
        return {}


def find_best_b_candidate(state):
    """Find slot with B-grade champion closest to A (highest score < 0.55)."""
    sig = fetch_signals()
    ms = sig.get('models', [])
    # Pick winning model per slot
    by_slot = {}
    for m in ms:
        key = (m.get('sym'), m.get('tf'))
        score = m.get('score', 0) or 0
        prev = by_slot.get(key)
        if prev is None or score > prev.get('score', 0):
            by_slot[key] = m

    # Filter: grade B AND score in healthy range
    candidates = [m for m in by_slot.values()
                  if m.get('grade') == 'B'
                  and 0.40 <= (m.get('score', 0) or 0) < 0.55]

    # Apply cooldown: skip recently pushed slots
    now_ts = time.time()
    recent = state.get('recent_pushes', {})
    fresh_candidates = []
    for c in candidates:
        slot_key = f'{c.get("sym")}_{c.get("tf")}'
        last_push = recent.get(slot_key, 0)
        if (now_ts - last_push) >= COOLDOWN_HOURS * 3600:
            fresh_candidates.append(c)

    if not fresh_candidates:
        return None

    # Pick the one with highest score (closest to A)
    fresh_candidates.sort(key=lambda x: -(x.get('score', 0) or 0))
    return fresh_candidates[0]


def main():
    load = get_load_avg()
    log(f'load_avg(5m)={load:.2f} (limit {LOAD_MAX})')

    if load > LOAD_MAX:
        log(f'SKIP: load too high')
        return

    if global_slots_available() <= 0:
        log('SKIP: tope global de paralelismo alcanzado (master_pipeline/gap_auto_launcher/trainer ocupan el resto)')
        return

    if is_push_running():
        log('SKIP: push already running')
        return

    state = load_state()
    candidate = find_best_b_candidate(state)
    if candidate is None:
        log('SKIP: no grade-B candidate available (or all in cooldown)')
        return

    sym = candidate.get('sym')
    tf = candidate.get('tf')
    strat = candidate.get('strategy')
    score = candidate.get('score', 0)
    cagr = candidate.get('cagr', 0)

    log(f'LAUNCH: {sym} {tf} (current champion {strat}, score {score:.4f}, CAGR {cagr:.1f}%) trials={TRIALS}')

    # Launch in background
    push_log = f'/opt/sigma/results/reports/adaptive_push_{sym}_{tf}_{time.strftime("%Y%m%d_%H%M%S")}.log'
    cmd = (f'nohup /opt/sigma_env/bin/python {PUSH_SCRIPT} {sym} {tf} {TRIALS} '
           f'> {push_log} 2>&1 &')
    _vitrina_log_push('push_started', sym, tf, strategy=strat, score=score, cagr=cagr, trials=TRIALS)  # --- VITRINA push_started call ---
    subprocess.Popen(cmd, shell=True)
    log(f'LAUNCHED → log: {push_log}')

    # Record in state
    slot_key = f'{sym}_{tf}'
    state.setdefault('recent_pushes', {})[slot_key] = time.time()
    state['last_launch'] = {
        'sym': sym, 'tf': tf, 'strategy': strat,
        'score': score, 'cagr': cagr,
        'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_state(state)


if __name__ == '__main__':
    main()
