#!/bin/bash
# SIGMA ENGINE - IB Gateway Headless Starter
# Requires xvfb-run for headless Linux operation

TWS_MAJOR_VRSN=1045
IBC_INI=/opt/sigma/ibkr/ibc/config.ini
TRADING_MODE=live
TWOFA_TIMEOUT_ACTION=exit
IBC_PATH=/opt/sigma/ibkr/ibc
TWS_PATH=/root/Jts
TWS_SETTINGS_PATH=
LOG_PATH=/opt/sigma/ibkr/logs
TWSUSERID=
TWSPASSWORD=
FIXUSERID=
FIXPASSWORD=
JAVA_PATH=
HIDE=yes
GATEWAY_OR_TWS=gateway
TWS_INSTALL_DIR=/opt/sigma/ibkr/gateway

mkdir -p "$LOG_PATH"

exec xvfb-run --auto-servernum --server-args='-screen 0 1024x768x24' \
    java -cp "${IBC_PATH}/IBC.jar:${TWS_INSTALL_DIR}/jars/*" \
    ibcalpha.ibc.IbcGateway \
    "${IBC_INI}" \
    "${TWS_INSTALL_DIR}" \
    "${TWS_PATH}" \
    "${TRADING_MODE}" \
    "$@"
