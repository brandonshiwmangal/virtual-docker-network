#!/bin/bash
# FIX: no "set -e" - one failed report used to kill the container and
#      put it in a restart loop. Errors are now logged and the loop continues.

log() { echo "[netops01] $(date '+%H:%M:%S') $*"; }

INTERVAL="${MONITOR_INTERVAL:-60}"
STARTUP_WAIT="${STARTUP_WAIT:-15}"

log "Network Operations Manager starting..."
log "Waiting ${STARTUP_WAIT} s for fw01 and rt01 to initialise..."
sleep "$STARTUP_WAIT"

log "Entering continuous monitoring loop (every ${INTERVAL} s)..."
# FIX: monitor.py now runs its own loop and survives per-section errors.
#      exec makes it PID 1 so "docker stop" shuts it down cleanly.
exec python3 /monitor.py --interval "$INTERVAL"
