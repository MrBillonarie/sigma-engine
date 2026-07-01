#!/usr/bin/env python3
"""
SIGMA Security Monitor — corre cada 30 min via cron.
Verifica:
  1. Permisos de secrets.json (debe ser 600)
  2. Solo 1 instancia de sigma-web corriendo
  3. web_server.py no modificado en las últimas 2h sin commit git
  4. Intentos de login fallidos al dashboard (grep en journalctl)
  5. Nuevas IPs baneadas por fail2ban en la última hora
  6. Login SSH exitoso / brute-force activo
Envía alerta Telegram si alguna condición falla.

Cron: */30 * * * * python3 /opt/sigma/security_monitor.py >> /var/log/sigma_security.log 2>&1
"""
import os
import sys
import json
import subprocess
import time
import stat
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# ─── Config ──────────────────────────────────────────────────────────────────
TOKEN    = open('/opt/sigma/config/tg_token.txt').read().strip()
CHAT_ID  = "-1003787411069"
BASE     = Path("/opt/sigma")
LOG_FILE = Path("/var/log/sigma_security.log")
STATE_FILE = BASE / "results" / "reports" / "security_monitor_state.json"

SECRETS_FILE    = BASE / "engine/config/secrets.json"
WEB_SERVER_FILE = BASE / "web_server.py"

# Cooldowns (segundos) para evitar spam de alertas
COOLDOWN = {
    "secrets_perms":   3600 * 4,   # 4 horas
    "multi_instance":  1800,        # 30 min
    "file_modified":   3600,        # 1 hora
    "login_attempts":  3600 * 2,    # 2 horas
    "new_bans":        3600 * 6,    # 6 horas
    "ssh_login":       3600 * 4,    # 4 horas
    "ssh_brute":       1800,        # 30 min
}

# ─── Helpers ─────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tg_alert(text: str, silent: bool = False):
    """Envía mensaje Telegram. silent=False genera notificación sonora."""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":              CHAT_ID,
            "text":                 text,
            "parse_mode":           "HTML",
            "disable_notification": str(silent).lower(),
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
        log(f"TG enviado OK: {text[:80]}")
    except Exception as e:
        log(f"TG fail: {e}")


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def cooldown_ok(state: dict, key: str) -> bool:
    """True si podemos enviar alerta (cooldown expirado o no existe)."""
    last = state.get(f"last_{key}", 0)
    return (time.time() - last) > COOLDOWN.get(key, 3600)


def mark_sent(state: dict, key: str):
    state[f"last_{key}"] = time.time()


def run(cmd: str, timeout: int = 15) -> tuple:
    """Ejecuta comando shell, retorna (returncode, output)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


# ─── Checks ──────────────────────────────────────────────────────────────────

def check_secrets_perms(state: dict) -> list:
    """Verifica que secrets.json tenga permisos 600 (solo root puede leer)."""
    issues = []
    if not SECRETS_FILE.exists():
        issues.append("⚠️ secrets.json no encontrado en /opt/sigma/")
        return issues
    try:
        mode = oct(stat.S_IMODE(SECRETS_FILE.stat().st_mode))
        if mode != "0o600":
            issues.append(
                f"🔴 <b>SEGURIDAD</b>: secrets.json tiene permisos <code>{mode}</code>\n"
                f"   Debería ser <code>0o600</code>. Ejecutar: <code>chmod 600 /opt/sigma/secrets.json</code>"
            )
            log(f"ALERT secrets.json perms={mode} (esperado 0o600)")
        else:
            log(f"OK secrets.json perms={mode}")
    except Exception as e:
        issues.append(f"⚠️ No se pudo verificar permisos de secrets.json: {e}")
    return issues


def check_multi_instance(state: dict) -> list:
    """Verifica que solo haya 1 instancia de sigma-web."""
    issues = []
    rc, out = run("pgrep -f 'web_server.py' | wc -l")
    try:
        count = int(out.strip())
    except ValueError:
        count = 0

    if count > 1:
        _, pids = run("pgrep -f 'web_server.py'")
        issues.append(
            f"🔴 <b>MULTI-INSTANCIA</b>: {count} procesos sigma-web detectados\n"
            f"   PIDs: <code>{pids.replace(chr(10), ', ')}</code>\n"
            f"   Matar extras: <code>pkill -o -f web_server.py</code>"
        )
        log(f"ALERT multi-instancia count={count} pids={pids}")
    else:
        log(f"OK sigma-web instancias={count}")
    return issues


def check_web_server_modified(state: dict) -> list:
    """Alerta si web_server.py fue modificado en las últimas 2h pero no commiteado."""
    issues = []
    if not WEB_SERVER_FILE.exists():
        return issues

    mtime = WEB_SERVER_FILE.stat().st_mtime
    age_seconds = time.time() - mtime

    if age_seconds < 7200:  # 2 horas
        rc, git_out = run(
            f"git -C {BASE} status --porcelain web_server.py 2>/dev/null"
        )
        if git_out.strip():
            modified_min = int(age_seconds / 60)
            issues.append(
                f"⚠️ <b>ARCHIVO MODIFICADO SIN COMMIT</b>\n"
                f"   <code>web_server.py</code> modificado hace <b>{modified_min} min</b> y tiene cambios sin commitear.\n"
                f"   git status: <code>{git_out[:100]}</code>"
            )
            log(f"ALERT web_server.py modificado {modified_min}min ago, git dirty: {git_out[:80]}")
        else:
            log(f"OK web_server.py modificado hace {int(age_seconds/60)}min pero está commiteado")
    else:
        log(f"OK web_server.py ultima modificacion hace {int(age_seconds/3600):.1f}h")
    return issues


def check_dashboard_login_attempts(state: dict) -> list:
    """Busca intentos de login fallidos al dashboard en journalctl."""
    issues = []
    rc, out = run(
        "journalctl -u sigma-web --since '1 hour ago' --no-pager -q 2>/dev/null "
        "| grep -iE 'unauthorized|forbidden|invalid.*password|auth.*fail|wrong.*key|401|403' "
        "| tail -20",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        count = len(lines)
        sample = "\n".join(lines[-3:])
        issues.append(
            f"🔶 <b>INTENTOS DE LOGIN FALLIDOS</b> en dashboard\n"
            f"   {count} eventos en la última hora:\n"
            f"   <code>{sample[:300]}</code>"
        )
        log(f"ALERT login_attempts count={count}")
    else:
        log("OK sin intentos de login fallidos en ultima hora")
    return issues


def check_fail2ban_new_bans(state: dict) -> list:
    """Detecta si fail2ban baneó nuevas IPs en la última hora."""
    issues = []
    rc, out = run(
        "journalctl -u fail2ban --since '1 hour ago' --no-pager -q 2>/dev/null "
        "| grep 'Ban ' | tail -20",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        count = len(lines)
        ips = []
        for line in lines:
            parts = line.split("Ban ")
            if len(parts) > 1:
                ip = parts[-1].strip().split()[0]
                ips.append(ip)
        ip_list = ", ".join(ips[-5:]) if ips else "desconocidas"
        issues.append(
            f"ℹ️ <b>fail2ban</b>: {count} nuevas IPs baneadas en la última hora\n"
            f"   Últimas: <code>{ip_list}</code>"
        )
        log(f"INFO fail2ban new_bans={count} ips={ip_list}")
    else:
        log("OK fail2ban sin nuevos bans en ultima hora")
    return issues


def check_ssh_auth_log(state: dict) -> list:
    """Verifica logins SSH exitosos y brute-force activo."""
    issues = []

    # Logins exitosos en últimas 2h
    rc, out = run(
        "journalctl -u ssh --since '2 hours ago' --no-pager -q 2>/dev/null "
        "| grep 'Accepted' | tail -10",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        count = len(lines)
        sample = "\n".join(lines)
        issues.append(
            f"ℹ️ <b>SSH Login exitoso</b> ({count} en últimas 2h):\n"
            f"   <code>{sample[:400]}</code>"
        )
        log(f"INFO ssh_accepted count={count}")

    # Brute force (>50 en 2h)
    rc, out = run(
        "journalctl -u ssh --since '2 hours ago' --no-pager -q 2>/dev/null "
        "| grep -cE 'Failed password|Invalid user' || echo 0",
        timeout=20
    )
    try:
        fail_count = int(out.strip())
    except ValueError:
        fail_count = 0

    if fail_count > 50:
        issues.append(
            f"🔴 <b>BRUTE-FORCE SSH ACTIVO</b>: {fail_count} intentos fallidos en las últimas 2h\n"
            f"   Verificar: <code>fail2ban-client status sshd</code>"
        )
        log(f"ALERT brute_force ssh fail_count={fail_count}")
    else:
        log(f"OK ssh failed_attempts_2h={fail_count}")
    return issues


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log("=== SIGMA Security Monitor iniciando ===")
    state = load_state()
    all_alerts = []

    # 1) Permisos secrets.json
    issues = check_secrets_perms(state)
    if issues and cooldown_ok(state, "secrets_perms"):
        all_alerts.extend(issues)
        mark_sent(state, "secrets_perms")

    # 2) Multi-instancia sigma-web
    issues = check_multi_instance(state)
    if issues and cooldown_ok(state, "multi_instance"):
        all_alerts.extend(issues)
        mark_sent(state, "multi_instance")

    # 3) web_server.py modificado sin commit
    issues = check_web_server_modified(state)
    if issues and cooldown_ok(state, "file_modified"):
        all_alerts.extend(issues)
        mark_sent(state, "file_modified")

    # 4) Intentos de login al dashboard
    issues = check_dashboard_login_attempts(state)
    if issues and cooldown_ok(state, "login_attempts"):
        all_alerts.extend(issues)
        mark_sent(state, "login_attempts")

    # 5) Nuevos bans fail2ban (informativo, cooldown más largo)
    issues = check_fail2ban_new_bans(state)
    if issues and cooldown_ok(state, "new_bans"):
        all_alerts.extend(issues)
        mark_sent(state, "new_bans")

    # 6) SSH auth log
    ssh_issues = check_ssh_auth_log(state)
    ssh_login_issues = [i for i in ssh_issues if "Login exitoso" in i]
    ssh_bf_issues    = [i for i in ssh_issues if "BRUTE-FORCE" in i]

    if ssh_login_issues and cooldown_ok(state, "ssh_login"):
        all_alerts.extend(ssh_login_issues)
        mark_sent(state, "ssh_login")

    if ssh_bf_issues and cooldown_ok(state, "ssh_brute"):
        all_alerts.extend(ssh_bf_issues)
        mark_sent(state, "ssh_brute")

    # Enviar alerta consolidada
    if all_alerts:
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"🔒 <b>SIGMA Security Monitor</b> — {ts_str}\n\n"
        body = "\n\n".join(all_alerts)
        msg = header + body
        if len(msg) > 4000:
            msg = msg[:3900] + "\n\n...(truncado)"
        is_critical = any("🔴" in a for a in all_alerts)
        tg_alert(msg, silent=not is_critical)
        log(f"SENT {len(all_alerts)} alerts to Telegram (critical={is_critical})")
    else:
        log("OK sin alertas — sistema limpio")

    save_state(state)
    log("=== SIGMA Security Monitor finalizado ===\n")


if __name__ == "__main__":
    main()
