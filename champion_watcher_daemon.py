#!/usr/bin/env python3
"""champion_watcher_daemon.py — vigila /opt/sigma/models/ y dispara champion_watcher al instante.

Filosofia: cuando un trial Optuna escribe un nuevo champion JSON, no hay que esperar al cron.
Detecta cambios en mtime cada 15s y ejecuta champion_watcher solo si hubo cambios.

Idempotente: si nada cambio, solo log + sleep (no spam de runs).
"""
import os, time, subprocess, sys
from pathlib import Path
from datetime import datetime

POLL_INTERVAL = 15            # segundos entre checks
DEBOUNCE_SEC  = 8             # esperar 8s tras ultimo cambio antes de disparar (evita race con writes parciales)
LOG_PATH      = "/opt/sigma/results/reports/champion_watcher_daemon.log"
WATCHER_PATH  = "/opt/sigma/champion_watcher.py"
PYTHON_BIN    = "/opt/sigma_env/bin/python"
MODELS_DIR    = Path("/opt/sigma/models")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def scan_mtimes():
    """Retorna {filepath: mtime} de todos los JSON en models/."""
    state = {}
    if not MODELS_DIR.exists():
        return state
    for tf_dir in MODELS_DIR.iterdir():
        if not tf_dir.is_dir():
            continue
        for jf in tf_dir.glob("*.json"):
            try:
                state[str(jf)] = jf.stat().st_mtime
            except Exception:
                pass
    return state

def diff_states(old, new):
    """Retorna lista de archivos nuevos o modificados."""
    changes = []
    for fp, mt in new.items():
        if fp not in old or old[fp] != mt:
            changes.append(fp)
    return changes

def fire_watcher():
    """Ejecuta champion_watcher.py una vez."""
    try:
        r = subprocess.run(
            [PYTHON_BIN, WATCHER_PATH],
            cwd="/opt/sigma",
            capture_output=True, text=True, timeout=120
        )
        out = (r.stdout or "")[-500:]
        if "rotation" in out.lower() or "fresh" in out.lower() or "discover" in out.lower():
            log(f"FIRED — watcher output: {out.strip()[:300]}")
        else:
            log(f"FIRED — sin cambios reales (false positive de mtime)")
        return True
    except subprocess.TimeoutExpired:
        log("FIRED — watcher timeout 120s")
    except Exception as e:
        log(f"FIRED — ERROR: {type(e).__name__}: {e}")
    return False

def main():
    log("daemon iniciado — polling cada " + str(POLL_INTERVAL) + "s, debounce " + str(DEBOUNCE_SEC) + "s")
    last_state = scan_mtimes()
    log(f"baseline: {len(last_state)} archivos JSON tracked")
    pending_change_at = 0  # timestamp del ultimo cambio detectado pero no disparado aun

    while True:
        try:
            time.sleep(POLL_INTERVAL)
            new_state = scan_mtimes()
            changes = diff_states(last_state, new_state)
            now = time.time()

            if changes:
                if pending_change_at == 0:
                    log(f"cambio detectado ({len(changes)} archivos), iniciando debounce {DEBOUNCE_SEC}s")
                pending_change_at = now
                last_state = new_state  # tracking actualizado, pero no firing aun
                continue

            # Sin cambios en este tick — verificar si hay debounce pendiente que ya cumplio
            if pending_change_at > 0 and (now - pending_change_at) >= DEBOUNCE_SEC:
                log(f"debounce cumplido, disparando watcher")
                fire_watcher()
                pending_change_at = 0

        except KeyboardInterrupt:
            log("daemon detenido por SIGINT")
            break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")
            time.sleep(POLL_INTERVAL * 2)

if __name__ == "__main__":
    main()
