# HN_PRO_MAX — Minecraft MCC Owner-Only Assistant Bot

Production-grade Minecraft bot built on top of [Minecraft Console Client (MCC) v26.1](https://github.com/MCCTeam/Minecraft-Console-Client).
Designed for the Aternos PaperMC server **HN25462.aternos.me:26624** (Minecraft Java 26.1.2 / protocol 775, cracked / offline mode).

The bot username is **HN_PRO_MAX** and it only takes orders from configured owners.

---

## ✨ Features

### 🛡️ Owner-only assistant
- Whitelist via [`owners.txt`](./owners.txt) and / or `OWNER_USERNAMES` env var
- Non-owner messages are ignored at the supervisor layer
- Per-sender rate limit + duplicate-message guard to avoid spammy replies

### 🚶 Always-active behavior
- Visible movement every ~1–2 seconds (look / move / sneak / animation)
- Periodic proactive chat when an owner is nearby
- Hostile-event panic burst to disengage from mobs

### 🛏️ Auto sleep
- Periodic `/bed sleep 8` search around the bot
- Bed failure reasons (`no bed`, `obstructed`, `monsters nearby`, `not night`, …) are translated into Hindi-flavored chat replies

### 🤖 Owner commands
| Command (chat)       | What it does |
| -------------------- | ------------ |
| `come here` / `follow me` | Starts following the owner |
| `stay` / `stop`      | Stops following, returns to patrol |
| `status`             | Reports current mode, follow / protect target, lumberjack progress, last location |
| `sleep now`          | Immediate bed search |
| `protect me`         | Equips sword slot, follows the owner in guard mode |
| `equip sword`        | Switch hotbar to slot 1 (sword) |
| `equip axe`          | Switch hotbar to slot 2 (axe) |
| `equip pickaxe`      | Switch hotbar to slot 3 |
| `equip shovel`       | Switch hotbar to slot 4 |
| `use shield`         | Switch hotbar to slot 5 (shield slot) |
| `drop logs`          | Drops all log item types from inventory |
| `drop food`          | Drops common cooked food and bread items |
| `gather wood`        | Triggers full lumberjack cycle |
| `bring wood`         | Returns and drops collected wood to the owner |
| `find trees`         | Walks toward the nearest log block |
| `cut logs`           | Chops the current target log |
| `help`               | Shows the command list in chat |

### 🌲 Lumberjack mode
State machine: `searching → chopping → returning → dropping`
1. Switches to axe slot
2. Walks toward the nearest `OakLog`, `SpruceLog`, `BirchLog`, etc. within 16 blocks
3. Digs the log block repeatedly
4. After 3 logs, follows the owner and drops the wood

> Wood gathering relies on MCC's `/move <block>` pathfinding and `/dig` command. Real-world success depends on terrain handling, chunk loading, and server permissions.

### 🔁 24/7 connection
- **Layer 1**: MCC `AutoRelog` (in `MCC_HN_PRO_MAX.ini`) reconnects after server disconnects
- **Layer 2**: `run_forever.sh` re-spawns the Python supervisor if it ever exits (with exponential back-off)
- **Layer 3**: Railway / Docker can restart the container itself

### ❤️ Health endpoint
A small JSON endpoint runs on `PORT` (default `8080`):
```
GET /health
```
Returns connection state, mode, owners list, lumberjack state, bot location, uptime.

---

## 🚀 Quick start (local)
```bash
cd mccbot
chmod +x download_mcc.sh install_mcc.sh run_forever.sh start_bot.sh
./download_mcc.sh   # or: ./install_mcc.sh
./run_forever.sh
```

Or background:
```bash
./download_mcc.sh   # only needed the first time
./start_bot.sh
tail -f supervisor_runtime.log
```

Stop:
```bash
pkill -f run_forever.sh
pkill -f mcc_supervisor.py
pkill -f 'MinecraftClient .*MCC_HN_PRO_MAX.ini'
```

---

## 🚂 Deploy on Railway

1. Push this repo to GitHub (already wired in `MCC_HN_PRO_MAX` repo).
2. In Railway, **New Project → Deploy from GitHub repo**.
3. Railway auto-detects the `Dockerfile` and `railway.json`.
4. Set service variables:
   - `OWNER_USERNAMES=.Nirankar66`
   - `PORT=8080`
   - `PYTHONUNBUFFERED=1`
5. Deploy. Health-check path is `/health`.

> Railway storage is ephemeral, so `supervisor_runtime.log` and chat logs reset on redeploy. AutoRelog + the wrapper script make sure the bot itself stays online.

---

## 📁 Layout

```
mccbot/
├── MinecraftClient            # MCC v26.1 executable (downloaded locally / in Docker build)
├── download_mcc.sh            # Pinned MCC binary downloader for local use and Docker
├── install_mcc.sh             # One-shot installer wrapper
├── MCC_HN_PRO_MAX.ini         # Main MCC configuration (Aternos server, AntiAFK off, AutoRelog on, FollowPlayer on)
├── mcc_supervisor.py          # Production supervisor (this brain)
├── run_forever.sh             # Wrapper that restarts supervisor forever
├── start_bot.sh               # Convenience launcher
├── owners.txt                 # Owner whitelist
├── Dockerfile                 # Railway / Docker image definition
├── railway.json               # Railway build + deploy config
├── .env.example               # Environment variable template
├── .dockerignore
├── .gitignore
├── README.md
└── ...                        # MCC sample files & .cs scripts for reference
```

---

## 🔍 Stability notes & honest caveats
- `AutoAttack` chat bot is **disabled by default** because the server previously rejected interact packets with `DecoderException: Failed to decode packet 'serverbound/minecraft:interact'`. The supervisor implements safer guard logic via `protect me` + sword equip.
- Smart armor / shield offhand automation is *not* implemented yet (MCC inventory APIs make it possible but it's out of the v2 scope).
- Lumberjack mode is a best-effort high-level state machine. Pathfinding through dense terrain or unloaded chunks can stall and the bot will time-out back to patrol mode.

---

## 🪪 Server info
- Host: `HN25462.aternos.me`
- Port: `26624`
- Game version: Paper 26.1.2 (Java protocol 775)
- Auth: cracked / offline (no Microsoft login)
