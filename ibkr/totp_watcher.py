#!/usr/bin/env python3
"""Watcher that only reacts to NEW 2FA events (ignores historical log lines)."""
import subprocess, os, time
from pathlib import Path

LOG = Path("/opt/sigma/ibkr/logs/ibc-3.19.0_GATEWAY-1045_Tuesday.txt")
CODE_FILE = Path("/tmp/totp_code.txt")
READY_FILE = Path("/tmp/totp_ready.txt")

def get_java_env():
    try:
        pid = subprocess.check_output(["pgrep", "-n", "java"]).decode().strip()
        env_raw = open(f"/proc/{pid}/environ", "rb").read()
        env = {}
        for item in env_raw.split(b"\x00"):
            if b"=" in item:
                k, v = item.split(b"=", 1)
                env[k.decode(errors="replace")] = v.decode(errors="replace")
        return env.get("DISPLAY", ""), env.get("XAUTHORITY", "")
    except:
        return "", ""

def enter_code(code, display, xauth):
    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = xauth
    r1 = subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "30", code], env=env, capture_output=True)
    time.sleep(0.2)
    r2 = subprocess.run(["xdotool", "key", "Return"], env=env, capture_output=True)
    print(f"[W] Entered {code} | rc={r1.returncode},{r2.returncode}", flush=True)
    return r1.returncode == 0

print("[W] Started. Recording current log size to ignore old events...", flush=True)
CODE_FILE.unlink(missing_ok=True)
READY_FILE.unlink(missing_ok=True)

# Mark current log end — only process NEW lines after this point
baseline = 0
if LOG.exists():
    baseline = max(0, len(LOG.read_text(errors="replace").splitlines()) - 30)
print(f"[W] Baseline: {baseline} lines. Watching for new 2FA events...", flush=True)

handled_events = set()

while True:
    try:
        if LOG.exists():
            lines = LOG.read_text(errors="replace").splitlines()
            new_lines = lines[baseline:]

            for i, line in enumerate(new_lines):
                if ("Second Factor Authentication; event=Focused" in line or
                        "Second Factor Authentication; event=Opened" in line):
                    event_key = f"{baseline+i}:{line[:50]}"
                    if event_key in handled_events:
                        continue
                    handled_events.add(event_key)

                    display, xauth = get_java_env()
                    READY_FILE.write_text("ready")
                    print(f"[W] NEW 2FA dialog! DISPLAY={display}. Waiting for code (28s)...", flush=True)

                    for _ in range(28):
                        if CODE_FILE.exists():
                            code = CODE_FILE.read_text().strip()
                            CODE_FILE.unlink(missing_ok=True)
                            READY_FILE.unlink(missing_ok=True)
                            if code and code.isdigit():
                                enter_code(code, display, xauth)
                            else:
                                print(f"[W] Invalid code: '{code}'", flush=True)
                            break
                        time.sleep(1)
                    else:
                        print("[W] Timeout waiting for code", flush=True)
                        READY_FILE.unlink(missing_ok=True)

            baseline = len(lines)
    except Exception as e:
        print(f"[W] err: {e}", flush=True)
    time.sleep(0.5)
