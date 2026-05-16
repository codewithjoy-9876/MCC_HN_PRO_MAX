#!/usr/bin/env bash
# Production wrapper: keep the supervisor alive 24/7.
# Exit codes:
#   0  = clean shutdown (SIGTERM / SIGINT)
#   *  = supervisor crashed, will restart with backoff
set -uo pipefail
cd "$(dirname "$0")"

trap 'echo "[wrapper] received signal, exiting"; exit 0' SIGTERM SIGINT

BACKOFF=5
MAX_BACKOFF=60

while true; do
  if [ ! -x ./MinecraftClient ]; then
    echo "[$(date -u +%FT%TZ)] [wrapper] MinecraftClient missing, downloading"
    ./download_mcc.sh
  fi
  echo "[$(date -u +%FT%TZ)] [wrapper] starting supervisor"
  python3 mcc_supervisor.py
  rc=$?
  echo "[$(date -u +%FT%TZ)] [wrapper] supervisor exited with code $rc"

  if [ "$rc" -eq 0 ]; then
    BACKOFF=5
  else
    BACKOFF=$(( BACKOFF * 2 ))
    if [ "$BACKOFF" -gt "$MAX_BACKOFF" ]; then BACKOFF=$MAX_BACKOFF; fi
  fi

  echo "[$(date -u +%FT%TZ)] [wrapper] restarting in ${BACKOFF}s"
  sleep "$BACKOFF"
done
