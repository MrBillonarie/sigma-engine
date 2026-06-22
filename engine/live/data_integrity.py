#!/usr/bin/env python3
"""
SIGMA Data Integrity — valida que los datos que alimentan senales no esten
stale o rotos. No reemplaza los fetchers existentes, valida sus outputs.

Corre cada 30 min. Alerta Telegram si algo esta stale -- informativo,
no bloquea trading (mismo principio que los red-flags del dashboard).
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import json, time
from datetime import datetime
from pathlib import Path

BASE      = Path('/opt/sigma')
MODELS    = BASE / 'models'
LOG_PATH  = BASE / 'results' / 'reports' / 'data_integrity.log'
STATE_PATH = BASE / 'results' / 'reports' / 'data_integrity_state.json'
CHAT_ID   = '-1003787411069'
ALERT_COOLDOWN_SEC = 4 * 3600  # 4h -- evita spam si algo sigue stale entre corridas

# Tolerancia de frescura por TF (en horas) -- algo mas laxo que el periodo nominal
# porque el cron de update_data corre 1x/dia, no en tiempo real.
OHLCV_FILES = {
    'data_5m_max.csv':  30,
    'data_15m_max.csv': 30,
    'data_1h_max.csv':  30,
    'data_4h_max.csv':  30,
    'data_1d_max.csv':  30,
}

# Activos M2 (commodities) -- mismo patron, archivos con prefijo de simbolo
M2_SYMBOLS = ['XAU', 'XAG', 'HG', 'NG', 'WTI', 'PL']
M2_TFS = ['5m', '15m', '1h', '4h', '1d']
M2_TOLERANCE_HOURS = 30

DERIVATIVES_FILES = {
    'results/lsr.db': 2,  # LSR fetcher corre cada 5 min (DB_PATH real en lsr_fetcher.py)
}


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')


def tg(text, silent=True):
    try:
        import urllib.request, urllib.parse
        token = _sigma_get_tg_token()
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = urllib.parse.urlencode({
            'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
            'disable_notification': str(silent).lower(),
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log(f'TG fail: {e}')


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'last_alert': {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600.0


def check_stale_files(stale_list, ok_list):
    for fname, tolerance_h in OHLCV_FILES.items():
        p = MODELS / fname
        if not p.exists():
            stale_list.append((fname, 'NO_EXISTE', None))
            continue
        age = age_hours(p)
        if age > tolerance_h:
            stale_list.append((fname, f'{age:.1f}h sin actualizar (tolerancia {tolerance_h}h)', age))
        else:
            ok_list.append(fname)

    for sym in M2_SYMBOLS:
        for tf in M2_TFS:
            fname = f'data_{sym}_{tf}_max.csv'
            p = MODELS / fname
            if not p.exists():
                continue  # no todos los simbolos M2 tienen todos los TFs necesariamente
            age = age_hours(p)
            if age > M2_TOLERANCE_HOURS:
                stale_list.append((fname, f'{age:.1f}h sin actualizar (tolerancia {M2_TOLERANCE_HOURS}h)', age))
            else:
                ok_list.append(fname)

    for relpath, tolerance_h in DERIVATIVES_FILES.items():
        p = BASE / relpath
        if not p.exists():
            stale_list.append((relpath, 'NO_EXISTE', None))
            continue
        age = age_hours(p)
        if age > tolerance_h:
            stale_list.append((relpath, f'{age:.1f}h sin actualizar (tolerancia {tolerance_h}h)', age))
        else:
            ok_list.append(relpath)


def main():
    stale, ok = [], []
    check_stale_files(stale, ok)

    state = load_state()
    last_alert = state.get('last_alert', {})
    now = time.time()

    if stale:
        to_report = []
        for fname, reason, _ in stale:
            last = last_alert.get(fname, 0)
            if now - last > ALERT_COOLDOWN_SEC:
                to_report.append((fname, reason))
                last_alert[fname] = now
        if to_report:
            parts = ['⚠️ <b>Data Integrity — datos stale detectados</b>', '']
            for fname, reason in to_report:
                parts.append(f'  • <code>{fname}</code>: {reason}')
            parts.append('\nNo bloquea trading -- es informativo. Si persiste, revisar el fetcher correspondiente.')
            tg('\n'.join(parts), silent=False)
        state['last_alert'] = last_alert
        save_state(state)
        log(f'STALE: {[(f, r) for f, r, _ in stale]}')
    else:
        log(f'OK: {len(ok)} archivos frescos')


if __name__ == '__main__':
    main()
