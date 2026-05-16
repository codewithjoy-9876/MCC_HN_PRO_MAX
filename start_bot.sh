#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
nohup ./run_forever.sh > nohup.out 2>&1 &
echo $! > mcc_supervisor.pid
sleep 3
ps -ef | grep -E 'MinecraftClient|mcc_supervisor.py|run_forever.sh' | grep -v grep || true
