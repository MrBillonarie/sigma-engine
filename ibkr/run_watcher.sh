#!/bin/bash
export DISPLAY=:2
export XAUTHORITY=/root/.Xauthority
exec /opt/sigma_env/bin/python /opt/sigma/ibkr/reactive_watcher.py
