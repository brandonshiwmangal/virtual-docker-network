#!/bin/bash
set -e

log() { echo "[netops01] $(date '+%H:%M:%S') $*"; }

log "Network Operations Manager starting..."
log "Waiting 15 s for fw01 and rt01 to initialise..."
sleep 15

log "Running initial network assessment..."
python3 /monitor.py --once

log "Entering continuous monitoring loop (every 60 s)..."
while true; do
    sleep 60
    python3 /monitor.py --once
done
