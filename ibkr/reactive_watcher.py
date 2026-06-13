#!/usr/bin/env python3
"""Reactive TOTP watcher v3 - precise click on input field + OK button."""
import time, os, subprocess, sys, socket
from pathlib import Path

CODE_FILE = Path("/tmp/totp_code.txt")
READY_FILE = Path("/tmp/totp_ready.txt")
DISPLAY = ":103"
XAUTH = "/tmp/xvfb-run.sNsYh1/Xauthority"
ENV = {**os.environ, "DISPLAY": DISPLAY, "XAUTHORITY": XAUTH}

def log(msg):
    print(f"[W3] {msg}", flush=True)

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

def click_at(ax, ay):
    xdo("mousemove", str(ax), str(ay))
    time.sleep(0.2)
    xdo("click", "1")
    time.sleep(0.2)

def enter_code(wid, code):
    x, y, w, h = get_window_pos(wid)
    log(f"Dialog at {x},{y} size {w}x{h}")

    # From screenshot: input field center ~(474+140, 445+42) = (614, 487)
    # Relative: x=42% w, y=31% h
    input_x = x + int(w * 0.42)
    input_y = y + int(h * 0.31)

    # OK button from screenshot: ~(474+99, 445+84) = (573, 529)
    # Relative: x=30% w, y=63% h
    ok_x = x + int(w * 0.30)
    ok_y = y + int(h * 0.63)

    log(f"Clicking input field at {input_x},{input_y}")
    click_at(input_x, input_y)

    # Triple-click to select existing content
    xdo("mousemove", str(input_x), str(input_y))
    time.sleep(0.1)
    xdo("click", "--repeat", "3", "1")
    time.sleep(0.2)

    # Clear
    xdo("key", "ctrl+a")
    time.sleep(0.1)
    xdo("key", "BackSpace")
    time.sleep(0.1)

    # Type code digit by digit
    log(f"Typing: {code}")
    for ch in code:
        xdo("key", ch)
        time.sleep(0.08)
    time.sleep(0.4)

    log(f"Clicking OK at {ok_x},{ok_y}")
    click_at(ok_x, ok_y)
    log("Submitted")

log("Watcher v3 started")
READY_FILE.write_text("ready")
if CODE_FILE.exists():
    CODE_FILE.unlink()

wid = find_2fa_window()
log(f"Initial dialog: {wid}")
last_check = time.time()

while True:
    if CODE_FILE.exists():
        try:
            code = CODE_FILE.read_text().strip()
            CODE_FILE.unlink()
            if code and code.isdigit() and len(code) == 6:
                log(f"Got code: {code}")
                wid = find_2fa_window()
                if wid:
                    enter_code(wid, code)
                    log("Waiting for port 4001...")
                    for i in range(90):
                        try:
                            s = socket.create_connection(("127.0.0.1", 4001), 1)
                            s.close()
                            log(f"PORT 4001 OPEN! Connected after {i}s")
                            READY_FILE.unlink() if READY_FILE.exists() else None
                            subprocess.Popen(["/opt/sigma_env/bin/python",
                                              "/opt/sigma/ibkr/ibkr_historical_fetcher.py"],
                                             stdout=open("/opt/sigma/ibkr/logs/fetcher.log", "w"),
                                             stderr=subprocess.STDOUT)
                            log("Data fetcher started!")
                            sys.exit(0)
                        except:
                            pass
                        time.sleep(1)
                    log("Port still closed after 90s")
                else:
                    log("No 2FA dialog found")
            else:
                log(f"Invalid code: {code!r}")
        except Exception as e:
            log(f"Error: {e}")

    now = time.time()
    if now - last_check > 8:
        wid = find_2fa_window()
        if wid:
            READY_FILE.write_text("ready")
            log(f"Dialog alive: {wid}")
        else:
            READY_FILE.unlink() if READY_FILE.exists() else None
            log("No dialog")
        last_check = now

    time.sleep(1)
