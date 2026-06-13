#!/bin/bash
DISPLAY=:103
XAUTHORITY=/tmp/xvfb-run.sNsYh1/Xauthority
export DISPLAY XAUTHORITY

echo "=== xdotool windows ==="
xdotool search --name "" 2>/dev/null | while read wid; do
  title=$(xdotool getwindowname "$wid" 2>/dev/null)
  echo "  $wid: $title"
done

echo ""
echo "=== 2FA dialog? ==="
WIDS=$(xdotool search --name "Second Factor" 2>/dev/null)
echo "Second Factor windows: $WIDS"
WIDS2=$(xdotool search --name "Authenticat" 2>/dev/null)
echo "Authenticating windows: $WIDS2"

echo ""
echo "=== xdotool focus test ==="
# Try to find any IBC-related window
ALL=$(xdotool search --name "IBKR" 2>/dev/null)
echo "IBKR windows: $ALL"
