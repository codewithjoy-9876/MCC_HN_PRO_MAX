#!/usr/bin/env bash
# Download the MCC Linux x64 binary if not already present.
# Used by Dockerfile build step so the heavy binary is never committed to git.
set -e
cd "$(dirname "$0")"

if [ -x "./MinecraftClient" ]; then
  echo "MinecraftClient already present, skipping download"
  exit 0
fi

# Pin to the specific release that supports Paper 26.1.2 (protocol 775).
MCC_TAG="${MCC_TAG:-20260507-439}"
URL="https://github.com/MCCTeam/Minecraft-Console-Client/releases/download/${MCC_TAG}/MinecraftClient-${MCC_TAG}-linux-x64"

echo "Downloading MCC ${MCC_TAG} (linux-x64) ..."
curl -fSL --retry 3 --retry-delay 2 -o ./MinecraftClient "$URL"
chmod +x ./MinecraftClient
echo "MCC binary saved to $(pwd)/MinecraftClient ($(du -h ./MinecraftClient | cut -f1))"
