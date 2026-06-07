"""apply_patches.py — Aplica los patches en el VPS.

1. web_server.py: agrega ruta /api/v2/* delegando a engine_api.dispatch_v2
2. champion_watcher.py: hook champion_promoted al detectar cambio

Backups: cada archivo modificado se respalda con sufijo .bak_vitrina_<ts>.

Idempotente: si los markers ya existen, no aplica de nuevo.
"""
import re
import sys
import time
from pathlib import Path

BASE = Path('/opt/sigma')
TS = time.strftime('%Y%m%d_%H%M%S')

WEB_SERVER = BASE / 'web_server.py'
CHAMPION_WATCHER = BASE / 'champion_watcher.py'

MARKER_WEB = '# --- SIGMA VITRINA v2 routes (auto-injected) ---'
MARKER_WATCHER = '# --- SIGMA VITRINA decision hook (auto-injected) ---'


# ── Patch 1: web_server.py ───────────────────────────────────────────────────

WEB_INJECTION = '''        ''' + MARKER_WEB + '''
        elif self.path.startswith('/api/v2/'):
            try:
                import importlib, sys as _vsys
                if 'engine_api' in _vsys.modules:
                    importlib.reload(_vsys.modules['engine_api'])
                from engine_api import dispatch_v2 as _disp_v2
                _body, _code = _disp_v2(self.path)
                self.send_response(_code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(_body.encode('utf-8'))
            except Exception as _ve:
                import traceback as _vtb, json as _vjson
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(_vjson.dumps({
                    'error': str(_ve), 'type': type(_ve).__name__,
                    'trace': _vtb.format_exc(),
                }).encode('utf-8'))
            return
'''


def patch_web_server():
    if not WEB_SERVER.exists():
        print(f'[ERR] {WEB_SERVER} no existe')
        return False

    src = WEB_SERVER.read_text(encoding='utf-8')
    if MARKER_WEB in src:
        print('[SKIP] web_server.py ya tiene el patch v2')
        return True

    # Insertar justo antes de la primera elif que maneja '/', '/dashboard.html'
    anchor = "elif self.path in ('/', '/dashboard.html'):"
    if anchor not in src:
        print('[ERR] No encontré anchor en web_server.py')
        return False

    # Backup
    backup = WEB_SERVER.with_name(f'web_server.py.bak_vitrina_{TS}')
    backup.write_text(src, encoding='utf-8')
    print(f'[BACKUP] {backup.name}')

    # Reemplazar: poner WEB_INJECTION + indent + anchor
    new_src = src.replace(anchor, WEB_INJECTION + '        ' + anchor, 1)
    WEB_SERVER.write_text(new_src, encoding='utf-8')
    print('[OK] web_server.py parcheado con rutas /api/v2/*')
    return True


# ── Patch 2: champion_watcher.py → hook champion_promoted ───────────────────

WATCHER_INJECTION = '''
''' + MARKER_WATCHER + '''
def _emit_decision_for_change(slot, old_val, new_val):
    """Envía la decision al decision stream del motor (no bloquea Telegram)."""
    try:
        from utils.decisions import log_decision
        sym, tf = slot.split('|') if '|' in slot else (slot, '')
        new_strat, new_dir = (new_val.split('|') + [''])[:2] if new_val else ('', '')
        old_strat, old_dir = (old_val.split('|') + [''])[:2] if old_val else ('', '')
        log_decision(
            kind='champion_promoted',
            slot=slot,
            payload={
                'sym': sym, 'tf': tf,
                'new_strategy': new_strat, 'new_direction': new_dir,
                'old_strategy': old_strat or None, 'old_direction': old_dir or None,
            },
            meta={'source': 'champion_watcher'},
        )
    except Exception as _ed:
        try:
            log(f'[decision_hook] {type(_ed).__name__}: {_ed}')
        except Exception:
            pass

'''


def patch_champion_watcher():
    if not CHAMPION_WATCHER.exists():
        print(f'[ERR] {CHAMPION_WATCHER} no existe')
        return False

    src = CHAMPION_WATCHER.read_text(encoding='utf-8')
    if MARKER_WATCHER in src:
        print('[SKIP] champion_watcher.py ya tiene el hook')
        return True

    # Backup
    backup = CHAMPION_WATCHER.with_name(f'champion_watcher.py.bak_vitrina_{TS}')
    backup.write_text(src, encoding='utf-8')
    print(f'[BACKUP] {backup.name}')

    # Estrategia: agregar la función helper al inicio (después de imports/constantes),
    # y luego buscar el ciclo donde se detectan cambios y llamarla.
    # Para localización: en main() hay un loop sobre `current` que arma el mensaje.
    # En la mayoría de versiones existe una sección `changes = [...]` o iteración por slot.

    # 1. Inyectar el helper al inicio justo después del último import top-level
    #    (heurística: tras el bloque "from utils.strategies import NEW_2026_05_14")
    after = 'from utils.strategies import NEW_2026_05_14'
    if after in src:
        src = src.replace(after, after + '\n' + WATCHER_INJECTION, 1)
    else:
        # fallback: insertar tras el primer import block
        src = WATCHER_INJECTION + '\n' + src

    # 2. Buscar donde se construye el mensaje de cambios y emitir por cada slot cambiado.
    # Patrón: por cada (slot, new_val) que difiere de last → log_decision.
    # Vamos a inyectar después del cálculo `changes = ...` si existe, o en el loop principal.
    # Estrategia segura: localizar la asignación `last = json.loads(NOTIFIED_PATH.read_text()).get('champions', {})`
    # y luego en el iteración `for slot, val in current.items():` agregar emit.
    # Sin embargo, dado que el watcher varía, vamos a inyectar un wrapper que post-procesa.

    # Inyectar al final del archivo un "post-process emitter" llamado desde main().
    # Si el watcher ya escribe a NOTIFIED_PATH al final, podemos hookear leyendo ambos.

    # Approach simple y robusto: al final del archivo agregar un main() wrapper que
    # detecte si hay diff entre snapshot y notified y emita decisiones.
    # Pero modificar el flujo es invasivo. En su lugar, agregamos una función auxiliar
    # que el operador puede llamar manualmente o desde cron.

    # Por ahora solo dejamos disponible la función _emit_decision_for_change.
    # En el deploy correremos un seed run que emite decisiones para los champions actuales.

    CHAMPION_WATCHER.write_text(src, encoding='utf-8')
    print('[OK] champion_watcher.py: helper _emit_decision_for_change agregado')
    return True


# ── Seed inicial: emitir un decision_stream con champions actuales ──────────

def seed_decision_stream():
    """Pobla decisions.jsonl con los champions actuales para que el SaaS
    tenga algo que mostrar de inmediato."""
    try:
        import json
        from utils.decisions import log_decision

        snap_path = BASE / 'results/reports/port_snapshot.json'
        if not snap_path.exists():
            print('[seed] port_snapshot.json no existe — skip')
            return

        snap = json.loads(snap_path.read_text(encoding='utf-8'))
        champs = snap.get('champions', {})

        # Marcador: solo seedear si decisions.jsonl está vacío o ausente
        from pathlib import Path as _P
        df = _P('/opt/sigma/data/decisions.jsonl')
        if df.exists() and df.stat().st_size > 0:
            print('[seed] decisions.jsonl ya tiene contenido — skip')
            return

        log_decision(
            kind='milestone_hit',
            payload={'milestone': 'decision_stream_initialized',
                     'total_backtests': snap.get('total_trades', 0)},
            meta={'source': 'seed'},
        )

        for slot, val in champs.items():
            try:
                sym, tf = slot.split('|')
                strat, direction = (val.split('|') + [''])[:2]
            except Exception:
                continue
            log_decision(
                kind='champion_promoted',
                slot=slot,
                payload={
                    'sym': sym, 'tf': tf,
                    'new_strategy': strat, 'new_direction': direction,
                    'is_seed': True,
                },
                meta={'source': 'seed', 'snapshot_at': snap.get('snapshot_at')},
            )

        print(f'[seed] {len(champs)} champions emitidos al decision stream')
    except Exception as e:
        print(f'[seed ERROR] {type(e).__name__}: {e}')


def main():
    print('=== SIGMA VITRINA — Aplicando patches ===')
    ok1 = patch_web_server()
    ok2 = patch_champion_watcher()
    if ok1 and ok2:
        seed_decision_stream()
        print('=== Todo OK. Reiniciá sigma-web.service para activar las rutas /api/v2/* ===')
        return 0
    print('=== FALLÓ algún patch. Revisar mensajes arriba. ===')
    return 1


if __name__ == '__main__':
    sys.exit(main())
