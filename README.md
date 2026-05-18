# HN_PRO_MAX — Minecraft MCC 24/7 AFK Presence Bot

HN_PRO_MAX is now a **single-purpose AFK presence bot** built on top of [Minecraft Console Client (MCC) v26.1](https://github.com/MCCTeam/Minecraft-Console-Client) for **HN25462.aternos.me:26624**.

This build is intentionally minimal:
- **No owner system**
- **No combat**
- **No lumberjack / block breaking**
- **No inventory workflows**
- **No proactive chat spam**

Its job is only to stay online, keep a small area active, move continuously, track coordinates, attempt sleep automatically, and expose quiet health/state telemetry.

---

## What this bot does

### 24/7 AFK presence
- Reconnects automatically after disconnects
- Keeps moving every ~**1.15 seconds**
- Uses small look / move / center / sneak cycles designed for a shelter area
- Intended to remain inside a controlled AFK shelter near your iron farm

### Silent self-awareness
- Tracks current coordinates from MCC movement reports
- Tracks whether movement progress is happening
- Tracks last known players seen in chat / join events
- Tracks hostile hints from MCC logs
- Tracks bed attempt results internally
- Tracks simple weather/time hints from sleep and weather-related logs
- **Does not spam public chat** about day/night or sleep failure

### Automatic sleep only
- Periodically runs `/bed sleep 8`
- Stores bed failure reasons internally instead of announcing them in chat
- Meant for a shelter with a nearby bed already prepared

### Railway health endpoint
`/health` returns JSON state such as:
- joined / connected_once
- AFK mode
- coordinates
- movement progress
- bed awareness
- weather hint
- nearby player / hostile awareness

---

## Operational assumptions
- The bot should remain inside a safe enclosure / shelter.
- The working area within roughly **12 chunks** should stay relevant for your farm setup.
- The bot should never stay fully idle; it keeps issuing movement/view commands every **1–2 seconds**.

---

## Removed from this build
- owner-only commands
- follow / protect logic
- combat
- wood gathering
- digging
- dropping / equipping
- public helper chat replies

---

## Local run
```bash
cd mccbot
chmod +x download_mcc.sh install_mcc.sh run_forever.sh start_bot.sh
./download_mcc.sh
./run_forever.sh
```

---

## Railway variables
Set:
- `PORT=8080`
- `PYTHONUNBUFFERED=1`

Do **not** set owner variables for this AFK build.

---

## Files of interest
- `mcc_supervisor.py` — AFK presence supervisor
- `health_server.py` — standalone Railway health server reading `presence_state.json`
- `run_forever.sh` — wrapper that restarts the supervisor forever
- `MCC_HN_PRO_MAX.ini` — MCC connection and bot config

---

## Server info
- Host: `HN25462.aternos.me`
- Port: `26624`
- Game version: Paper 26.1.2
- Auth: cracked / offline
