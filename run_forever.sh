#!/usr/bin/env bash
# Production wrapper: keep the supervisor alive 24/7.
# Exit codes:
#   0  = clean shutdown (SIGTERM / SIGINT)
#   *  = supervisor crashed, will restart with backoff
set -uo pipefail
cd "$(dirname "$0")"

trap 'echo "[wrapper] received signal, terminating children and exiting"; pkill -f "MinecraftClient" 2>/dev/null || true; pkill -f "python3 mcc_supervisor.py" 2>/dev/null || true; exit 0' SIGTERM SIGINT

BACKOFF=5
MAX_BACKOFF=60
HEALTH_PID=""
TEMPLATE_CONFIG="$(pwd)/MCC_HN_PRO_MAX.ini"
RUNTIME_CONFIG="$(pwd)/MCC_HN_PRO_MAX.runtime.ini"

start_health_server() {
  if [ -n "$HEALTH_PID" ] && kill -0 "$HEALTH_PID" 2>/dev/null; then
    return
  fi
  echo "[$(date -u +%FT%TZ)] [wrapper] starting standalone health server on port ${PORT:-8080}"
  python3 ./health_server.py >/tmp/health_server.log 2>&1 &
  HEALTH_PID=$!
}

prepare_runtime_config() {
  export MCC_TEMPLATE_CONFIG="$TEMPLATE_CONFIG"
  export MCC_RUNTIME_CONFIG="$RUNTIME_CONFIG"
  if python3 ./resolve_runtime_config.py; then
    export MCC_CONFIG_PATH="$RUNTIME_CONFIG"
  else
    echo "[$(date -u +%FT%TZ)] [wrapper] runtime config resolution failed, using template config"
    export MCC_CONFIG_PATH="$TEMPLATE_CONFIG"
  fi
}

while true; do
  start_health_server
  pkill -f "MinecraftClient" 2>/dev/null || true
  if [ ! -x ./MinecraftClient ]; then
    echo "[$(date -u +%FT%TZ)] [wrapper] MinecraftClient missing, downloading"
    ./download_mcc.sh
  fi
  prepare_runtime_config
  echo "[$(date -u +%FT%TZ)] [wrapper] starting supervisor with config ${MCC_CONFIG_PATH}"
  export DISABLE_EMBEDDED_HEALTH_SERVER=1
  python3 mcc_supervisor.py
  rc=$?
  echo "[$(date -u +%FT%TZ)] [wrapper] supervisor exited with code $rc"

  if [ "$rc" -eq 0 ]; then
    BACKOFF=5
  elif [ "$rc" -eq 17 ]; then
    BACKOFF=8
  else
    BACKOFF=$(( BACKOFF * 2 ))
    if [ "$BACKOFF" -gt "$MAX_BACKOFF" ]; then BACKOFF=$MAX_BACKOFF; fi
  fi

  echo "[$(date -u +%FT%TZ)] [wrapper] restarting in ${BACKOFF}s"
  sleep "$BACKOFF"
done
