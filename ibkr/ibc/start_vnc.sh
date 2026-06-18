#!/bin/bash
IBC_PATH="/opt/sigma/ibkr/ibc"
LOG="/opt/sigma/ibkr/logs"
mkdir -p "$LOG"

export DISPLAY=:2

echo "=== SIGMA ENGINE - IBKR con VNC Display :2 ==="
echo "Iniciando IB Gateway en display VNC :2..."

bash "${IBC_PATH}/gatewaystart.sh" -inline > "${LOG}/ibc_startup.log" 2>&1 &
GW_PID=$!
echo "Gateway PID: $GW_PID"
echo "Puedes ver la ventana en VNC: http://178.104.10.97:6080/vnc.html (pass: sigma123)"
echo "Esperando conexion en puerto 4001 (hasta 5 min, aprueba 2FA en la ventana VNC)..."

for i in $(seq 1 60); do
    sleep 5
    if nc -z 127.0.0.1 4001 2>/dev/null; then
        echo ""
        echo "Gateway conectado en puerto 4001!"
        break
    fi
    echo -n "."
    if ! kill -0 $GW_PID 2>/dev/null; then
        echo ""
        echo "ERROR: Gateway process died. Log:"
        tail -20 "${LOG}/ibc_startup.log"
        exit 1
    fi
done

if ! nc -z 127.0.0.1 4001 2>/dev/null; then
    echo ""
    echo "ERROR: Gateway no respondio en 5 minutos."
    tail -20 "${LOG}/ibc_startup.log"
    kill $GW_PID 2>/dev/null
    exit 1
fi

echo "Descargando datos historicos 1H/4H (5 anyos)..."
/opt/sigma_env/bin/python /opt/sigma/ibkr/ibkr_historical_fetcher.py
STATUS=$?

echo "Deteniendo gateway..."
kill $GW_PID 2>/dev/null

if [ $STATUS -eq 0 ]; then
    echo "Descarga completada!"
    ls -lh /opt/sigma/models/data_*_1h_max.csv 2>/dev/null
else
    echo "ERROR en descarga. Status: $STATUS"
fi
