# HN_PRO_MAX — Minecraft Owner-Only Assistant Bot

Production-grade [Minecraft Console Client](https://github.com/MCCTeam/Minecraft-Console-Client) based bot for Java Edition (Paper 26.1.2 / protocol 775). Designed to live on a cracked Aternos server and act as an **owner-only personal assistant** with chat replies, follow / protect / sleep behaviors, inventory equip / drop helpers, and a lumberjack mode skeleton.

> **Target server:** `HN25462.aternos.me:26624` (cracked / offline mode).
> **Bot username:** `HN_PRO_MAX`.
> **Hosting:** Railway (Docker) — also runs locally on any Linux x64 with Python 3.10+.

---

## Features

### Owner-only assistant
Only usernames listed in `owners.txt` or the `OWNER_USERNAMES` env var can send commands. Other players' chat is observed but never triggers actions.

### Chat-driven commands

| Intent | Example trigger phrases |
|---|---|
| Come here | `come here`, `follow me`, `idhar aa`, `mere paas aa` |
| Stop | `stop`, `stay`, `ruk ja`, `follow stop` |
| Status | `status`, `online`, `kahan ho` |
| Sleep now | `sleep now`, `so ja`, `bed use` |
| Protect me | `protect me`, `guard me`, `bachao` |
| Equip sword | `equip sword`, `sword nikalo` |
| Equip axe | `equip axe`, `axe le` |
| Equip pickaxe | `equip pickaxe` |
| Use shield | `use shield`, `shield uthao` |
| Drop logs | `drop logs`, `wood do`, `logs do` |
| Drop food | `drop food`, `food do`, `khana do` |
| Drop all | `drop all`, `sab drop` |
| Find trees | `find trees`, `tree dhundo` |
| Cut logs | `cut logs`, `chop tree`, `log kaato` |
| Collect wood | `collect wood`, `logs collect` |
| Bring wood | `bring wood`, `logs lao` |
| Lumberjack | `lumberjack`, `lumberjack mode` |
| Help | `help`, `madad`, `commands` |
| Owners | `owners list`, `who is owner` |

### Activity behavior
- ~1.4s movement cadence after a short post-join warmup, never idle.
- Periodic `/bed sleep 8` checks; if sleep fails, announces the reason (bed not found, occupied, obstructed, daytime, hostiles nearby, etc.) in chat.
- Proactive chat to the owner roughly every 3 minutes if the owner was recently seen.
- Hostile-death triggers a short panic-movement window.

### 24/7 reconnection (production grade)
- **Layer 1 — MCC AutoRelog** with unlimited retries inside the Minecraft client.
- **Layer 2 — Python `run_forever.sh`** wrapper restarts the supervisor on crash with exponential backoff (5s → 60s).
- **Layer 3 — Railway** runs the container with `restartPolicyType=ALWAYS` so the whole container is restarted if it dies.
- **Health endpoint** `/health` (JSON) for Railway / external monitoring.

### Lumberjack mode (skeleton)
- `find trees` — scans nearby blocks (best-effort, MCC inventory + terrain handling required).
- `cut logs` — sends dig commands to blocks directly in front of the bot at body and head height.
- `collect wood` — enables MCC's items collector bot.
- `bring wood` — follows the owner and drops log items.

> Full forest-AI lumberjack (radius scan → pathfind → multi-tree chop → return) requires a custom block-aware extension. The current build provides the safe scaffold.

### Inventory equip helpers
- `equip sword|axe|pickaxe` — best-effort hotbar slot switch.
- `drop logs|food|all` — drops items by type using MCC's `dropitem` command.
- `use shield` — informs the owner that offhand-equip requires a custom slot patch (not in this build).

> MCC's inventory handling must be **enabled** in `MCC_HN_PRO_MAX.ini` for any of these to actually move items.

---

## File layout

```
.
├── mcc_supervisor.py          # Production supervisor (this is the brain)
├── MCC_HN_PRO_MAX.ini         # MCC main config
├── owners.txt                 # Owner whitelist
├── download_mcc.sh            # Downloads MCC binary at container build time
├── run_forever.sh             # 24/7 wrapper with exponential backoff
├── start_bot.sh               # Local quick-start
├── _tests_command.py          # Unit tests for the command parser
├── Dockerfile                 # Production container
├── railway.json               # Railway deploy config
├── .dockerignore
├── .gitignore
├── .env.example               # All env var documentation
├── README.md
├── RAILWAY_START_GUIDE.txt
├── sample-matches.ini         # MCC auto-respond sample
└── LICENSE
```

The MCC binary itself (~85 MB) is **not committed** to git — it's downloaded by `download_mcc.sh` during Docker build.

---

## Local quick start

```bash
# First time: fetch the MCC binary
./download_mcc.sh

# Run the bot
chmod +x run_forever.sh start_bot.sh
./start_bot.sh

# Watch live logs
tail -f supervisor_runtime.log

# Check health
curl http://127.0.0.1:8080/health
```

To stop:

```bash
pkill -f mcc_supervisor.py
```

To run the command-parser unit tests:

```bash
python3 _tests_command.py
```

---

## Railway deployment

1. Push this repo to GitHub.
2. In [Railway](https://railway.com), create a new project → **Deploy from GitHub repo**.
3. Railway detects the root `Dockerfile` and builds automatically.
4. Set these environment variables in the Railway service:
   - `OWNER_USERNAMES` → comma-separated in-game owners (case-insensitive)
   - `BOT_USERNAME` → defaults to `HN_PRO_MAX`
   - `PORT` → auto-set by Railway, default `8080`
5. The healthcheck path is `/health`, configured via `railway.json`.
6. Deploy. The container runs `run_forever.sh`, which auto-restarts the supervisor on crash.

> Railway storage is ephemeral; logs reset on redeploy. Use Railway's log viewer or pipe logs to an external sink for long-term retention.

CLI alternative:

```bash
npm i -g @railway/cli       # or use the install script
railway login
railway init                # link / create project
railway up                  # upload current directory
railway logs --tail
railway variables set OWNER_USERNAMES=.Nirankar66
```

See: <https://docs.railway.com/cli>

---

## Health endpoint

`GET /health` returns:

```json
{
  "ok": true,
  "joined": true,
  "connected_once": true,
  "mode": "patrol",
  "follow_target": "",
  "protect_target": "",
  "owners": [".nirankar66"],
  "uptime_seconds": 1234
}
```

---

## Security notes

- The bot account uses **cracked/offline auth** and has no Mojang credentials.
- Owner whitelist is enforced at supervisor level, **not** server side. If the server is compromised or admins can spoof chat (`/nick`, `/tellraw`), owner identity could be spoofed too.
- Treat `owners.txt` as a configuration secret — anyone added there can fully control the bot.

---

## Known limitations

- Tree-cutting is a best-effort `/dig` sequence, not full pathfind + scan.
- Offhand / armor auto-equip is not implemented (MCC primitives are available, custom code required).
- `dropitem` requires MCC inventory handling to be enabled in the INI.
- `AutoAttack` is **disabled** in this profile because earlier versions of this server rejected interact packets and caused disconnect loops.

---

## License

This project is licensed under the terms of the repository `LICENSE` file. Built on top of the [Minecraft Console Client](https://github.com/MCCTeam/Minecraft-Console-Client) (CDDL-1.0).
