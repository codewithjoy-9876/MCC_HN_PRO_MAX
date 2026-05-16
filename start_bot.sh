#!/usr/bin/env bash
# Local launcher (background mode). For Railway, use run_forever.sh (set in Dockerfile CMD).
set -u
cd "$(dirname "$0")"
nohup python3 mcc_supervisor.py > nohup.out 2>&1 &
echo $! > mcc_supervisor.pid
sleep 3
ps -ef | grep -E 'MinecraftClient|mcc_supervisor.py' | grep -v grep || true
