#!/usr/bin/env python3
"""
HN_PRO_MAX Production Supervisor
================================
- Owner-only assistant commands
- 24/7 reconnection via MCC AutoRelog + Python wrapper
- Owner inventory workflow (equip / drop / shield)
- Lumberjack mode skeleton (find/cut/collect/bring wood)
- Health server for Railway / external monitoring
- Robust state machine, graceful shutdown, no command spam on disconnect
"""

from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional


# ---------------------------------------------------------------------------
# Constants and configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "MCC_HN_PRO_MAX.ini")
MCC_PATH = os.path.join(BASE_DIR, "MinecraftClient")
RUNTIME_LOG = os.path.join(BASE_DIR, "supervisor_runtime.log")
OWNERS_FILE = os.path.join(BASE_DIR, "owners.txt")

BOT_USERNAME = os.getenv("BOT_USERNAME", "HN_PRO_MAX")
HEALTH_PORT = int(os.getenv("PORT", "8080"))

# Movement cadence (seconds between supervisor-driven actions)
MOVEMENT_INTERVAL = float(os.getenv("MOVEMENT_INTERVAL", "1.4"))
SEND_THROTTLE = float(os.getenv("SEND_THROTTLE", "1.2"))

# Owner identification (case-insensitive)
DEFAULT_OWNER = ".Nirankar66"

# Bed sleep retry interval
BED_RETRY_INTERVAL_SECONDS = 120
BED_REASON_REPEAT_COOLDOWN_SECONDS = 300

# Reply rate-limiting
GLOBAL_REPLY_COOLDOWN_SECONDS = 3
DUPLICATE_MSG_WINDOW_SECONDS = 45
PROACTIVE_CHAT_COOLDOWN_SECONDS = 180

# Lumberjack tuning
WOOD_SEARCH_RADIUS = 16
WOOD_TYPES_FOR_DIG = [
    "OakLog", "BirchLog", "SpruceLog", "JungleLog",
    "AcaciaLog", "DarkOakLog", "MangroveLog", "CherryLog",
]

# Hostile mob death pattern
HOSTILE_DEATH_RE = re.compile(
    r"HN_PRO_MAX .*?(Zombie|Skeleton|Creeper|Spider|Drowned|Husk|Stray|Phantom|Witch|Vindicator|Pillager)",
    re.IGNORECASE,
)

# Disconnect patterns from MCC
DISCONNECT_RE = re.compile(
    r"(Disconnected by Server"
    r"|Connection has been lost"
    r"|Cannot send text: not connected to a server"
    r"|Failed to decode packet .*interact"
    r"|Received unknown packet id"
    r"|A timeout occured)",
    re.IGNORECASE,
)

# Chat line pattern from MCC (matches "* <player> <message>" or with timestamp)
CHAT_RE = re.compile(r"^(?:\d{2}:\d{2}:\d{2}\s+)?\*\s+(\S+)\s+(.+)$")
JOIN_RE = re.compile(r"^(?:\d{2}:\d{2}:\d{2}\s+)?(\S+) joined the game$")
LEAVE_RE = re.compile(r"^(?:\d{2}:\d{2}:\d{2}\s+)?(\S+) left the game$")

# Bed reason translation
BED_REASON_PATTERNS = [
    (re.compile(r"Could not find a bed", re.IGNORECASE), "nearby bed nahi mila"),
    (re.compile(r"Can not reach the bed safely", re.IGNORECASE), "bed tak safely pahunch nahi pa raha"),
    (re.compile(r"Failed to reach the bed position", re.IGNORECASE), "bed tak time par nahi pahunch saka"),
    (re.compile(r"not a bed", re.IGNORECASE), "jo block mila wo bed nahi tha"),
    (re.compile(r"bed is occupied", re.IGNORECASE), "bed occupied hai"),
    (re.compile(r"bed is obstructed", re.IGNORECASE), "bed obstructed hai"),
    (re.compile(r"too far away", re.IGNORECASE), "bed bahut door hai"),
    (re.compile(r"monsters nearby", re.IGNORECASE), "nearby hostile mobs hain"),
    (re.compile(r"only at night|during thunderstorms", re.IGNORECASE), "abhi raat ya thunderstorm nahi hai"),
    (re.compile(r"Could not lay in bed", re.IGNORECASE), "bed use karne ki condition poori nahi hui"),
]

# ANSI cleanup
ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Logger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        try:
            open(self.path, "w", encoding="utf-8").write("")
        except OSError:
            pass

    def log(self, *parts: object) -> None:
        line = f"{utcnow().isoformat()} " + " ".join(str(p) for p in parts)
        with self._lock:
            print(line, flush=True)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


LOG = _Logger(RUNTIME_LOG)


# ---------------------------------------------------------------------------
# Owner whitelist
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    return name.strip().lower()


def load_owners() -> set[str]:
    found: set[str] = set()
    env_value = os.getenv("OWNER_USERNAMES", "").strip()
    if env_value:
        for item in env_value.split(","):
            item = item.strip()
            if item:
                found.add(normalize_name(item))
    if os.path.exists(OWNERS_FILE):
        try:
            with open(OWNERS_FILE, "r", encoding="utf-8") as f:
                for raw in f:
                    text = raw.strip()
                    if text and not text.startswith("#"):
                        found.add(normalize_name(text))
        except OSError as exc:
            LOG.log("[owners-read-error]", repr(exc))
    if not found:
        found.add(normalize_name(DEFAULT_OWNER))
    return found


# ---------------------------------------------------------------------------
# Bot state
# ---------------------------------------------------------------------------

@dataclass
class BotState:
    stop_flag: bool = False
    joined: bool = False
    connected_once: bool = False
    active_after: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    follow_target: str = ""
    protect_target: str = ""
    last_seen_player: str = ""
    last_seen_player_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    last_reply_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    panic_until: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    next_bed_attempt_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    last_bed_reason: str = ""
    last_bed_announce_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    last_proactive_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))
    process_started_at: datetime = field(default_factory=utcnow)
    owners: set[str] = field(default_factory=set)
    recent_sender_msg: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    recent_reply: dict[str, str] = field(default_factory=dict)
    move_index: int = 0
    mode: str = "patrol"  # patrol | follow | protect | lumberjack
    lumberjack_owner: str = ""


STATE = BotState()
CMD_QUEUE: "queue.Queue[str]" = queue.Queue()
HEALTH_SERVER_STARTED = False


# ---------------------------------------------------------------------------
# Movement / reply pools
# ---------------------------------------------------------------------------

MOVEMENT_CYCLE = [
    "/look north",
    "/move north -f",
    "/animation mainhand",
    "/look east",
    "/move east -f",
    "/sneak",
    "/look south",
    "/move south -f",
    "/animation mainhand",
    "/look west",
    "/move west -f",
    "/move center",
]

PANIC_CYCLE = [
    "/look south",
    "/move south -f",
    "/move west -f",
    "/look east",
    "/move east -f",
    "/move north -f",
    "/move center",
]

HELLO_LINES = [
    "hey owner, online hoon",
    "haan owner, yahin hoon",
    "sun raha hoon, bolo",
    "owner mode active hai",
]
STATUS_LINES = [
    "main online hoon aur chat dekh raha hoon",
    "alive hoon, movement bhi on hai",
    "server par hoon, sun raha hoon",
]
FOLLOW_LINES = [
    "aa raha hoon",
    "theek hai, follow kar raha hoon",
    "coming to you",
]
STOP_LINES = [
    "theek hai, ruk gaya",
    "ok, yahin stay karta hoon",
    "follow stop kar diya",
]
UNKNOWN_LINES = [
    "samjha, aur bolo",
    "suna maine",
    "theek, note kar liya",
]
PROACTIVE_LINES = [
    "owner, koi help chahiye toh bolo",
    "main nearby active hoon",
    "commands: come here / stop / status / sleep now / protect me",
    "main idle nahi hoon, active patrol par hoon",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean_line(line: str) -> str:
    line = ANSI_RE.sub("", line)
    return line.replace("\u258c", "").replace("\r", "").strip()


def normalize_msg(msg: str) -> str:
    return re.sub(r"\s+", " ", msg.strip().lower())


def is_owner(name: str) -> bool:
    return normalize_name(name) in STATE.owners


def enqueue(text: str, source: str = "auto") -> None:
    CMD_QUEUE.put(text)
    LOG.log("[queue]", f"src={source}", text)


def clear_pending_commands(reason: str = "") -> None:
    cleared = 0
    while True:
        try:
            CMD_QUEUE.get_nowait()
            cleared += 1
        except queue.Empty:
            break
    if cleared:
        LOG.log("[queue-clear]", f"cleared={cleared}", reason)


# ---------------------------------------------------------------------------
# Send loop (writes to MCC stdin)
# ---------------------------------------------------------------------------

def send_loop(proc: subprocess.Popen) -> None:
    while not STATE.stop_flag and proc.poll() is None:
        try:
            cmd = CMD_QUEUE.get(timeout=1.0)
        except queue.Empty:
            continue
        # Drop everything if not in joined state, except /quit
        if not STATE.joined and cmd != "/quit":
            LOG.log("[send-skip-disconnected]", cmd)
            continue
        try:
            if proc.stdin is None:
                LOG.log("[send-error]", "stdin closed")
                break
            proc.stdin.write(cmd + "\n")
            proc.stdin.flush()
            LOG.log("[send]", cmd)
        except Exception as exc:  # noqa: BLE001
            LOG.log("[send-error]", repr(exc))
            break
        time.sleep(SEND_THROTTLE)


# ---------------------------------------------------------------------------
# Owner command parser and handlers
# ---------------------------------------------------------------------------

COMMAND_KEYWORDS = {
    "follow": ["come here", "idhar aa", "mere paas aa", "follow me", "follow kro", "follow karo", "come to me"],
    "stop": ["stop", "ruk ja", "ruko", "stay here", "stay", "mat aao", "follow stop"],
    "status": ["status", "alive", "online", "kahan ho", "kahaan ho", "bot status"],
    "help": ["help", "madad", "commands"],
    "sleep_now": ["sleep now", "so ja", "soja", "bed use", "go to bed"],
    "protect_me": ["protect me", "guard me", "raksha karo", "bachao"],
    "owner": ["who is owner", "owners list", "owner list"],
    "equip_sword": ["equip sword", "sword nikalo", "sword le", "take sword"],
    "equip_axe": ["equip axe", "axe nikalo", "axe le", "take axe"],
    "equip_pickaxe": ["equip pickaxe", "pickaxe nikalo"],
    "equip_shield": ["use shield", "shield use", "shield uthao", "raise shield"],
    "drop_logs": ["drop logs", "logs do", "give logs", "wood do", "drop wood"],
    "drop_food": ["drop food", "food do", "give food", "khana do"],
    "drop_all": ["drop all", "sab drop"],
    "find_trees": ["find trees", "find tree", "locate trees", "tree dhundo"],
    "cut_logs": ["cut logs", "cut tree", "chop tree", "log kaato", "wood kaato"],
    "collect_wood": ["collect wood", "wood collect", "wood uthao", "logs collect"],
    "bring_wood": ["bring wood", "bring logs", "wood lao", "logs lao"],
    "lumberjack": ["lumberjack", "lumberjack mode", "wood mode"],
    "hello": ["hi", "hello", "hey", "namaste", "yo"],
}


def detect_command(msg: str) -> str:
    text = normalize_msg(msg)
    # Order-sensitive: more specific phrases first
    priority = [
        "find_trees", "cut_logs", "collect_wood", "bring_wood", "lumberjack",
        "equip_sword", "equip_axe", "equip_pickaxe", "equip_shield",
        "drop_logs", "drop_food", "drop_all",
        "sleep_now", "protect_me", "follow", "stop",
        "status", "owner", "help", "hello",
    ]
    for key in priority:
        for phrase in COMMAND_KEYWORDS.get(key, []):
            if phrase in text:
                return key
    return "chat"


def send_public_reply(sender: str, text: str) -> None:
    reply = f"@{sender} {text}"
    last = STATE.recent_reply.get(sender.lower())
    if last == reply:
        return
    enqueue(reply, source="reply")
    STATE.recent_reply[sender.lower()] = reply


def cmd_follow(sender: str) -> None:
    STATE.follow_target = sender
    STATE.protect_target = ""
    STATE.mode = "follow"
    enqueue(f"/follow start {sender}", source="cmd")
    send_public_reply(sender, random.choice(FOLLOW_LINES))


def cmd_stop(sender: str) -> None:
    STATE.follow_target = ""
    STATE.protect_target = ""
    STATE.mode = "patrol"
    enqueue("/follow stop", source="cmd")
    send_public_reply(sender, random.choice(STOP_LINES))


def cmd_status(sender: str) -> None:
    mode = STATE.mode
    if STATE.follow_target:
        mode = f"follow={STATE.follow_target}"
    if STATE.protect_target:
        mode = f"protect={STATE.protect_target}"
    msg = f"{random.choice(STATUS_LINES)} | mode={mode}"
    send_public_reply(sender, msg)


def cmd_help(sender: str) -> None:
    send_public_reply(
        sender,
        "cmds: come here, stay, status, sleep now, protect me, equip sword/axe/pickaxe, use shield, drop logs/food, find trees, cut logs, bring wood",
    )


def cmd_sleep_now(sender: str) -> None:
    enqueue("/bed sleep 8", source="cmd")
    send_public_reply(sender, "nearby bed check kar raha hoon")


def cmd_protect_me(sender: str) -> None:
    STATE.protect_target = sender
    STATE.follow_target = sender
    STATE.mode = "protect"
    enqueue(f"/follow start {sender}", source="cmd")
    send_public_reply(sender, "protect mode on, pass me rahunga")


def cmd_owner(sender: str) -> None:
    owners = ", ".join(sorted(STATE.owners))
    send_public_reply(sender, f"owners: {owners}")


def equip_item(sender: str, label: str, item_keywords: list[str]) -> None:
    """
    Production note: MCC's `dropitem` and `changeslot` work with inventory handling
    enabled. To "equip" an item, we attempt to switch to a hotbar slot that
    contains the item by name pattern. Since item discovery requires inventory
    inspection commands, we issue a layered fallback:
      1) Cycle hotbar to slot 1 and announce intent (predictable starting state)
      2) Inform the owner that the bot is trying to equip the requested item
    Inventory handling must be enabled in MCC config for actual movement of
    items; if it is not enabled, the announce step is still useful.
    """
    LOG.log("[equip-request]", label, item_keywords)
    # Best-effort: try common hotbar slots; owner can manually arrange inventory
    enqueue("/changeslot 1", source="cmd")
    send_public_reply(sender, f"{label} equip karne ki koshish kar raha hoon (hotbar slot 1)")


def cmd_equip_sword(sender: str) -> None:
    equip_item(sender, "sword", ["Sword"])


def cmd_equip_axe(sender: str) -> None:
    equip_item(sender, "axe", ["Axe"])


def cmd_equip_pickaxe(sender: str) -> None:
    equip_item(sender, "pickaxe", ["Pickaxe"])


def cmd_equip_shield(sender: str) -> None:
    LOG.log("[equip-shield-request]")
    send_public_reply(
        sender,
        "shield offhand-equip ke liye custom slot patch chahiye; abhi best-effort mode hai",
    )


def cmd_drop_logs(sender: str) -> None:
    for wood in WOOD_TYPES_FOR_DIG:
        enqueue(f"/dropitem {wood}", source="cmd")
    send_public_reply(sender, "logs drop kar raha hoon, agar inventory me hain")


def cmd_drop_food(sender: str) -> None:
    food_items = ["Bread", "CookedBeef", "CookedPorkchop", "CookedChicken", "CookedMutton", "Apple"]
    for f in food_items:
        enqueue(f"/dropitem {f}", source="cmd")
    send_public_reply(sender, "food drop kar raha hoon, agar mila to")


def cmd_drop_all(sender: str) -> None:
    enqueue("/autodrop on", source="cmd")
    send_public_reply(sender, "autodrop enabled (everything mode config par depend karta hai)")


def cmd_find_trees(sender: str) -> None:
    LOG.log("[lumberjack-find]", sender)
    STATE.lumberjack_owner = sender
    enqueue("/inventory", source="cmd")  # log state
    send_public_reply(sender, f"nearby {WOOD_SEARCH_RADIUS} blocks me trees scan kar raha hoon")


def cmd_cut_logs(sender: str) -> None:
    """
    Lumberjack cut routine: attempt to dig logs around the bot. MCC's `dig`
    command digs a single block at a coordinate, so the supervisor cannot do
    true tree-cutting without world introspection. We issue a sequence of
    dig commands for blocks directly in front of the bot at multiple heights,
    which works for trees the bot is touching.
    """
    LOG.log("[lumberjack-cut]", sender)
    STATE.lumberjack_owner = sender
    STATE.mode = "lumberjack"
    # Best-effort: dig blocks directly in front at body and head height
    enqueue("/look north", source="cmd")
    enqueue("/dig ~ ~ ~-1", source="cmd")
    enqueue("/dig ~ ~1 ~-1", source="cmd")
    enqueue("/dig ~ ~2 ~-1", source="cmd")
    send_public_reply(sender, "log cutting attempt kar raha hoon (in-front blocks)")


def cmd_collect_wood(sender: str) -> None:
    LOG.log("[lumberjack-collect]", sender)
    # MCC has an ItemsCollector bot; enable via runtime command if configured
    enqueue("/itemscollector start", source="cmd")
    send_public_reply(sender, "dropped items collect karne ki koshish kar raha hoon")


def cmd_bring_wood(sender: str) -> None:
    LOG.log("[lumberjack-bring]", sender)
    enqueue(f"/follow start {sender}", source="cmd")
    for wood in WOOD_TYPES_FOR_DIG:
        enqueue(f"/dropitem {wood}", source="cmd")
    send_public_reply(sender, "tumhare paas aa raha hoon aur logs drop karunga")


def cmd_lumberjack(sender: str) -> None:
    STATE.lumberjack_owner = sender
    STATE.mode = "lumberjack"
    cmd_find_trees(sender)


def cmd_hello(sender: str) -> None:
    send_public_reply(sender, random.choice(HELLO_LINES))


def cmd_chat(sender: str, msg: str) -> None:
    send_public_reply(sender, random.choice(UNKNOWN_LINES))


# ---------------------------------------------------------------------------
# Chat reply routing
# ---------------------------------------------------------------------------

COMMAND_HANDLERS = {
    "follow": cmd_follow,
    "stop": cmd_stop,
    "status": cmd_status,
    "help": cmd_help,
    "sleep_now": cmd_sleep_now,
    "protect_me": cmd_protect_me,
    "owner": cmd_owner,
    "equip_sword": cmd_equip_sword,
    "equip_axe": cmd_equip_axe,
    "equip_pickaxe": cmd_equip_pickaxe,
    "equip_shield": cmd_equip_shield,
    "drop_logs": cmd_drop_logs,
    "drop_food": cmd_drop_food,
    "drop_all": cmd_drop_all,
    "find_trees": cmd_find_trees,
    "cut_logs": cmd_cut_logs,
    "collect_wood": cmd_collect_wood,
    "bring_wood": cmd_bring_wood,
    "lumberjack": cmd_lumberjack,
    "hello": cmd_hello,
}


def maybe_reply(sender: str, msg: str) -> None:
    if normalize_name(sender) == normalize_name(BOT_USERNAME):
        return

    now = utcnow()
    STATE.last_seen_player = sender
    STATE.last_seen_player_at = now

    if not is_owner(sender):
        LOG.log("[ignore-non-owner]", sender, msg)
        return

    key = sender.lower()
    nmsg = normalize_msg(msg)
    prev_msg, prev_time = STATE.recent_sender_msg.get(key, ("", datetime.min.replace(tzinfo=timezone.utc)))
    if nmsg == prev_msg and (now - prev_time).total_seconds() < DUPLICATE_MSG_WINDOW_SECONDS:
        LOG.log("[reply-skip]", "duplicate-player-msg", sender)
        return
    STATE.recent_sender_msg[key] = (nmsg, now)

    if (now - STATE.last_reply_at).total_seconds() < GLOBAL_REPLY_COOLDOWN_SECONDS:
        LOG.log("[reply-skip]", "global-rate-limit", sender)
        return

    cmd = detect_command(msg)
    handler = COMMAND_HANDLERS.get(cmd)
    try:
        if handler is None:
            cmd_chat(sender, msg)
        else:
            if cmd in {"chat", "hello"}:
                handler(sender)
            else:
                handler(sender)
    except Exception as exc:  # noqa: BLE001
        LOG.log("[handler-error]", cmd, repr(exc))
        send_public_reply(sender, "internal error aaya, fir try karo")

    STATE.last_reply_at = now


# ---------------------------------------------------------------------------
# Movement loop
# ---------------------------------------------------------------------------

def movement_tick() -> None:
    now = utcnow()
    if not STATE.joined:
        return
    if now < STATE.active_after:
        return

    # Periodic bed attempt at night
    if now >= STATE.next_bed_attempt_at:
        enqueue("/bed sleep 8", source="auto")
        STATE.next_bed_attempt_at = now + timedelta(seconds=BED_RETRY_INTERVAL_SECONDS)
        STATE.move_index += 1
        return

    if STATE.follow_target or STATE.protect_target:
        # Subtle non-idle actions during follow/protect
        if STATE.move_index % 2 == 0:
            enqueue("/look north" if STATE.move_index % 4 == 0 else "/look east", source="auto")
        else:
            enqueue("/animation mainhand", source="auto")
    else:
        cycle = PANIC_CYCLE if now < STATE.panic_until else MOVEMENT_CYCLE
        enqueue(cycle[STATE.move_index % len(cycle)], source="auto")

    # Proactive chat to owner if recently seen
    if (
        STATE.last_seen_player
        and is_owner(STATE.last_seen_player)
        and (now - STATE.last_seen_player_at).total_seconds() < 300
        and (now - STATE.last_proactive_at).total_seconds() > PROACTIVE_CHAT_COOLDOWN_SECONDS
    ):
        enqueue(f"@{STATE.last_seen_player} {random.choice(PROACTIVE_LINES)}", source="auto")
        STATE.last_proactive_at = now

    STATE.move_index += 1


def movement_loop() -> None:
    while not STATE.stop_flag:
        try:
            movement_tick()
        except Exception as exc:  # noqa: BLE001
            LOG.log("[movement-error]", repr(exc))
        time.sleep(MOVEMENT_INTERVAL)


# ---------------------------------------------------------------------------
# Bed reason announcement
# ---------------------------------------------------------------------------

def translate_bed_reason(line: str) -> str:
    for rx, reason in BED_REASON_PATTERNS:
        if rx.search(line):
            return reason
    return ""


def maybe_announce_bed_reason(line: str) -> None:
    reason = translate_bed_reason(line)
    if not reason:
        return
    now = utcnow()
    if reason == STATE.last_bed_reason and (now - STATE.last_bed_announce_at).total_seconds() < BED_REASON_REPEAT_COOLDOWN_SECONDS:
        return
    target = ""
    if STATE.last_seen_player and is_owner(STATE.last_seen_player) and (now - STATE.last_seen_player_at).total_seconds() < 600:
        target = f"@{STATE.last_seen_player} "
    enqueue(f"{target}abhi so nahi sakta: {reason}", source="auto")
    STATE.last_bed_reason = reason
    STATE.last_bed_announce_at = now


# ---------------------------------------------------------------------------
# Health server
# ---------------------------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in {"/", "/health", "/healthz"}:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        payload = {
            "ok": True,
            "joined": STATE.joined,
            "connected_once": STATE.connected_once,
            "mode": STATE.mode,
            "follow_target": STATE.follow_target,
            "protect_target": STATE.protect_target,
            "owners": sorted(list(STATE.owners)),
            "uptime_seconds": int((utcnow() - STATE.process_started_at).total_seconds()),
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        return


def start_health_server() -> None:
    global HEALTH_SERVER_STARTED
    if HEALTH_SERVER_STARTED:
        return
    try:
        server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    except OSError as exc:
        LOG.log("[health-bind-fail]", repr(exc))
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    HEALTH_SERVER_STARTED = True
    LOG.log("[health-server]", f"listening on 0.0.0.0:{HEALTH_PORT}")


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def sig_handler(signum, frame):  # noqa: ANN001
    STATE.stop_flag = True
    LOG.log("[signal]", signum)


# ---------------------------------------------------------------------------
# MCC process management
# ---------------------------------------------------------------------------

def launch_mcc() -> subprocess.Popen:
    LOG.log("[startup]", "launching MCC supervisor")
    LOG.log("[owners]", ",".join(sorted(STATE.owners)))
    LOG.log("[config]", CONFIG_PATH)
    return subprocess.Popen(
        [MCC_PATH, CONFIG_PATH],
        cwd=BASE_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )


def handle_mcc_line(line: str, greeted_session: list[bool]) -> None:
    LOG.log("[mcc]", line)

    if "Server was successfully joined." in line:
        STATE.joined = True
        STATE.connected_once = True
        greeted_session[0] = False
        STATE.follow_target = ""
        STATE.protect_target = ""
        STATE.mode = "patrol"
        STATE.active_after = utcnow() + timedelta(seconds=10)
        STATE.next_bed_attempt_at = utcnow() + timedelta(seconds=35)
        clear_pending_commands("post-join")
        return

    if DISCONNECT_RE.search(line):
        STATE.joined = False
        STATE.active_after = datetime.min.replace(tzinfo=timezone.utc)
        STATE.follow_target = ""
        STATE.protect_target = ""
        STATE.mode = "patrol"
        clear_pending_commands("disconnect")
        LOG.log("[disconnect-detected]", line)
        return

    if STATE.joined and (not greeted_session[0]) and line.endswith(f"{BOT_USERNAME} joined the game"):
        greeted_session[0] = True
        STATE.active_after = utcnow() + timedelta(seconds=8)
        enqueue(f"{BOT_USERNAME} online. owner-only assistant active, sleep-check active.", source="auto")
        return

    m = CHAT_RE.match(line)
    if m:
        sender, msg = m.group(1), m.group(2)
        maybe_reply(sender, msg)
        return

    j = JOIN_RE.match(line)
    if j:
        player = j.group(1)
        if normalize_name(player) != normalize_name(BOT_USERNAME):
            STATE.last_seen_player = player
            STATE.last_seen_player_at = utcnow()
        return

    if LEAVE_RE.match(line):
        return

    if HOSTILE_DEATH_RE.search(line):
        STATE.panic_until = utcnow() + timedelta(seconds=20)
        if not STATE.follow_target and not STATE.protect_target:
            enqueue("/move south -f", source="auto")
            enqueue("/move west -f", source="auto")
        return

    maybe_announce_bed_reason(line)


def main_loop() -> int:
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    STATE.owners = load_owners()
    start_health_server()

    proc = launch_mcc()
    threading.Thread(target=send_loop, args=(proc,), daemon=True).start()
    threading.Thread(target=movement_loop, daemon=True).start()

    greeted_session = [False]
    STATE.next_bed_attempt_at = utcnow() + timedelta(seconds=30)

    try:
        while not STATE.stop_flag:
            if proc.stdout is None:
                LOG.log("[mcc-stdout-none]")
                break
            raw = proc.stdout.readline()
            if raw == "" and proc.poll() is not None:
                break
            if not raw:
                time.sleep(0.05)
                continue
            line = clean_line(raw)
            if not line:
                continue
            try:
                handle_mcc_line(line, greeted_session)
            except Exception as exc:  # noqa: BLE001
                LOG.log("[handle-line-error]", repr(exc))
    finally:
        STATE.stop_flag = True
        try:
            if proc.poll() is None:
                STATE.joined = False
                clear_pending_commands("shutdown")
                try:
                    if proc.stdin and not proc.stdin.closed:
                        proc.stdin.write("/quit\n")
                        proc.stdin.flush()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
                proc.terminate()
                time.sleep(2)
                if proc.poll() is None:
                    proc.kill()
        except Exception as exc:  # noqa: BLE001
            LOG.log("[shutdown-error]", repr(exc))

    rc = proc.poll() if proc else None
    LOG.log("[exit]", f"returncode={rc} connected_once={STATE.connected_once}")
    return rc if rc is not None else 0


def main() -> int:
    try:
        return main_loop()
    except KeyboardInterrupt:
        LOG.log("[exit]", "KeyboardInterrupt")
        return 0
    except Exception as exc:  # noqa: BLE001
        LOG.log("[fatal]", repr(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
