#!/usr/bin/env python3
"""
SIGMA Architecture Map — mapa vivo de que esta corriendo y por que.

Corre diario. Compara el estado real del VPS (servicios systemd, timers, crontab)
contra un manifiesto mantenido a mano. Si aparece algo nuevo que no esta en el
manifiesto, alerta sin cooldown -- es exactamente la clase de hallazgo que el
17/06 costo 15 horas sin detectarse (dos motores de trading corriendo en paralelo).

El manifiesto NO se actualiza solo despues de la primera corrida (baseline).
Cualquier cambio intencional despues de eso se agrega a mano.
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import json, re, subprocess, time
from datetime import datetime
from pathlib import Path

BASE          = Path('/opt/sigma')
MANIFEST_PATH = BASE / 'results' / 'reports' / 'architecture_manifest.json'
SNAPSHOT_PATH = BASE / 'results' / 'reports' / 'architecture_snapshot.md'
STATE_PATH    = BASE / 'results' / 'reports' / 'architecture_map_state.json'
LOG_PATH      = BASE / 'results' / 'reports' / 'architecture_map.log'

CHAT_ID = '-1003787411069'
REMOVED_COOLDOWN_SEC = 6 * 3600  # un servicio/cron conocido que desaparece puede ser mantenimiento


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


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout or ''
    except Exception as e:
        log(f'cmd fail {cmd}: {e}')
        return ''


def get_sigma_units(unit_type):
    """unit_type: 'service' o 'timer'. Retorna dict {nombre: descripcion}."""
    out = _run(['systemctl', 'list-units', f'--type={unit_type}', '--all', '--no-legend'])
    units = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith('sigma-'):
            continue
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        name = parts[0]
        desc = parts[4]
        units[name] = desc
    return units


def get_cron_lines():
    out = _run(['crontab', '-l'])
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        lines.append(line)
    return lines


def load_manifest():
    if not MANIFEST_PATH.exists():
        return None
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None


def save_manifest(manifest):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'last_removed_alert': {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def write_snapshot(services, timers, cron_lines, manifest):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        '# SIGMA — Snapshot de Arquitectura',
        f'> Generado automaticamente: {now}',
        '> Este archivo se regenera cada dia. No editar a mano.',
        '',
        '## Servicios systemd (sigma-*)',
        '',
    ]
    for name in sorted(services):
        lines.append(f'- `{name}` — {services[name]}')
    lines += ['', '## Timers systemd (sigma-*)', '']
    for name in sorted(timers):
        lines.append(f'- `{name}` — {timers[name]}')
    lines += ['', f'## Crontab ({len(cron_lines)} líneas activas)', '']
    for c in cron_lines:
        lines.append(f'- `{c[:160]}`')
    lines += ['', f'## Manifiesto (baseline conocido)', '']
    if manifest:
        lines.append(f"- Servicios conocidos: {len(manifest.get('services', []))}")
        lines.append(f"- Timers conocidos: {len(manifest.get('timers', []))}")
        lines.append(f"- Cron lines conocidas: {len(manifest.get('cron_lines', []))}")
        lines.append(f"- Baseline creado: {manifest.get('created_at', '?')}")
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text('\n'.join(lines), encoding='utf-8')


def main():
    services = get_sigma_units('service')
    timers = get_sigma_units('timer')
    cron_lines = get_cron_lines()

    manifest = load_manifest()
    state = load_state()

    if manifest is None:
        manifest = {
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'services': sorted(services.keys()),
            'timers': sorted(timers.keys()),
            'cron_lines': cron_lines,
        }
        save_manifest(manifest)
        write_snapshot(services, timers, cron_lines, manifest)
        log(f'BASELINE creado: {len(services)} servicios, {len(timers)} timers, {len(cron_lines)} cron lines')
        tg(
            f'🗺 <b>Architecture Map — baseline creado</b>\n\n'
            f'Servicios: <b>{len(services)}</b>  Timers: <b>{len(timers)}</b>  Cron: <b>{len(cron_lines)}</b>\n\n'
            f'Desde ahora, cualquier servicio/cron nuevo que no esté en el manifiesto dispara alerta inmediata.',
            silent=True,
        )
        return

    known_services = set(manifest.get('services', []))
    known_timers = set(manifest.get('timers', []))
    known_cron = set(manifest.get('cron_lines', []))

    new_services = set(services.keys()) - known_services
    new_timers = set(timers.keys()) - known_timers
    new_cron = set(cron_lines) - known_cron

    removed_services = known_services - set(services.keys())
    removed_timers = known_timers - set(timers.keys())
    removed_cron = known_cron - set(cron_lines)

    if new_services or new_timers or new_cron:
        parts = ['🚨 <b>ARCHITECTURE MAP — algo nuevo corriendo que no estaba documentado</b>', '']
        if new_services:
            parts.append('<b>Servicios nuevos:</b>')
            parts += [f'  • <code>{s}</code> — {services[s]}' for s in sorted(new_services)]
        if new_timers:
            parts.append('<b>Timers nuevos:</b>')
            parts += [f'  • <code>{t}</code>' for t in sorted(new_timers)]
        if new_cron:
            parts.append('<b>Líneas de cron nuevas:</b>')
            parts += [f'  • <code>{c[:120]}</code>' for c in sorted(new_cron)]
        parts.append('\nSi esto fue intencional, agregalo al manifiesto a mano. Si no, investigar ahora.')
        tg('\n'.join(parts), silent=False)
        log(f'ALERT nuevo: services={new_services} timers={new_timers} cron={len(new_cron)} lineas')

    now_ts = time.time()
    removed_alerts = state.get('last_removed_alert', {})
    removed_items = [('servicio', s) for s in removed_services] + [('timer', t) for t in removed_timers]
    if removed_cron:
        removed_items.append(('cron', f'{len(removed_cron)} línea(s)'))
    for kind, name in removed_items:
        key = f'{kind}:{name}'
        last = removed_alerts.get(key, 0)
        if now_ts - last > REMOVED_COOLDOWN_SEC:
            tg(f'ℹ️ <b>Architecture Map</b>: {kind} conocido ya no está presente: <code>{name}</code>', silent=True)
            removed_alerts[key] = now_ts
            log(f'INFO removido: {kind} {name}')
    state['last_removed_alert'] = removed_alerts
    save_state(state)

    write_snapshot(services, timers, cron_lines, manifest)
    log(f'STATUS services={len(services)} timers={len(timers)} cron={len(cron_lines)} '
        f'nuevos={len(new_services)+len(new_timers)+len(new_cron)}')


if __name__ == '__main__':
    main()
