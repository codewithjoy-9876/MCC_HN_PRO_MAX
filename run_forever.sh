#!/usr/bin/env bash
# Wrapper that keeps the supervisor running 24/7.
# If supervisor exits for any reason, it restarts after a short backoff.

set -u
cd "$(dirname "$0")"

trap 'echo "[$(date -u +%FT%TZ)] received SIGTERM/SIGINT, exiting"; exit 0' SIGTERM SIGINT

BACKOFF=5
MAX_BACKOFF=60

while true; do
  echo "[$(date -u +%FT%TZ)] starting supervisor"
  python3 mcc_supervisor.py
  CODE=$?
  echo "[$(date -u +%FT%TZ)] supervisor exited with code $CODE; restarting in ${BACKOFF}s"
  sleep "$BACKOFF"
  if [ "$BACKOFF" -lt "$MAX_BACKOFF" ]; then
    BACKOFF=$((BACKOFF * 2))
    if [ "$BACKOFF" -gt "$MAX_BACKOFF" ]; then
      BACKOFF=$MAX_BACKOFF
    fi
  fi
done
