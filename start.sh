#!/bin/bash
# start.sh · runs the PAPER copy desk (copier2) + PAPER hype sniper (hype.py) + dashboard server (server.py) together, each with auto-restart.
# Keys come only from env secrets (HELIUS_API_KEY required; SOLANATRACKER_API_KEY optional). Nothing is baked into the image.
ROOT=${APP_ROOT:-/workspace}; export PORT=${PORT:-7860} HOST=${HOST:-0.0.0.0} PYTHONUNBUFFERED=1
cd "$ROOT/desk"
[ -n "$HELIUS_API_KEY" ] || echo "WARNING: HELIUS_API_KEY secret is not set -> live detection will not work"
[ -f desk_state.json ] || cp seed_state.json desk_state.json
python3 "$ROOT/persist.py" restore
python3 "$ROOT/persist.py" loop &
# copier2 with restart loop
( while true; do cd "$ROOT/desk" && python3 -u copier2.py; echo "[start] copier2 exited ($?), restarting in 5s"; sleep 5; done ) &
# hype sniper (strategy 2, own $100 paper balance) with restart loop; HYPE=0 turns it off
if [ "${HYPE:-1}" != "0" ]; then
  ( while true; do cd "$ROOT/desk" && python3 -u hype.py; echo "[start] hype exited ($?), restarting in 10s"; sleep 10; done ) &
fi
# freshness watchdog: copier2 rewrites desk_state.json every few seconds; if it stalls 180s, kill it so the loop restarts it
( sleep 120; while true; do
    age=$(( $(date +%s) - $(stat -c %Y "$ROOT/desk/desk_state.json" 2>/dev/null || echo 0) ))
    if [ "$age" -gt 180 ]; then echo "[start] state stale ${age}s -> restarting copier2"; pkill -f "python3 -u copier2.py"; sleep 60; fi
    if [ "${HYPE:-1}" != "0" ]; then
      hage=$(( $(date +%s) - $(stat -c %Y "$ROOT/desk/hype_state.json" 2>/dev/null || date +%s) ))
      if [ "$hage" -gt 180 ]; then echo "[start] hype state stale ${hage}s -> restarting hype"; pkill -f "python3 -u hype.py"; fi
    fi
    sleep 30; done ) &
# dashboard in the foreground (keeps the container alive); restarts if it dies
cd "$ROOT/memebot"
while true; do python3 -u server.py; echo "[start] server exited ($?), restarting"; sleep 2; done
