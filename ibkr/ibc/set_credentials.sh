#!/bin/bash
# Run this script to set your IBKR credentials in IBC config
# Usage: bash /opt/sigma/ibkr/ibc/set_credentials.sh

CONFIG="/opt/sigma/ibkr/ibc/config.ini"

echo ""
echo "=== SIGMA ENGINE - IBKR Credential Setup ==="
echo "Esto configura IBC para iniciar IB Gateway con tu cuenta."
echo "ReadOnly=yes: SOLO lectura de datos, sin ejecutar trades."
echo ""
read -p "IBKR Username: " IBKR_USER
read -s -p "IBKR Password: " IBKR_PASS
echo ""

sed -i "s/IbLoginId=.*/IbLoginId=${IBKR_USER}/" "$CONFIG"
sed -i "s/IbPassword=.*/IbPassword=${IBKR_PASS}/" "$CONFIG"

echo ""
echo "Credenciales guardadas en $CONFIG"
echo "Ahora ejecuta: bash /opt/sigma/ibkr/ibc/start_and_download.sh"
