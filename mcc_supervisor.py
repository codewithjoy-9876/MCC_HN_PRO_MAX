#!/usr/bin/env python3
"""
HN_PRO_MAX MCC Supervisor (Production v2)
- Owner-only assistant
- Inventory workflow (equip/drop)
- Lumberjack / wood gathering
- 24/7 auto-reconnect
- Health endpoint for Railway

Built around the Minecraft Console Client (MCC) v26.1 CLI commands.
Reference docs:
  https://mccteam.github.io/guide/usage.html
  https://mccteam.github.io/guide/chat-bots.html
  https://github.com/MCCTeam/Minecraft-Console-Client
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Iterable

# =============================================================================
# Paths and constants
# =============================================================================
BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, 'MCC_HN_PRO_MAX.ini')
MCC = os.path.join(BASE, 'MinecraftClient')
LOG = os.path.join(BASE, 'supervisor_runtime.log')
OWNERS_FILE = os.path.join(BASE, 'owners.txt')

# Regex patterns for parsing MCC stdout
ANSI_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
CHAT_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?\*\s+(\S+)\s+(.+)$')
WHISPER_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?(\S+)\s+whispers to you:\s+(.+)$', re.I)
JOIN_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?([^\s]+) joined the game$')
LEAVE_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?([^\s]+) left the game$')
DISCONNECT_RE = re.compile(
    r'(Disconnected by Server|Connection has been lost|Cannot send text: not connected to a server'
    r'|Failed to decode packet .*interact|Received unknown packet id|A timeout occured)',
    re.I,
)
HOSTILE_DEATH_RE = re.compile(
    r'HN_PRO_MAX .*?(Zombie|Skeleton|Creeper|Spider|Drowned|Husk|Stray|Phantom|Witch|Pillager|Vindicator)',
    re.I,
)
SERVER_JOIN_RE = re.compile(r'Server was successfully joined', re.I)
SELF_SPAWN_RE = re.compile(r'HN_PRO_MAX joined the game', re.I)
LOCATION_RE = re.compile(r'X:(-?\d+\.\d+)\s+Y:(-?\d+\.\d+)\s+Z:(-?\d+\.\d+)')

# Bed sleep failure -> human-friendly reason mapping
BED_REASON_PATTERNS = [
    (re.compile(r'Could not find a bed', re.I), 'nearby bed nahi mila'),
    (re.compile(r'Can not reach the bed safely', re.I), 'bed tak safely pahunch nahi pa raha'),
    (re.compile(r'Failed to reach the bed position', re.I), 'bed tak time par nahi pahunch saka'),
    (re.compile(r'not a bed', re.I), 'wo block bed nahi tha'),
    (re.compile(r'bed is occupied', re.I), 'bed occupied hai'),
    (re.compile(r'bed is obstructed', re.I), 'bed obstructed hai'),
    (re.compile(r'too far away', re.I), 'bed bahut door hai'),
    (re.compile(r'monsters nearby', re.I), 'nearby hostile mobs hain'),
    (re.compile(r'only at night|during thunderstorms', re.I), 'abhi raat ya thunderstorm nahi hai'),
    (re.compile(r'Could not lay in bed', re.I), 'bed use karne ki condition poori nahi hui'),
]

# Wood log block materials that MCC understands (Minecraft 1.21.x logs)
LOG_BLOCKS = [
    'OakLog', 'SpruceLog', 'BirchLog', 'JungleLog', 'AcaciaLog', 'DarkOakLog',
    'MangroveLog', 'CherryLog', 'PaleOakLog', 'CrimsonStem', 'WarpedStem',
]

# =============================================================================
# Global runtime state
# =============================================================================
stop_flag = False
joined = False
connected_once = False
process_started_at = datetime.now(timezone.utc)
active_after = datetime.min.replace(tzinfo=timezone.utc)
panic_until = datetime.min.replace(tzinfo=timezone.utc)
next_bed_attempt_at = datetime.min.replace(tzinfo=timezone.utc)

follow_target = ''
protect_target = ''
mode = 'patrol'              # patrol | follow | protect | lumberjack | idle
lumberjack_state = 'idle'    # idle | searching | chopping | returning | dropping
lumberjack_owner = ''
trees_chopped_count = 0

last_seen_player = ''
last_seen_player_at = datetime.min.replace(tzinfo=timezone.utc)
last_reply_at = datetime.min.replace(tzinfo=timezone.utc)
last_proactive_at = datetime.min.replace(tzinfo=timezone.utc)
last_bed_reason = ''
last_bed_announce_at = datetime.min.replace(tzinfo=timezone.utc)
last_health_warning_at = datetime.min.replace(tzinfo=timezone.utc)
bot_location: tuple[float, float, float] | None = None

recent_sender_msg: dict[str, tuple[str, datetime]] = {}
recent_reply: dict[str, str] = {}
cmd_q: 'queue.Queue[str]' = queue.Queue()
move_index = 0
owners: set[str] = set()
STATE = SimpleNamespace(owners=owners)
health_server_started = False
process_handle: subprocess.Popen | None = None

# =============================================================================
# Movement and dialogue cycles
# =============================================================================
PATROL_CYCLE = [
    '/look north',
    '/move north -f',
    '/animation mainhand',
    '/look east',
    '/move east -f',
    '/sneak',
    '/look south',
    '/move south -f',
    '/animation mainhand',
    '/look west',
    '/move west -f',
    '/move center',
]

PANIC_CYCLE = [
    '/look south',
    '/move south -f',
    '/move west -f',
    '/look east',
    '/move east -f',
    '/move north -f',
    '/move center',
]

HELLO_LINES = [
    'hey owner, online hoon 👋',
    'haan owner, yahin hoon.',
    'sun raha hoon, bolo.',
    'owner mode active hai.',
]
STATUS_LINES = [
    'main online hoon aur chat dekh raha hoon.',
    'alive hoon, movement bhi on hai.',
    'server par hoon, sun raha hoon.',
]
FOLLOW_LINES = [
    'aa raha hoon.',
    'theek hai, follow kar raha hoon.',
    'coming to you.',
]
STOP_LINES = [
    'theek hai, ruk gaya.',
    'ok, yahin stay karta hoon.',
    'follow stop kar diya.',
]
UNKNOWN_LINES = [
    'samjha owner, aur bolo.',
    'suna maine.',
    'theek, note kar liya.',
]
PROACTIVE_LINES = [
    'owner, koi help chahiye toh bolo.',
    'main nearby active hoon.',
    'commands: come here / stay / status / sleep now / protect me / gather wood',
    'main idle nahi hoon, active patrol par hoon.',
]
PROTECT_LINES = [
    'protect mode active, nearby hostile par nazar hai.',
    'mai aapko cover kar raha hoon.',
    'guard mode on.',
]
EQUIP_SLOT_MAP = {
    'sword': 1,
    'axe': 2,
    'pickaxe': 3,
    'shovel': 4,
    'shield': 5,   # treated as offhand-ish; MCC will use slot 5
    'food': 6,
    'block': 7,
}

# =============================================================================
# Utility helpers
# =============================================================================
def now() -> datetime:
    return datetime.now(timezone.utc)


def log(*parts) -> None:
    line = f"{now().isoformat()} " + ' '.join(str(p) for p in parts)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def clean(line: str) -> str:
    line = ANSI_RE.sub('', line)
    return line.replace('\u258c', '').replace('\r', '').strip()


def normalize_player_name(name: str) -> str:
    return name.strip().lower()


def normalize_msg(msg: str) -> str:
    return re.sub(r'\s+', ' ', msg.strip().lower())


def load_owners() -> set[str]:
    loaded: set[str] = set()
    env_owners = os.getenv('OWNER_USERNAMES', '').strip()
    if env_owners:
        for part in env_owners.split(','):
            part = part.strip()
            if part:
                loaded.add(normalize_player_name(part))
    if os.path.exists(OWNERS_FILE):
        try:
            with open(OWNERS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        loaded.add(normalize_player_name(line))
        except Exception as e:
            log('[owners-load-error]', repr(e))
    if not loaded:
        loaded.add(normalize_player_name('.Nirankar66'))
    return loaded


def is_owner(name: str) -> bool:
    current_owners = getattr(STATE, 'owners', None) or owners
    return normalize_player_name(name) in current_owners


def enqueue(text: str) -> None:
    if not text:
        return
    cmd_q.put(text)
    log('[queue]', text)


def clear_pending_commands() -> None:
    cleared = 0
    while True:
        try:
            cmd_q.get_nowait()
            cleared += 1
        except queue.Empty:
            break
    if cleared:
        log('[queue-clear]', f'cleared={cleared}')


# =============================================================================
# IO loops
# =============================================================================
def send_loop(proc: subprocess.Popen) -> None:
    """Drain command queue into MCC stdin at a safe rate."""
    while not stop_flag and proc.poll() is None:
        try:
            cmd = cmd_q.get(timeout=1)
        except queue.Empty:
            continue
        if not joined and cmd != '/quit':
            log('[send-skip-disconnected]', cmd)
            continue
        try:
            assert proc.stdin is not None
            proc.stdin.write(cmd + '\n')
            proc.stdin.flush()
            log('[send]', cmd)
        except Exception as e:
            log('[send-error]', repr(e))
            break
        time.sleep(1.2)


def movement_loop() -> None:
    """Drive non-idle movement and periodic auto-actions."""
    global move_index, next_bed_attempt_at, last_proactive_at
    while not stop_flag:
        if joined:
            t = now()
            if t < active_after:
                time.sleep(1.0)
                continue

            # Periodic sleep check
            if t >= next_bed_attempt_at and mode in ('patrol', 'follow', 'protect'):
                enqueue('/bed sleep 8')
                next_bed_attempt_at = t + timedelta(seconds=180)

            # Decide cycle based on mode
            if mode == 'lumberjack':
                pass  # lumberjack loop drives its own movement
            elif mode == 'follow' and follow_target:
                if move_index % 2 == 0:
                    enqueue('/look north' if move_index % 4 == 0 else '/look east')
                else:
                    enqueue('/animation mainhand')
            elif mode == 'protect' and protect_target:
                if move_index % 3 == 0:
                    enqueue('/animation mainhand')
                elif move_index % 3 == 1:
                    enqueue('/look ' + random.choice(['north', 'east', 'south', 'west']))
                else:
                    enqueue('/sneak')
            else:
                cycle = PANIC_CYCLE if t < panic_until else PATROL_CYCLE
                enqueue(cycle[move_index % len(cycle)])

            # Periodic proactive chat
            if (
                last_seen_player
                and is_owner(last_seen_player)
                and (t - last_seen_player_at).total_seconds() < 300
                and (t - last_proactive_at).total_seconds() > 180
            ):
                enqueue(f'@{last_seen_player} {random.choice(PROACTIVE_LINES)}')
                last_proactive_at = t

            # Ask MCC to report current location
            if move_index % 8 == 0:
                enqueue('/move get')

            move_index += 1
        time.sleep(1.2)


def lumberjack_loop() -> None:
    """High-level state machine for wood gathering."""
    global lumberjack_state, trees_chopped_count
    while not stop_flag:
        if mode != 'lumberjack' or not joined:
            time.sleep(1.5)
            continue
        try:
            if lumberjack_state == 'idle':
                lumberjack_state = 'searching'
                enqueue('/changeslot 2')  # axe slot
                enqueue('/animation mainhand')
                log('[lumberjack]', 'state=searching, switched to axe slot 2')
                time.sleep(2.0)
            elif lumberjack_state == 'searching':
                for log_type in LOG_BLOCKS:
                    enqueue(f'/move <{log_type}> 16')
                time.sleep(8.0)
                lumberjack_state = 'chopping'
                log('[lumberjack]', 'state=chopping')
            elif lumberjack_state == 'chopping':
                for _ in range(6):
                    enqueue('/dig')
                    time.sleep(1.6)
                enqueue('/animation mainhand')
                trees_chopped_count += 1
                log('[lumberjack]', f'state=chopping done, trees_chopped={trees_chopped_count}')
                if trees_chopped_count >= 3:
                    lumberjack_state = 'returning'
                else:
                    lumberjack_state = 'searching'
            elif lumberjack_state == 'returning':
                if lumberjack_owner:
                    enqueue(f'/follow start {lumberjack_owner}')
                time.sleep(10.0)
                lumberjack_state = 'dropping'
                log('[lumberjack]', 'state=dropping')
            elif lumberjack_state == 'dropping':
                for log_type in LOG_BLOCKS:
                    enqueue(f'/dropitem {log_type}')
                enqueue('/follow stop')
                if lumberjack_owner:
                    enqueue(f'@{lumberjack_owner} wood deliver kar diya owner.')
                trees_chopped_count = 0
                lumberjack_state = 'idle'
                set_mode('patrol')
                log('[lumberjack]', 'cycle complete, reverted to patrol mode')
        except Exception as e:
            log('[lumberjack-error]', repr(e))
            lumberjack_state = 'idle'
            set_mode('patrol')
        time.sleep(1.5)


# =============================================================================
# Mode handling
# =============================================================================
def set_mode(new_mode: str) -> None:
    global mode
    if mode != new_mode:
        log('[mode-change]', f'{mode} -> {new_mode}')
    mode = new_mode


def reset_runtime_after_disconnect() -> None:
    global joined, active_after, follow_target, protect_target, lumberjack_state, trees_chopped_count
    joined = False
    active_after = datetime.min.replace(tzinfo=timezone.utc)
    follow_target = ''
    protect_target = ''
    lumberjack_state = 'idle'
    trees_chopped_count = 0
    clear_pending_commands()
    set_mode('patrol')


# =============================================================================
# Owner command intent parser
# =============================================================================
def command_from_message(msg: str) -> tuple[str, dict]:
    """Return (intent, params) for owner messages."""
    m = normalize_msg(msg)
    params: dict = {}

    if any(x in m for x in ['come here', 'idhar aa', 'mere paas aa', 'follow me', 'follow karo', 'come to me']):
        return 'follow', params
    if any(x in m for x in ['stay', 'stop', 'ruk', 'mat aao', 'rukja', 'ruko']):
        return 'stay', params
    if any(x in m for x in ['status', 'alive', 'online', 'kahan ho', 'kahaan ho', 'mode']):
        return 'status', params
    if 'sleep' in m and 'can' not in m:
        return 'sleep_now', params
    if any(x in m for x in ['protect me', 'guard me', 'cover me', 'protect karo', 'mera saath de']):
        return 'protect', params

    # Inventory / equipment
    for tool, slot in EQUIP_SLOT_MAP.items():
        if f'equip {tool}' in m or f'use {tool}' in m or f'{tool} equip' in m:
            params['tool'] = tool
            params['slot'] = slot
            return 'equip', params
    if 'use shield' in m or 'shield use' in m:
        params['tool'] = 'shield'
        params['slot'] = EQUIP_SLOT_MAP['shield']
        return 'equip', params

    # Drop intents
    if 'drop logs' in m or 'drop wood' in m or 'log de' in m or 'wood de' in m:
        params['kind'] = 'logs'
        return 'drop', params
    if 'drop food' in m or 'khana de' in m or 'food de' in m:
        params['kind'] = 'food'
        return 'drop', params
    if 'drop all' in m:
        params['kind'] = 'all'
        return 'drop', params
    if m.startswith('drop ') or 'drop item' in m:
        params['kind'] = 'item'
        return 'drop', params

    # Lumberjack
    if 'find trees' in m or 'find wood' in m or 'find forest' in m:
        return 'find_trees', params
    if 'cut logs' in m or 'cut wood' in m or 'cut tree' in m or 'chop tree' in m:
        return 'cut_logs', params
    if 'gather wood' in m or 'collect wood' in m or 'wood gather' in m or 'lumberjack' in m:
        return 'gather_wood', params
    if 'bring wood' in m or 'bring logs' in m or 'wood lao' in m:
        return 'bring_wood', params

    if 'help' in m or 'commands' in m or 'madad' in m:
        return 'help', params
    if any(x in m for x in ['hi', 'hello', 'hey', 'namaste', 'yo']):
        return 'hello', params
    if any(x in m for x in ['owner', 'owners']):
        return 'owner_list', params
    return 'chat', params


def detect_command(msg: str) -> str:
    """Backward-compatible intent labels for tests and external tooling."""
    normalized = normalize_msg(msg)
    if 'lumberjack mode' in normalized:
        return 'lumberjack'
    intent, params = command_from_message(msg)
    legacy_map = {
        'follow': 'follow',
        'stay': 'stop',
        'status': 'status',
        'sleep_now': 'sleep_now',
        'protect': 'protect_me',
        'find_trees': 'find_trees',
        'cut_logs': 'cut_logs',
        'bring_wood': 'bring_wood',
        'help': 'help',
        'hello': 'hello',
        'owner_list': 'owner',
        'chat': 'chat',
    }
    if intent == 'equip':
        return f"equip_{params.get('tool', 'item')}"
    if intent == 'drop':
        kind = params.get('kind', 'item')
        if kind == 'logs':
            return 'drop_logs'
        if kind == 'food':
            return 'drop_food'
        if kind == 'all':
            return 'drop_all'
        return 'drop_item'
    if intent == 'gather_wood':
        if 'collect wood' in normalized:
            return 'collect_wood'
        if 'lumberjack' in normalized:
            return 'lumberjack'
        return 'gather_wood'
    return legacy_map.get(intent, intent)


def send_public_reply(sender: str, text: str) -> None:
    reply = f'@{sender} {text}'
    last = recent_reply.get(sender.lower())
    if last == reply:
        return
    enqueue(reply)
    recent_reply[sender.lower()] = reply


def handle_owner_intent(sender: str, intent: str, params: dict) -> None:
    """Translate intent into one or more MCC commands."""
    global follow_target, protect_target, lumberjack_owner

    if intent == 'follow':
        follow_target = sender
        enqueue(f'/follow start {sender}')
        send_public_reply(sender, random.choice(FOLLOW_LINES))
        set_mode('follow')
    elif intent == 'stay':
        follow_target = ''
        protect_target = ''
        enqueue('/follow stop')
        send_public_reply(sender, random.choice(STOP_LINES))
        set_mode('patrol')
    elif intent == 'status':
        extras = []
        if follow_target:
            extras.append(f'follow={follow_target}')
        if protect_target:
            extras.append(f'protect={protect_target}')
        if mode == 'lumberjack':
            extras.append(f'lumberjack_state={lumberjack_state}')
            extras.append(f'trees={trees_chopped_count}')
        extras.append(f'mode={mode}')
        loc = f' loc={bot_location}' if bot_location else ''
        send_public_reply(
            sender,
            f"{random.choice(STATUS_LINES)} [{', '.join(extras)}]{loc}",
        )
    elif intent == 'sleep_now':
        enqueue('/bed sleep 8')
        send_public_reply(sender, 'theek hai, nearby bed dhoondh raha hoon.')
    elif intent == 'protect':
        protect_target = sender
        follow_target = sender
        enqueue(f'/follow start {sender}')
        enqueue('/changeslot 1')  # sword slot
        send_public_reply(sender, random.choice(PROTECT_LINES))
        set_mode('protect')
    elif intent == 'equip':
        tool = params.get('tool', 'sword')
        slot = params.get('slot', 1)
        enqueue(f'/changeslot {slot}')
        enqueue('/animation mainhand')
        send_public_reply(sender, f'{tool} equip kiya (hotbar slot {slot}).')
    elif intent == 'drop':
        kind = params.get('kind', 'item')
        if kind == 'logs':
            for log_type in LOG_BLOCKS:
                enqueue(f'/dropitem {log_type}')
            send_public_reply(sender, 'sab logs drop kar raha hoon.')
        elif kind == 'food':
            for food in ['Bread', 'CookedBeef', 'CookedPorkchop', 'CookedChicken', 'CookedMutton', 'Apple', 'BakedPotato', 'Carrot']:
                enqueue(f'/dropitem {food}')
            send_public_reply(sender, 'food items drop kar raha hoon.')
        elif kind == 'all':
            enqueue('/dropitem')
            send_public_reply(sender, 'full inventory drop abhi limited hai, hand ka item drop kiya.')
        else:
            enqueue('/dropitem')
            send_public_reply(sender, 'hand ka item drop kiya.')
    elif intent == 'find_trees':
        for log_type in LOG_BLOCKS:
            enqueue(f'/move <{log_type}> 24')
        send_public_reply(sender, 'forest scan kar raha hoon, nearest log dhoond raha hoon.')
    elif intent == 'cut_logs':
        enqueue('/changeslot 2')
        for _ in range(4):
            enqueue('/dig')
        send_public_reply(sender, 'log cut kar raha hoon.')
    elif intent == 'gather_wood':
        lumberjack_owner = sender
        set_mode('lumberjack')
        send_public_reply(sender, 'lumberjack mode on. wood gather karke wapas aaunga.')
    elif intent == 'bring_wood':
        lumberjack_owner = sender
        # Move directly to dropping phase if we already have wood
        globals()['lumberjack_state'] = 'returning'
        set_mode('lumberjack')
        send_public_reply(sender, 'wood le ke aapke paas aa raha hoon.')
    elif intent == 'help':
        send_public_reply(
            sender,
            'commands: come here, stay, status, sleep now, protect me, '
            'equip sword/axe/shield, drop logs/food, gather wood, bring wood.',
        )
    elif intent == 'owner_list':
        send_public_reply(sender, f"owners: {', '.join(sorted(owners))}")
    elif intent == 'hello':
        send_public_reply(sender, random.choice(HELLO_LINES))
    else:
        send_public_reply(sender, random.choice(UNKNOWN_LINES))


def maybe_reply(sender: str, msg: str) -> None:
    """Throttled owner-only reply dispatcher."""
    global last_reply_at, last_seen_player, last_seen_player_at
    if normalize_player_name(sender) == 'hn_pro_max':
        return

    t = now()
    last_seen_player = sender
    last_seen_player_at = t

    if not is_owner(sender):
        log('[ignore-non-owner]', sender, msg)
        return

    key = sender.lower()
    nmsg = normalize_msg(msg)
    prev_sender_msg, prev_time = recent_sender_msg.get(key, ('', datetime.min.replace(tzinfo=timezone.utc)))
    if nmsg == prev_sender_msg and (t - prev_time).total_seconds() < 30:
        log('[reply-skip]', 'duplicate-player-msg', sender, msg)
        return
    recent_sender_msg[key] = (nmsg, t)

    if (t - last_reply_at).total_seconds() < 2:
        log('[reply-skip]', 'global-rate-limit', sender, msg)
        return

    intent, params = command_from_message(msg)
    log('[intent]', f"sender={sender} intent={intent} params={params}")
    try:
        handle_owner_intent(sender, intent, params)
    except Exception as e:
        log('[intent-error]', repr(e))
        send_public_reply(sender, 'intent process karte hue error aaya, dobara try karo.')
    last_reply_at = t


def translate_bed_reason(line: str) -> str:
    for rx, txt in BED_REASON_PATTERNS:
        if rx.search(line):
            return txt
    return ''


def maybe_announce_bed_reason(line: str) -> None:
    global last_bed_reason, last_bed_announce_at
    reason = translate_bed_reason(line)
    if not reason:
        return
    t = now()
    if reason == last_bed_reason and (t - last_bed_announce_at).total_seconds() < 300:
        return
    if last_seen_player and is_owner(last_seen_player) and (t - last_seen_player_at).total_seconds() < 600:
        enqueue(f'@{last_seen_player} abhi so nahi sakta: {reason}.')
    else:
        enqueue(f'abhi so nahi sakta: {reason}.')
    last_bed_reason = reason
    last_bed_announce_at = t


def maybe_update_location(line: str) -> None:
    global bot_location
    m = LOCATION_RE.search(line)
    if m:
        try:
            bot_location = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        except Exception:
            pass


# =============================================================================
# Health endpoint for Railway
# =============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ['/', '/health', '/healthz']:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'not found')
            return
        payload = {
            'ok': True,
            'joined': joined,
            'connected_once': connected_once,
            'mode': mode,
            'follow_target': follow_target,
            'protect_target': protect_target,
            'lumberjack_state': lumberjack_state,
            'trees_chopped': trees_chopped_count,
            'owners': sorted(list(getattr(STATE, 'owners', owners))),
            'last_seen_player': last_seen_player,
            'bot_location': bot_location,
            'uptime_seconds': int((datetime.now(timezone.utc) - process_started_at).total_seconds()),
        }
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # silence default logging
        return


def start_health_server() -> None:
    global health_server_started
    if health_server_started:
        return
    port = int(os.getenv('PORT', '8080'))
    try:
        server = ThreadingHTTPServer(('0.0.0.0', port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        health_server_started = True
        log('[health-server]', f'listening on 0.0.0.0:{port}')
    except OSError as e:
        log('[health-server-error]', f'port {port} unavailable: {e}')


# =============================================================================
# Lifecycle
# =============================================================================
def sig_handler(signum, frame) -> None:
    global stop_flag
    stop_flag = True
    log('[signal]', signum)


def main() -> int:
    global stop_flag, joined, connected_once, active_after, next_bed_attempt_at
    global panic_until, owners, process_handle

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    open(LOG, 'w', encoding='utf-8').write('')
    owners = load_owners()
    STATE.owners = owners
    log('[startup]', 'launching MCC supervisor (production v2)')
    log('[owners]', ','.join(sorted(owners)))
    start_health_server()

    if not os.path.exists(MCC):
        log('[fatal]', f'MCC binary missing at {MCC}')
        return 2
    if not os.path.exists(CONFIG):
        log('[fatal]', f'MCC config missing at {CONFIG}')
        return 2

    proc = subprocess.Popen(
        [MCC, CONFIG],
        cwd=BASE,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    process_handle = proc

    threading.Thread(target=send_loop, args=(proc,), daemon=True).start()
    threading.Thread(target=movement_loop, daemon=True).start()
    threading.Thread(target=lumberjack_loop, daemon=True).start()

    greeted_session = False
    next_bed_attempt_at = now() + timedelta(seconds=40)

    try:
        while not stop_flag:
            assert proc.stdout is not None
            raw = proc.stdout.readline()
            if raw == '' and proc.poll() is not None:
                break
            if not raw:
                time.sleep(0.05)
                continue

            line = clean(raw)
            if not line:
                continue
            log('[mcc]', line)

            if SERVER_JOIN_RE.search(line):
                joined = True
                connected_once = True
                greeted_session = False
                active_after = now() + timedelta(seconds=10)
                next_bed_attempt_at = now() + timedelta(seconds=40)
                clear_pending_commands()
                continue

            if DISCONNECT_RE.search(line):
                log('[disconnect-detected]', line)
                reset_runtime_after_disconnect()
                continue

            if joined and not greeted_session and SELF_SPAWN_RE.search(line):
                greeted_session = True
                active_after = now() + timedelta(seconds=8)
                enqueue('HN_PRO_MAX online. owner-only assistant + lumberjack ready.')
                continue

            # Player public chat
            m = CHAT_RE.match(line)
            if m:
                sender, msg = m.group(1), m.group(2)
                maybe_reply(sender, msg)
                continue

            # Player whispers / private
            w = WHISPER_RE.match(line)
            if w:
                sender, msg = w.group(1), w.group(2)
                maybe_reply(sender, msg)
                continue

            # Other players joining
            j = JOIN_RE.match(line)
            if j:
                player = j.group(1)
                if normalize_player_name(player) != 'hn_pro_max':
                    globals()['last_seen_player'] = player
                    globals()['last_seen_player_at'] = now()
                continue

            if LEAVE_RE.match(line):
                continue

            # Hostile damage events
            if HOSTILE_DEATH_RE.search(line):
                panic_until = now() + timedelta(seconds=20)
                if mode in ('patrol', 'follow'):
                    enqueue('/move south -f')
                    enqueue('/move west -f')
                continue

            maybe_update_location(line)
            maybe_announce_bed_reason(line)
    except Exception as e:
        log('[main-loop-error]', repr(e))
    finally:
        stop_flag = True
        try:
            if proc.poll() is None:
                clear_pending_commands()
                try:
                    assert proc.stdin is not None
                    proc.stdin.write('/quit\n')
                    proc.stdin.flush()
                except Exception:
                    pass
                time.sleep(1)
                proc.terminate()
                time.sleep(2)
                if proc.poll() is None:
                    proc.kill()
        except Exception as e:
            log('[shutdown-error]', repr(e))

    rc = proc.poll()
    log('[exit]', f'returncode={rc} connected_once={connected_once}')
    return 0 if rc in (0, None) else 1


if __name__ == '__main__':
    sys.exit(main())
