#!/usr/bin/env bash
# Fetches the official Minecraft Console Client binary into this folder.
# Source: https://mccteam.github.io/guide/installation.html
set -euo pipefail
cd "$(dirname "$0")"
echo "[install_mcc] downloading MCC binary..."
curl -fsSL https://mccteam.github.io/install.sh | sh
chmod +x MinecraftClient
echo "[install_mcc] done."
./MinecraftClient --help | head -5 || true
