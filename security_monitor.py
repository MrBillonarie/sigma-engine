#!/usr/bin/env python3
"""
SIGMA Security Monitor — corre cada 30 min via cron.
Todas las alertas van al chat PRIVADO del admin, nunca al grupo público.
"""
import os
import json
import subprocess
import time
import stat
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# ─── Config ──────────────────────────────────────────────────────────────────
def _load_token() -> str:
    p = Path("/opt/sigma/config/tg_token.txt")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("SIGMA_TG_TOKEN", "")

TOKEN      = _load_token()
ADMIN_CHAT = "6085164298"   # chat privado admin — nunca el grupo público
BASE       = Path("/opt/sigma")
LOG_FILE   = Path("/var/log/sigma_security.log")
STATE_FILE = BASE / "results" / "reports" / "security_monitor_state.json"

SECRETS_FILE    = BASE / "engine" / "config" / "secrets.json"
WEB_SERVER_FILE = BASE / "web_server.py"

COOLDOWN = {
    "secrets_perms":  3600 * 4,
    "multi_instance": 1800,
    "file_modified":  3600,
    "login_attempts": 3600 * 2,
    "new_bans":       3600 * 6,
    "ssh_login":      3600 * 4,
    "ssh_brute":      1800,
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


def tg_send(chat_id: str, text: str, silent: bool = False):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":              chat_id,
            "text":                 text,
            "parse_mode":           "HTML",
            "disable_notification": str(silent).lower(),
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        log(f"TG -> {chat_id}: {text[:80]}")
    except Exception as e:
        log(f"TG fail: {e}")


def admin_alert(text: str, silent: bool = False):
    """Siempre al chat privado del admin, nunca al grupo público."""
    tg_send(ADMIN_CHAT, text, silent=silent)


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
    last = state.get(f"last_{key}", 0)
    return (time.time() - last) > COOLDOWN.get(key, 3600)


def mark_sent(state: dict, key: str):
    state[f"last_{key}"] = time.time()


def run(cmd: str, timeout: int = 15) -> tuple:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


# ─── Checks ──────────────────────────────────────────────────────────────────

def check_secrets_perms(state: dict) -> list:
    issues = []
    if not SECRETS_FILE.exists():
        issues.append("⚠️ secrets.json no encontrado")
        return issues
    try:
        mode = oct(stat.S_IMODE(SECRETS_FILE.stat().st_mode))
        if mode != "0o600":
            issues.append(
                f"🔴 <b>SEGURIDAD</b>: secrets.json tiene permisos <code>{mode}</code>\n"
                f"   Fix: <code>chmod 600 /opt/sigma/engine/config/secrets.json</code>"
            )
            log(f"ALERT secrets.json perms={mode}")
        else:
            log(f"OK secrets.json perms={mode}")
    except Exception as e:
        issues.append(f"⚠️ No se pudo verificar permisos: {e}")
    return issues


def check_multi_instance(state: dict) -> list:
    issues = []
    rc, out = run("pgrep -f 'web_server.py' | wc -l")
    try:
        count = int(out.strip())
    except ValueError:
        count = 0
    if count > 1:
        _, pids = run("pgrep -f 'web_server.py'")
        issues.append(
            f"🔴 <b>MULTI-INSTANCIA</b>: {count} procesos sigma-web\n"
            f"   PIDs: <code>{pids.replace(chr(10), ', ')}</code>\n"
            f"   Fix: <code>pkill -o -f web_server.py</code>"
        )
        log(f"ALERT multi-instancia count={count}")
    else:
        log(f"OK sigma-web instancias={count}")
    return issues


def check_web_server_modified(state: dict) -> list:
    issues = []
    if not WEB_SERVER_FILE.exists():
        return issues
    mtime = WEB_SERVER_FILE.stat().st_mtime
    age_seconds = time.time() - mtime
    if age_seconds < 7200:
        rc, git_out = run(f"git -C {BASE} status --porcelain web_server.py 2>/dev/null")
        if git_out.strip():
            modified_min = int(age_seconds / 60)
            issues.append(
                f"⚠️ <b>ARCHIVO MODIFICADO SIN COMMIT</b>\n"
                f"   <code>web_server.py</code> modificado hace <b>{modified_min} min</b> sin commitear."
            )
            log(f"ALERT web_server.py modificado {modified_min}min sin commit")
        else:
            log("OK web_server.py commiteado")
    else:
        log("OK web_server.py sin cambios recientes")
    return issues


def check_dashboard_login_attempts(state: dict) -> list:
    issues = []
    rc, out = run(
        "journalctl -u sigma-web --since '1 hour ago' --no-pager -q 2>/dev/null "
        "| grep -iE 'unauthorized|forbidden|invalid.*password|auth.*fail|401|403' "
        "| tail -20",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        sample = "\n".join(lines[-3:])
        issues.append(
            f"🔶 <b>LOGIN FALLIDOS dashboard</b>: {len(lines)} en última hora\n"
            f"   <code>{sample[:300]}</code>"
        )
        log(f"ALERT login_attempts count={len(lines)}")
    else:
        log("OK sin login fallidos en dashboard")
    return issues


def check_fail2ban_new_bans(state: dict) -> list:
    issues = []
    rc, out = run(
        "journalctl -u fail2ban --since '1 hour ago' --no-pager -q 2>/dev/null | grep 'Ban ' | tail -20",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        ips = []
        for line in lines:
            parts = line.split("Ban ")
            if len(parts) > 1:
                ips.append(parts[-1].strip().split()[0])
        ip_list = ", ".join(ips[-5:]) if ips else "desconocidas"
        issues.append(
            f"ℹ️ <b>fail2ban</b>: {len(lines)} IPs baneadas en última hora\n"
            f"   <code>{ip_list}</code>"
        )
        log(f"INFO fail2ban new_bans={len(lines)}")
    else:
        log("OK fail2ban sin nuevos bans")
    return issues


def check_ssh_auth_log(state: dict) -> list:
    issues = []
    rc, out = run(
        "journalctl -u ssh --since '2 hours ago' --no-pager -q 2>/dev/null | grep 'Accepted' | tail -10",
        timeout=20
    )
    if out.strip():
        lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
        issues.append(
            f"ℹ️ <b>SSH Login exitoso</b> ({len(lines)} en 2h):\n"
            f"   <code>{chr(10).join(lines)[:400]}</code>"
        )
        log(f"INFO ssh_accepted count={len(lines)}")

    rc, out = run(
        "journalctl -u ssh --since '2 hours ago' --no-pager -q 2>/dev/null "
        r"| grep -c 'Failed password\|Invalid user' || echo 0",
        timeout=20
    )
    try:
        fail_count = int(out.strip())
    except ValueError:
        fail_count = 0

    if fail_count > 50:
        issues.append(
            f"🔴 <b>BRUTE-FORCE SSH</b>: {fail_count} intentos en 2h\n"
            f"   <code>fail2ban-client status sshd</code>"
        )
        log(f"ALERT brute_force ssh fail_count={fail_count}")
    else:
        log(f"OK ssh failed_2h={fail_count}")
    return issues


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log("=== SIGMA Security Monitor iniciando ===")
    state = load_state()
    all_alerts = []

    checks = [
        ("secrets_perms",  check_secrets_perms),
        ("multi_instance", check_multi_instance),
        ("file_modified",  check_web_server_modified),
        ("login_attempts", check_dashboard_login_attempts),
        ("new_bans",       check_fail2ban_new_bans),
    ]
    for key, fn in checks:
        issues = fn(state)
        if issues and cooldown_ok(state, key):
            all_alerts.extend(issues)
            mark_sent(state, key)

    ssh_issues = check_ssh_auth_log(state)
    ssh_login = [i for i in ssh_issues if "Login exitoso" in i]
    ssh_bf    = [i for i in ssh_issues if "BRUTE-FORCE" in i]

    if ssh_login and cooldown_ok(state, "ssh_login"):
        all_alerts.extend(ssh_login)
        mark_sent(state, "ssh_login")
    if ssh_bf and cooldown_ok(state, "ssh_brute"):
        all_alerts.extend(ssh_bf)
        mark_sent(state, "ssh_brute")

    if all_alerts:
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"🔒 <b>SIGMA Security Monitor</b> — {ts_str}\n\n"
        body = "\n\n".join(all_alerts)
        msg = header + body
        if len(msg) > 4000:
            msg = msg[:3900] + "\n\n...(truncado)"
        is_critical = any("🔴" in a for a in all_alerts)
        admin_alert(msg, silent=not is_critical)
        log(f"SENT {len(all_alerts)} alerts -> admin privado (critical={is_critical})")
    else:
        log("OK sin alertas — sistema limpio")

    save_state(state)
    log("=== SIGMA Security Monitor finalizado ===\n")


if __name__ == "__main__":
    main()
