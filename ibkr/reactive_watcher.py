#!/usr/bin/env python3
"""Reactive TOTP watcher v6 - Tab+Space + click fallback."""
import time, os, subprocess, sys, socket
from pathlib import Path

CODE_FILE = Path("/tmp/totp_code.txt")
DISPLAY = ":2"
XAUTH = "/root/.Xauthority"
ENV = {**os.environ, "DISPLAY": DISPLAY, "XAUTHORITY": XAUTH}

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[W6 {ts}] {msg}", flush=True)

def xdo(*args, timeout=5):
    r = subprocess.run(["xdotool"] + list(args), env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode

def find_2fa_window():
    out, _ = xdo("search", "--name", "Second Factor")
    wids = out.split()
    return wids[-1] if wids else None

def get_window_pos(wid):
    out, _ = xdo("getwindowgeometry", wid)
    x, y, w, h = 0, 0, 0, 0
    for line in out.splitlines():
        if "Position:" in line:
            pos = line.split(":",1)[1].strip().split(" ")[0]
            x, y = [int(v) for v in pos.split(",")]
        if "Geometry:" in line:
            geo = line.split(":",1)[1].strip()
            w, h = [int(v) for v in geo.split("x")]
    return x, y, w, h

def enter_code(wid, code):
    x, y, w, h = get_window_pos(wid)
    log(f"Dialog geometry: pos=({x},{y}) size={w}x{h}")

    # Activate and focus
    xdo("windowactivate", "--sync", wid)
    time.sleep(0.2)
    xdo("windowfocus", "--sync", wid)
    time.sleep(0.2)

    # Click input field (center-ish, upper portion)
    # Based on visual analysis: input at ~50% x, 48% y of dialog
    input_x = x + w // 2
    input_y = y + int(h * 0.48)
    log(f"Click input at ({input_x},{input_y})")
    xdo("mousemove", "--sync", str(input_x), str(input_y))
    time.sleep(0.2)
    xdo("click", "1")
    time.sleep(0.3)

    # Select all + delete existing content
    xdo("key", "--clearmodifiers", "ctrl+a")
    time.sleep(0.15)
    xdo("key", "--clearmodifiers", "Delete")
    time.sleep(0.15)

    # Type the code
    log(f"Typing: {code}")
    xdo("type", "--clearmodifiers", "--delay", "30", code)
    time.sleep(0.4)

    # Method 1: Tab to OK, then Space
    log("Submit via Tab+Space")
    xdo("key", "--clearmodifiers", "Tab")
    time.sleep(0.2)
    xdo("key", "--clearmodifiers", "space")
    time.sleep(0.3)

    # Method 2: Enter key
    log("Submit via Return")
    xdo("key", "--clearmodifiers", "Return")
    time.sleep(0.3)

    # Method 3: Click at OK position
    ok_x = x + int(w * 0.38)
    ok_y = y + int(h * 0.80)
    log(f"Click OK at ({ok_x},{ok_y})")
    xdo("mousemove", "--sync", str(ok_x), str(ok_y))
    time.sleep(0.2)
    xdo("click", "1")
    time.sleep(0.2)
    xdo("click", "1")

    log("All submission methods tried")

log("Watcher v6 ready (Tab+Space+Return+Click)")
if CODE_FILE.exists():
    CODE_FILE.unlink()

wid = find_2fa_window()
log(f"Initial scan: {wid}")
last_check = time.time()

while True:
    if CODE_FILE.exists():
        try:
            code = CODE_FILE.read_text().strip()
            CODE_FILE.unlink()
            if code and code.isdigit() and len(code) == 6:
                log(f"GOT CODE: {code}")
                wid = find_2fa_window()
                if wid:
                    enter_code(wid, code)
                    log("Waiting for port 4001...")
                    for i in range(120):
                        try:
                            s = socket.create_connection(("127.0.0.1", 4001), 1)
                            s.close()
                            log(f"PORT 4001 OPEN after {i}s!")
                            subprocess.Popen(["/opt/sigma_env/bin/python",
                                              "/opt/sigma/ibkr/ibkr_historical_fetcher.py"],
                                             stdout=open("/opt/sigma/ibkr/logs/fetcher.log", "w"),
                                             stderr=subprocess.STDOUT)
                            log("Data fetcher STARTED!")
                            sys.exit(0)
                        except:
                            pass
                        time.sleep(1)
                    log("Port still closed after 120s")
                else:
                    log("NO DIALOG when code arrived - too late?")
            else:
                log(f"Invalid code: {code!r}")
        except Exception as e:
            log(f"Error: {e}")

    now = time.time()
    if now - last_check > 5:
        wid = find_2fa_window()
        log(f"Dialog: {wid if wid else 'NONE'}")
        last_check = now

    time.sleep(0.3)
