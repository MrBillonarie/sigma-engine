#!/usr/bin/env python3
"""
SIGMA Watchdog — monitor de recursos cada 5 min.
- Avisa si RAM libre < 500MB
- Mata procesos colgados (sin CPU > 15 min)
- Detecta procesos zombie
- Notifica Telegram solo en eventos accionables (no spam)
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import psutil, time, json, urllib.request, urllib.parse, os
from datetime import datetime
from pathlib import Path

TOKEN     = _sigma_get_tg_token()
CHAT_ID   = "-1003787411069"
LOG_FILE  = Path("/opt/sigma/results/reports/watchdog.log")
STATE     = Path("/opt/sigma/results/reports/watchdog_state.json")
MIN_FREE_MB    = 250
MAX_PROC_RAM_MB = 2800   # algo bajo el cap de prlimit (2500MB)
STUCK_THRESHOLD_SEC = 900  # 15 min sin CPU

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')

def tg(text, silent=True):
    """Envia mensaje a TG (silent por defecto — eventos informativos)."""
    try:
        url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
        data = urllib.parse.urlencode({
            'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
            'disable_notification': str(silent).lower(),
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log(f'TG fail: {e}')

def load_state():
    try:
        return json.loads(STATE.read_text())
    except:
        return {'cpu_idle_since': {}, 'last_low_ram_alert': 0}

def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s))

def main():
    state = load_state()
    now = time.time()

    # 1) RAM global
    vm = psutil.virtual_memory()
    free_mb = vm.available // 1024 // 1024
    used_mb = vm.used // 1024 // 1024
    total_mb = vm.total // 1024 // 1024

    if free_mb < MIN_FREE_MB:
        last = state.get('last_low_ram_alert', 0)
        if now - last > 14400:  # cooldown 4h por aviso de RAM (menos spam)
            tg(f'⚠️ <b>RAM baja en VPS</b>\nUsado: {used_mb}MB / Total: {total_mb}MB\nLibre: <b>{free_mb}MB</b> (umbral {MIN_FREE_MB}MB)', silent=True)
            state['last_low_ram_alert'] = now
            log(f'ALERT RAM libre={free_mb}MB')
    else:
        log(f'OK RAM libre={free_mb}MB usado={used_mb}MB')

    # 2) Procesos asset_pipeline y master_pipeline
    cpu_idle = state.get('cpu_idle_since', {})
    new_cpu_idle = {}
    pipeline_procs = []
    for p in psutil.process_iter(['pid','cmdline','memory_info','cpu_percent']):
        try:
            cmd = ' '.join(p.info['cmdline'] or [])
            if 'asset_pipeline.py' not in cmd and 'master_pipeline.py' not in cmd:
                continue
            pid = p.info['pid']
            rss_mb = p.info['memory_info'].rss // 1024 // 1024
            cpu = p.cpu_percent(interval=1.0)
            pipeline_procs.append((pid, rss_mb, cpu, cmd))

            # RAM por proceso — solo kill si RAM global tambien esta critica
            # (antes: kill ciego al hit cap → mataba trials del sprint sin necesidad)
            if rss_mb > 3000 and 'master_pipeline' not in cmd and free_mb < 100:
                log(f'KILL proc {pid} RAM={rss_mb}MB > {MAX_PROC_RAM_MB}MB cap (free_mb={free_mb})')
                try:
                    p.terminate()
                    import time as _t; _t.sleep(3)
                    if p.is_running(): p.kill()
                    # NO TG por kill individual — log-only. Acumular para digest diario.
                except Exception as _ke:
                    log(f'kill fail {pid}: {_ke}')
                continue
            elif rss_mb > MAX_PROC_RAM_MB and 'master_pipeline' not in cmd:
                # RAM proc alta pero global OK — log solamente, no killear
                log(f'PROC_HIGH_RAM pid={pid} rss={rss_mb}MB free={free_mb}MB (no kill, RAM global OK)')

            # CPU idle tracking
            key = str(pid)
            if cpu < 1.0:
                if key not in cpu_idle:
                    new_cpu_idle[key] = now
                else:
                    new_cpu_idle[key] = cpu_idle[key]
                    idle_for = now - new_cpu_idle[key]
                    if idle_for > STUCK_THRESHOLD_SEC and 'master_pipeline' not in cmd:
                        log(f'STUCK proc {pid} idle {int(idle_for)}s — killing')
                        try:
                            p.terminate()
                            time.sleep(2)
                            if p.is_running():
                                p.kill()
                            tg(f'🔧 <b>Watchdog</b>: maté proceso {pid} (colgado {int(idle_for/60)}min sin CPU)', silent=True)
                        except Exception as e:
                            log(f'kill fail {pid}: {e}')
            # else: tenía CPU recién, se resetea (no se agrega a new_cpu_idle)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    state['cpu_idle_since'] = new_cpu_idle

    # 2026-05-16: SILENT_BUG MONITOR — detecta si los except con logging
    # nuevos (web_server) dispararon. Si > 0 en ultimos 5min, alerta TG.
    silent_bug_count = 0
    silent_bug_samples = []
    try:
        _r = subprocess.run(
            ['journalctl', '-u', 'sigma-web', '--since', '5 min ago', '--no-pager'],
            capture_output=True, text=True, timeout=8
        )
        for ln in (_r.stdout or '').split(chr(10)):
            if '[SILENT_BUG' in ln:
                silent_bug_count += 1
                if len(silent_bug_samples) < 3:
                    silent_bug_samples.append(ln[-200:])
    except Exception:
        pass
    if silent_bug_count > 0:
        last_sb = state.get('last_silent_bug_alert', 0)
        if now - last_sb > 1800:  # cooldown 30min
            state['last_silent_bug_alert'] = now
            _nl = chr(10)
            _parts = ['SILENT_BUG DETECTADO (web_server)']
            _parts.append('Count ultimos 5min: ' + str(silent_bug_count))
            for s in silent_bug_samples:
                _parts.append(s[:150])
            tg(_nl.join(_parts), silent=False)
            log('ALERT silent_bugs: count=' + str(silent_bug_count))

    # 2026-05-14: SERVICE HEALTH — alertar solo si un servicio critico cae.
    # NO freshness alerts (eran ruido al grupo publico). Solo SIGKILL real.
    CRITICAL_SERVICES = ['sigma-web.service', 'sigma-trainer.service',
                         'sigma-pipeline.service', 'sigma-telegram.service',
                         'sigma-paper-trader.service']
    import subprocess as _sp
    svc_alerts = []
    for svc in CRITICAL_SERVICES:
        try:
            r = _sp.run(['systemctl', 'is-active', svc], capture_output=True, text=True, timeout=5)
            status = (r.stdout or '').strip()
            if status != 'active':
                svc_alerts.append((svc, status))
        except Exception as _ee:
            svc_alerts.append((svc, 'check-fail:' + _ee.__class__.__name__))
    if svc_alerts:
        last_svc = state.get('last_service_alert', 0)
        if now - last_svc > 900:
            _nl = chr(10)
            _parts = ['SERVICIO CAIDO']
            for svc, status in svc_alerts:
                _parts.append('  ' + svc + ': ' + status)
            tg(_nl.join(_parts), silent=False)
            state['last_service_alert'] = now
            log('ALERT services down: ' + str([s[0] for s in svc_alerts]))

    save_state(state)

    log(f'STATUS pipelines={len(pipeline_procs)} ram_free={free_mb}MB svc_down={len(svc_alerts)} silent_bugs={silent_bug_count}')

if __name__ == '__main__':
    main()
