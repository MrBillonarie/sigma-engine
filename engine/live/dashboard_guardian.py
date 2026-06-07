#!/usr/bin/env python3
"""
SIGMA Dashboard Guardian — protege dashboard.py de sobreescrituras.

Verifica cada 2 min que dashboard.py tenga los markers hedge fund.
Si alguien lo sobrescribe con una version vieja, lo restaura desde el snapshot.
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import os, sys, shutil, time
from datetime import datetime
from pathlib import Path

DASHBOARD = Path('/opt/sigma/engine/live/dashboard.py')
SNAPSHOT  = Path('/opt/sigma/engine/live/dashboard.py.hedge_canonical')
LOG       = Path('/opt/sigma/results/reports/dashboard_guardian.log')

# Markers que DEBE tener la version hedge fund
MARKERS = ['kpi-strip', 'risk-panel', 'footer-pro', 'VALOR FLOTANTE', 'Portafolio operable',
           'CAPITAL REALIZADO', '_drawEquityFrame', 'Proof of Work']

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')

def tg_alert(text):
    """Notificar via Telegram (silencioso)."""
    try:
        import urllib.request, urllib.parse
        TOKEN   = _sigma_get_tg_token()
        CHAT_ID = '-1003787411069'
        url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
        data = urllib.parse.urlencode({
            'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
            'disable_notification': 'true',
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception:
        pass

def main():
    if not DASHBOARD.exists():
        log('ERROR: dashboard.py no existe')
        return

    if not SNAPSHOT.exists():
        # Crear snapshot inicial si dashboard tiene markers
        content = DASHBOARD.read_text(encoding='utf-8')
        missing = [m for m in MARKERS if m not in content]
        if missing:
            log(f'NO se puede crear snapshot — dashboard actual missing: {missing}')
            return
        shutil.copy2(DASHBOARD, SNAPSHOT)
        log(f'Snapshot canonico creado ({DASHBOARD.stat().st_size} bytes)')
        return

    # Verificar dashboard actual
    content = DASHBOARD.read_text(encoding='utf-8')
    missing = [m for m in MARKERS if m not in content]

    if not missing:
        log(f'OK dashboard.py tiene todos los markers ({DASHBOARD.stat().st_size} bytes)')
        return

    # RESTAURAR
    log(f'ALERT dashboard.py missing markers: {missing} — RESTAURANDO')

    # Backup del archivo malo para forensia
    bad_backup = DASHBOARD.parent / f'dashboard.py.OVERWRITTEN_{int(time.time())}'
    shutil.copy2(DASHBOARD, bad_backup)
    log(f'Backup del archivo malo: {bad_backup.name}')

    # Restaurar desde snapshot
    shutil.copy2(SNAPSHOT, DASHBOARD)
    log(f'Restaurado desde snapshot ({DASHBOARD.stat().st_size} bytes)')

    # Regenerar dashboard.html
    import subprocess
    try:
        subprocess.run(['/opt/sigma_env/bin/python', str(DASHBOARD)],
                       cwd=str(DASHBOARD.parent.parent.parent), timeout=30,
                       capture_output=True)
        log('dashboard.html regenerado')
    except Exception as e:
        log(f'Error regenerando HTML: {e}')

    tg_alert(
        f'🛡 <b>Dashboard Guardian</b>\n\n'
        f'dashboard.py fue sobrescrito por algo y lo restauré.\n'
        f'Markers faltantes: <code>{", ".join(missing)}</code>\n\n'
        f'Backup del archivo malo: <code>{bad_backup.name}</code>'
    )

if __name__ == '__main__':
    main()
