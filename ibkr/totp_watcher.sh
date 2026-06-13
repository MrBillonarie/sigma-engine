#!/bin/bash
AUTH_FILE=$(ls /tmp/xvfb-run.*/Xauthority 2>/dev/null | head -1)
CODE_FILE=/tmp/totp_code.txt
LOG=/opt/sigma/ibkr/logs/ibc-3.19.0_GATEWAY-1045_Tuesday.txt

echo "[WATCHER] Esperando dialogo 2FA..."

while true; do
    if grep -q 'Second Factor Authentication; event=Focused' "$LOG" 2>/dev/null; then
        # 2FA dialog is up - wait for code file
        echo "[WATCHER] 2FA detectado! Esperando codigo en $CODE_FILE"
        for i in $(seq 1 25); do
            if [ -f "$CODE_FILE" ]; then
                CODE=$(cat "$CODE_FILE")
                echo "[WATCHER] Ingresando codigo: $CODE"
                AUTH_FILE=$(ls /tmp/xvfb-run.*/Xauthority 2>/dev/null | head -1)
                DISPLAY=:99 XAUTHORITY="$AUTH_FILE" xdotool type --clearmodifiers --delay 30 "$CODE"
                sleep 0.2
                DISPLAY=:99 XAUTHORITY="$AUTH_FILE" xdotool key Return
                echo "[WATCHER] Codigo ingresado!"
                rm -f "$CODE_FILE"
                sleep 5
                break
            fi
            sleep 1
        done
    fi
    sleep 1
done
