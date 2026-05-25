#!/usr/bin/env python3
"""
HN_PRO_MAX MCC Supervisor (AFK Presence Build)
- 24/7 reconnect loop via MCC + wrapper
- Silent AFK-presence movement every ~1-2 seconds
- Automatic bed usage without chat spam
- Coordinate / bed / player / hostile awareness via health state
- No owner system, no command handling, no digging, no combat, no inventory actions

Built around Minecraft Console Client (MCC) v26.1 CLI commands.
References:
  https://mccteam.github.io/guide/usage.html
  https://mccteam.github.io/guide/chat-bots.html
  https://github.com/MCCTeam/Minecraft-Console-Client
"""

from __future__ import annotations

import json
import math
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

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.getenv('MCC_CONFIG_PATH', os.path.join(BASE, 'MCC_HN_PRO_MAX.ini'))
MCC = os.path.join(BASE, 'MinecraftClient')
LOG = os.path.join(BASE, 'supervisor_runtime.log')
STATE_JSON = os.path.join(BASE, 'presence_state.json')

ANSI_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
CHAT_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?\*\s+(\S+)\s+(.+)$')
WHISPER_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?(\S+)\s+whispers to you:\s+(.+)$', re.I)
JOIN_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?([^\s]+) joined the game$')
LEAVE_RE = re.compile(r'^(?:\d{2}:\d{2}:\d{2}\s+)?([^\s]+) left the game$')
DISCONNECT_RE = re.compile(
    r'(Disconnected by Server|Connection has been lost|Cannot send text: not connected to a server'
    r'|Received unknown packet id|A timeout occured|SocketException: Connection timed out|idle for too long)',
    re.I,
)
SERVER_JOIN_RE = re.compile(r'Server was successfully joined', re.I)
SELF_SPAWN_RE = re.compile(r'HN_PRO_MAX joined the game', re.I)
LOCATION_RE = re.compile(r'X:(-?\d+\.\d+)\s+Y:(-?\d+\.\d+)\s+Z:(-?\d+\.\d+)')
RAIN_RE = re.compile(r'(?:started raining|rain started|it is raining|rain begins)', re.I)
RAIN_STOP_RE = re.compile(r'(?:rain stopped|stopped raining|rain has stopped)', re.I)
THUNDER_RE = re.compile(r'(?:thunderstorm|started thundering|thunder begins)', re.I)
THUNDER_STOP_RE = re.compile(r'(?:thunderstorm ended|stopped thundering|thunder has stopped)', re.I)
HOSTILE_RE = re.compile(r'(Zombie|Skeleton|Creeper|Spider|Drowned|Husk|Stray|Phantom|Witch|Pillager|Vindicator|Slime|Enderman)', re.I)
DUPLICATE_LOGIN_RE = re.compile(r'logged in from another location', re.I)
CANNOT_MOVE_RE = re.compile(r'Cannot move(?:\s+(north|east|south|west|up|down))?|Cannot move in that direction', re.I)

BED_REASON_PATTERNS = [
    (re.compile(r'Could not find a bed', re.I), 'no_bed_found'),
    (re.compile(r'Can not reach the bed safely', re.I), 'bed_not_safe'),
    (re.compile(r'Failed to reach the bed position', re.I), 'bed_path_timeout'),
    (re.compile(r'not a bed', re.I), 'not_a_bed'),
    (re.compile(r'bed is occupied', re.I), 'bed_occupied'),
    (re.compile(r'bed is obstructed', re.I), 'bed_obstructed'),
    (re.compile(r'too far away', re.I), 'bed_too_far'),
    (re.compile(r'monsters nearby', re.I), 'monsters_nearby'),
    (re.compile(r'only at night|during thunderstorms', re.I), 'not_night_or_thunderstorm'),
    (re.compile(r'Could not lay in bed', re.I), 'bed_conditions_failed'),
]

stop_flag = False
restart_requested = False
joined = False
connected_once = False
process_started_at = datetime.now(timezone.utc)
active_after = datetime.min.replace(tzinfo=timezone.utc)
next_bed_attempt_at = datetime.min.replace(tzinfo=timezone.utc)
last_bed_attempt_at = datetime.min.replace(tzinfo=timezone.utc)
last_bed_reason = ''
last_bed_reason_at = datetime.min.replace(tzinfo=timezone.utc)
last_sleep_success_at = datetime.min.replace(tzinfo=timezone.utc)
last_weather_hint = 'unknown'
last_weather_at = datetime.min.replace(tzinfo=timezone.utc)
last_disconnect_reason = ''
last_disconnect_at = datetime.min.replace(tzinfo=timezone.utc)
last_hostile_hint = ''
last_hostile_at = datetime.min.replace(tzinfo=timezone.utc)
last_seen_player = ''
last_seen_player_at = datetime.min.replace(tzinfo=timezone.utc)
known_players: set[str] = set()
bot_location: tuple[float, float, float] | None = None
last_position_at = datetime.min.replace(tzinfo=timezone.utc)
last_progress_at = datetime.min.replace(tzinfo=timezone.utc)
last_state_write_at = datetime.min.replace(tzinfo=timezone.utc)
process_handle: subprocess.Popen | None = None
cmd_q: queue.Queue[str] = queue.Queue()
move_index = 0
health_server_started = False
joined_at = datetime.min.replace(tzinfo=timezone.utc)
movement_anchor: tuple[float, float, float] | None = None
last_move_direction = ''
blocked_directions: dict[str, datetime] = {}
pending_followup_command = ''
pending_followup_at = datetime.min.replace(tzinfo=timezone.utc)
next_action_at = datetime.min.replace(tzinfo=timezone.utc)
bed_search_radius = 8
next_motion_at = datetime.min.replace(tzinfo=timezone.utc)
last_motion_style = 'idle'
last_motion_command = ''
last_motion_reason = 'startup'
last_move_direction = ''
direction_failures = {d: 0 for d in ('north', 'east', 'south', 'west')}
direction_cooldown_until = {d: datetime.min.replace(tzinfo=timezone.utc) for d in ('north', 'east', 'south', 'west')}

AFK_MODE = 'afk_presence'
MOVEMENT_INTERVAL_SECONDS = 1.35
SEND_INTERVAL_MIN_SECONDS = 0.55
SEND_INTERVAL_MAX_SECONDS = 1.25
MOVEMENT_DELAY_MIN_SECONDS = 1.6
MOVEMENT_DELAY_MAX_SECONDS = 4.8
MOVEMENT_LONG_PAUSE_CHANCE = 0.18
MOVEMENT_LONG_PAUSE_MIN_SECONDS = 5.5
MOVEMENT_LONG_PAUSE_MAX_SECONDS = 11.0
BED_SCAN_INTERVAL_SECONDS = 50
BED_SCAN_JITTER_SECONDS = 16
BED_SEARCH_RADII = [8, 12, 20]
CHUNK_LOAD_TARGET = 12
REJOIN_GRACE_SECONDS = 20
INITIAL_CONNECT_RESTART_SECONDS = 120
NO_POSITION_UPDATE_SECONDS = 45
NO_PROGRESS_RESTART_SECONDS = 150
WATCHDOG_INTERVAL_SECONDS = 5
DIRECTION_COOLDOWN_SECONDS = 24
FAILURE_COOLDOWN_STEP_SECONDS = 12
ANCHOR_DRIFT_LIMIT = 1.15
CARDINALS = ('north', 'east', 'south', 'west')
LOOK_ONLY_ACTIONS = ['/sneak', '/animation mainhand', '/move center']


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso_or_none(ts: datetime) -> str | None:
    if ts == datetime.min.replace(tzinfo=timezone.utc):
        return None
    return ts.isoformat()


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
    return ANSI_RE.sub('', line).replace('\u258c', '').replace('\r', '').strip()


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


def distance_3d(a: tuple[float, float, float] | None, b: tuple[float, float, float] | None) -> float:
    if a is None or b is None:
        return 0.0
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def translate_bed_reason(line: str) -> str:
    for rx, key in BED_REASON_PATTERNS:
        if rx.search(line):
            return key
    return ''


def available_directions(now_ts: datetime | None = None) -> list[str]:
    t = now_ts or now()
    candidates = [d for d in CARDINALS if direction_cooldown_until[d] <= t]
    if not candidates:
        soonest = min(direction_cooldown_until.values())
        if soonest > datetime.min.replace(tzinfo=timezone.utc):
            for d in CARDINALS:
                direction_cooldown_until[d] = t
        candidates = list(CARDINALS)
    if last_move_direction in candidates and len(candidates) > 1:
        reordered = [d for d in candidates if d != last_move_direction]
        reordered.append(last_move_direction)
        return reordered
    return candidates


def choose_bed_radius() -> int:
    global bed_search_radius
    if last_bed_reason in {'no_bed_found', 'bed_path_timeout', 'bed_not_safe', 'bed_too_far', 'not_a_bed'}:
        try:
            idx = BED_SEARCH_RADII.index(bed_search_radius)
        except ValueError:
            idx = 0
        bed_search_radius = BED_SEARCH_RADII[min(idx + 1, len(BED_SEARCH_RADII) - 1)]
    else:
        bed_search_radius = BED_SEARCH_RADII[0]
    return bed_search_radius


def schedule_next_motion(base_seconds: float | None = None) -> None:
    global next_motion_at
    delay = base_seconds if base_seconds is not None else random.uniform(MOVEMENT_DELAY_MIN_SECONDS, MOVEMENT_DELAY_MAX_SECONDS)
    if random.random() < MOVEMENT_LONG_PAUSE_CHANCE:
        delay += random.uniform(MOVEMENT_LONG_PAUSE_MIN_SECONDS, MOVEMENT_LONG_PAUSE_MAX_SECONDS)
    next_motion_at = now() + timedelta(seconds=delay)


def register_move_attempt(cmd: str) -> None:
    global last_move_direction, last_motion_command
    last_motion_command = cmd
    if not cmd.startswith('/move '):
        return
    parts = cmd.split()
    if len(parts) < 2:
        return
    direction = parts[1].strip().lower()
    if direction in CARDINALS:
        last_move_direction = direction


def plan_movement_action(now_ts: datetime | None = None) -> tuple[list[str], str, str]:
    t = now_ts or now()
    candidates = available_directions(t)
    primary = random.choice(candidates)
    secondary_pool = [d for d in candidates if d != primary]
    secondary = random.choice(secondary_pool) if secondary_pool else primary
    player_recent = last_seen_player_at != datetime.min.replace(tzinfo=timezone.utc) and (t - last_seen_player_at).total_seconds() <= 90
    hostile_recent = last_hostile_at != datetime.min.replace(tzinfo=timezone.utc) and (t - last_hostile_at).total_seconds() <= 45

    if movement_anchor is not None and bot_location is not None:
        dx = bot_location[0] - movement_anchor[0]
        dz = bot_location[2] - movement_anchor[2]
        if abs(dx) >= ANCHOR_DRIFT_LIMIT or abs(dz) >= ANCHOR_DRIFT_LIMIT:
            recover_dir = primary
            if abs(dx) >= abs(dz):
                if dx > 0 and 'west' in candidates:
                    recover_dir = 'west'
                elif dx < 0 and 'east' in candidates:
                    recover_dir = 'east'
            else:
                if dz > 0 and 'north' in candidates:
                    recover_dir = 'north'
                elif dz < 0 and 'south' in candidates:
                    recover_dir = 'south'
            return [f'/look {recover_dir}', f'/move {recover_dir}', '/move center', '/move get'], 'recover', 'return_to_anchor'
        if dx > 0.40 and 'west' in candidates:
            primary = 'west'
        elif dx < -0.40 and 'east' in candidates:
            primary = 'east'
        elif dz > 0.40 and 'north' in candidates:
            primary = 'north'
        elif dz < -0.40 and 'south' in candidates:
            primary = 'south'

    roll = random.random()
    if hostile_recent:
        roll = min(0.12, roll)
    elif player_recent:
        roll = max(0.28, roll)

    if roll < 0.20:
        return [f'/look {primary}', '/move get'], 'look_only', 'scan_room'
    if roll < 0.48:
        cmds = [f'/look {primary}', f'/move {primary}', '/move center']
        if random.random() < 0.55:
            cmds.append('/move get')
        return cmds, 'walk', 'short_walk'
    if roll < 0.60:
        cmds = [f'/look {primary}', f'/move {secondary}', '/move center']
        if random.random() < 0.45:
            cmds.append('/move get')
        return cmds, 'sidestep', 'space_probe'
    if roll < 0.82:
        return [random.choice(LOOK_ONLY_ACTIONS)], 'micro_idle', 'human_pause'
    cmds = ['/move center']
    if random.random() < 0.55:
        cmds.insert(0, f'/look {primary}')
    if random.random() < 0.35:
        cmds.append('/move get')
    return cmds, 'reset', 'recenter'


def movement_state(now_ts: datetime | None = None) -> dict:
    t = now_ts or now()
    moving = last_progress_at != datetime.min.replace(tzinfo=timezone.utc) and (t - last_progress_at).total_seconds() < 8
    next_action_in = None if next_motion_at == datetime.min.replace(tzinfo=timezone.utc) else max(0, int((next_motion_at - t).total_seconds()))
    blocked_directions = {
        d: max(0, int((until - t).total_seconds()))
        for d, until in direction_cooldown_until.items()
        if until > t
    }
    return {
        'moving': moving,
        'seconds_since_progress': None if last_progress_at == datetime.min.replace(tzinfo=timezone.utc) else int((t - last_progress_at).total_seconds()),
        'last_progress_at': iso_or_none(last_progress_at),
        'style': last_motion_style,
        'last_command': last_motion_command or None,
        'last_reason': last_motion_reason or None,
        'next_action_in_seconds': next_action_in,
        'blocked_directions': blocked_directions,
        'anchor_location': movement_anchor,
        'bed_search_radius': bed_search_radius,
    }


def seconds_since(ts: datetime, now_ts: datetime | None = None) -> float | None:
    if ts == datetime.min.replace(tzinfo=timezone.utc):
        return None
    t = now_ts or now()
    return (t - ts).total_seconds()


def watchdog_reason(now_ts: datetime | None = None) -> str | None:
    t = now_ts or now()
    if not connected_once and not joined and last_disconnect_at != datetime.min.replace(tzinfo=timezone.utc):
        if (t - process_started_at).total_seconds() >= INITIAL_CONNECT_RESTART_SECONDS:
            return 'initial_connect_stalled'
        return None
    if connected_once and not joined and last_disconnect_at != datetime.min.replace(tzinfo=timezone.utc):
        if (t - last_disconnect_at).total_seconds() >= REJOIN_GRACE_SECONDS:
            return 'reconnect_timeout'
        return None
    if not joined:
        return None
    if joined_at != datetime.min.replace(tzinfo=timezone.utc) and (t - joined_at).total_seconds() < 15:
        return None
    if last_position_at == datetime.min.replace(tzinfo=timezone.utc):
        if joined_at != datetime.min.replace(tzinfo=timezone.utc) and (t - joined_at).total_seconds() >= NO_POSITION_UPDATE_SECONDS:
            return 'no_position_updates'
        return None
    if (t - last_position_at).total_seconds() >= NO_POSITION_UPDATE_SECONDS:
        return 'position_updates_stalled'
    if last_progress_at != datetime.min.replace(tzinfo=timezone.utc) and (t - last_progress_at).total_seconds() >= NO_PROGRESS_RESTART_SECONDS:
        return 'movement_stalled'
    return None


def request_supervisor_restart(reason: str) -> None:
    global stop_flag, restart_requested
    restart_requested = True
    stop_flag = True
    log('[watchdog]', f'forcing supervisor restart reason={reason}')
    write_state(force=True)
    clear_pending_commands()
    proc = process_handle
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                time.sleep(2)
                if proc.poll() is None:
                    proc.kill()
        except Exception as e:
            log('[watchdog-terminate-error]', repr(e))


def watchdog_loop() -> None:
    while not stop_flag:
        reason = watchdog_reason()
        if reason:
            request_supervisor_restart(reason)
            return
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


def state_payload(now_ts: datetime | None = None) -> dict:
    t = now_ts or now()
    next_bed_in = None
    if next_bed_attempt_at != datetime.min.replace(tzinfo=timezone.utc):
        next_bed_in = max(0, int((next_bed_attempt_at - t).total_seconds()))
    payload = {
        'ok': True,
        'service': 'HN_PRO_MAX',
        'mode': AFK_MODE,
        'joined': joined,
        'connected_once': connected_once,
        'uptime_seconds': int((t - process_started_at).total_seconds()),
        'movement_interval_seconds': MOVEMENT_INTERVAL_SECONDS,
        'chunk_load_target': CHUNK_LOAD_TARGET,
        'bot_location': bot_location,
        'movement': movement_state(t),
        'bed_awareness': {
            'last_attempt_at': iso_or_none(last_bed_attempt_at),
            'next_attempt_in_seconds': next_bed_in,
            'last_reason': last_bed_reason or None,
            'last_reason_at': iso_or_none(last_bed_reason_at),
            'last_success_at': iso_or_none(last_sleep_success_at),
        },
        'weather_awareness': {
            'hint': last_weather_hint,
            'updated_at': iso_or_none(last_weather_at),
        },
        'nearby_awareness': {
            'last_seen_player': last_seen_player or None,
            'last_seen_player_at': iso_or_none(last_seen_player_at),
            'known_players': sorted(known_players),
            'last_hostile_hint': last_hostile_hint or None,
            'last_hostile_at': iso_or_none(last_hostile_at),
        },
        'disconnect': {
            'last_reason': last_disconnect_reason or None,
            'last_at': iso_or_none(last_disconnect_at),
        },
        'watchdog': {
            'pending_restart_reason': watchdog_reason(t),
            'rejoin_grace_seconds': REJOIN_GRACE_SECONDS,
            'initial_connect_restart_seconds': INITIAL_CONNECT_RESTART_SECONDS,
            'no_position_update_seconds': NO_POSITION_UPDATE_SECONDS,
            'no_progress_restart_seconds': NO_PROGRESS_RESTART_SECONDS,
        },
    }
    return payload


def write_state(force: bool = False) -> None:
    global last_state_write_at
    t = now()
    if not force and (t - last_state_write_at).total_seconds() < 2:
        return
    tmp = STATE_JSON + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state_payload(t), f, ensure_ascii=False)
        os.replace(tmp, STATE_JSON)
        last_state_write_at = t
    except Exception as e:
        log('[state-write-error]', repr(e))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ['/', '/health', '/healthz']:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'not found')
            return
        body = json.dumps(state_payload()).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
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


def send_loop(proc: subprocess.Popen) -> None:
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
            register_move_attempt(cmd)
            log('[send]', cmd)
        except Exception as e:
            log('[send-error]', repr(e))
            break
        time.sleep(random.uniform(SEND_INTERVAL_MIN_SECONDS, SEND_INTERVAL_MAX_SECONDS))


def movement_loop() -> None:
    global move_index, next_bed_attempt_at, last_bed_attempt_at, next_motion_at, last_motion_style, last_motion_reason
    while not stop_flag:
        t = now()
        if joined and t >= active_after:
            if t >= next_bed_attempt_at:
                radius = choose_bed_radius()
                enqueue('/move get')
                enqueue(f'/bed sleep {radius}')
                last_bed_attempt_at = t
                next_bed_attempt_at = t + timedelta(seconds=BED_SCAN_INTERVAL_SECONDS + random.uniform(-BED_SCAN_JITTER_SECONDS, BED_SCAN_JITTER_SECONDS))
                write_state(force=True)

            if next_motion_at == datetime.min.replace(tzinfo=timezone.utc) or t >= next_motion_at:
                cmds, style, reason = plan_movement_action(t)
                for cmd in cmds:
                    enqueue(cmd)
                last_motion_style = style
                last_motion_reason = reason
                move_index += 1
                schedule_next_motion()
                write_state(force=True)
        time.sleep(0.35)


def reset_runtime_after_disconnect(reason: str = '') -> None:
    global joined, active_after, next_motion_at, last_disconnect_reason, last_disconnect_at, joined_at, bot_location, last_position_at, last_progress_at, last_motion_style, last_motion_command, last_motion_reason, last_move_direction, movement_anchor, bed_search_radius
    joined = False
    joined_at = datetime.min.replace(tzinfo=timezone.utc)
    active_after = datetime.min.replace(tzinfo=timezone.utc)
    next_motion_at = datetime.min.replace(tzinfo=timezone.utc)
    bot_location = None
    movement_anchor = None
    bed_search_radius = BED_SEARCH_RADII[0]
    last_position_at = datetime.min.replace(tzinfo=timezone.utc)
    last_progress_at = datetime.min.replace(tzinfo=timezone.utc)
    last_motion_style = 'idle'
    last_motion_command = ''
    last_motion_reason = 'disconnected'
    last_move_direction = ''
    for direction in CARDINALS:
        direction_failures[direction] = 0
        direction_cooldown_until[direction] = datetime.min.replace(tzinfo=timezone.utc)
    clear_pending_commands()
    if reason:
        last_disconnect_reason = reason
        last_disconnect_at = now()
    write_state(force=True)


def maybe_update_location(line: str) -> None:
    global bot_location, last_position_at, last_progress_at, last_motion_reason, movement_anchor
    m = LOCATION_RE.search(line)
    if not m:
        return
    try:
        new_loc = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    except Exception:
        return
    previous = bot_location
    bot_location = new_loc
    if movement_anchor is None and joined and new_loc != (0.0, 0.0, 0.0):
        movement_anchor = new_loc
    last_position_at = now()
    if previous is None or distance_3d(previous, new_loc) >= 0.15:
        last_progress_at = last_position_at
        if last_move_direction in direction_failures:
            direction_failures[last_move_direction] = 0
            direction_cooldown_until[last_move_direction] = datetime.min.replace(tzinfo=timezone.utc)
        if last_motion_style in {'walk', 'sidestep'}:
            last_motion_reason = 'progress_confirmed'
    write_state()


def maybe_update_weather(line: str) -> None:
    global last_weather_hint, last_weather_at
    new_hint = None
    if THUNDER_RE.search(line):
        new_hint = 'thunderstorm'
    elif THUNDER_STOP_RE.search(line):
        new_hint = 'clear'
    elif RAIN_RE.search(line):
        new_hint = 'rain'
    elif RAIN_STOP_RE.search(line):
        new_hint = 'clear'
    if new_hint:
        last_weather_hint = new_hint
        last_weather_at = now()
        write_state()


def maybe_update_bed_awareness(line: str) -> None:
    global last_bed_reason, last_bed_reason_at, next_bed_attempt_at, last_weather_hint, last_weather_at, last_sleep_success_at, bed_search_radius
    reason = translate_bed_reason(line)
    if reason:
        last_bed_reason = reason
        last_bed_reason_at = now()
        if reason == 'not_night_or_thunderstorm':
            last_weather_hint = 'clear_or_day'
            last_weather_at = now()
            next_bed_attempt_at = now() + timedelta(seconds=75)
        elif reason == 'monsters_nearby':
            next_bed_attempt_at = now() + timedelta(seconds=30)
        elif reason in {'no_bed_found', 'bed_path_timeout', 'bed_not_safe', 'bed_too_far', 'not_a_bed'}:
            choose_bed_radius()
            next_bed_attempt_at = now() + timedelta(seconds=18)
        write_state(force=True)
        return
    if 'sleep' in line.lower() and ('leave bed' in line.lower() or 'lay in bed' in line.lower() or 'sleeping' in line.lower()):
        last_sleep_success_at = now()
        last_bed_reason = ''
        bed_search_radius = BED_SEARCH_RADII[0]
        write_state(force=True)


def maybe_update_player_awareness(line: str) -> None:
    global last_seen_player, last_seen_player_at
    m = JOIN_RE.match(line)
    if m:
        player = m.group(1)
        if player.lower() != 'hn_pro_max':
            known_players.add(player)
            last_seen_player = player
            last_seen_player_at = now()
            write_state()
        return
    m = LEAVE_RE.match(line)
    if m:
        player = m.group(1)
        known_players.discard(player)
        if last_seen_player == player:
            last_seen_player_at = now()
        write_state()
        return
    m = CHAT_RE.match(line)
    if m:
        player = m.group(1)
        if player.lower() != 'hn_pro_max':
            known_players.add(player)
            last_seen_player = player
            last_seen_player_at = now()
            write_state()
        return
    m = WHISPER_RE.match(line)
    if m:
        player = m.group(1)
        if player.lower() != 'hn_pro_max':
            known_players.add(player)
            last_seen_player = player
            last_seen_player_at = now()
            write_state()


def maybe_update_hostile_awareness(line: str) -> None:
    global last_hostile_hint, last_hostile_at
    m = HOSTILE_RE.search(line)
    if m:
        last_hostile_hint = m.group(1)
        last_hostile_at = now()
        write_state()


def maybe_update_motion_feedback(line: str) -> None:
    global last_motion_reason
    lowered = line.lower()
    if 'cannot move in that direction' in lowered and last_move_direction in direction_failures:
        direction_failures[last_move_direction] += 1
        cooldown = DIRECTION_COOLDOWN_SECONDS + ((direction_failures[last_move_direction] - 1) * FAILURE_COOLDOWN_STEP_SECONDS)
        direction_cooldown_until[last_move_direction] = now() + timedelta(seconds=cooldown)
        last_motion_reason = f'blocked_{last_move_direction}'
        schedule_next_motion(base_seconds=random.uniform(2.5, 4.5))
        write_state(force=True)
        return
    if 'you are dead' in lowered or 'was slain by' in lowered or 'died' in lowered:
        last_motion_reason = 'death_pause'
        schedule_next_motion(base_seconds=random.uniform(8.0, 12.0))
        write_state(force=True)


def sig_handler(signum, frame) -> None:
    global stop_flag
    stop_flag = True
    log('[signal]', signum)


def main() -> int:
    global joined, connected_once, active_after, next_bed_attempt_at, next_motion_at, process_handle, joined_at, last_position_at, last_progress_at, restart_requested, last_motion_style, last_motion_reason, movement_anchor, bed_search_radius

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    open(LOG, 'w', encoding='utf-8').write('')
    write_state(force=True)
    log('[startup]', 'launching MCC supervisor (afk presence build)')
    if os.getenv('DISABLE_EMBEDDED_HEALTH_SERVER', '1') != '1':
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
    threading.Thread(target=watchdog_loop, daemon=True).start()

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
                join_ts = now()
                joined = True
                connected_once = True
                joined_at = join_ts
                movement_anchor = None
                bed_search_radius = BED_SEARCH_RADII[0]
                active_after = join_ts + timedelta(seconds=10)
                next_bed_attempt_at = join_ts + timedelta(seconds=22)
                next_motion_at = join_ts + timedelta(seconds=random.uniform(11.0, 16.0))
                last_position_at = datetime.min.replace(tzinfo=timezone.utc)
                last_progress_at = datetime.min.replace(tzinfo=timezone.utc)
                last_motion_style = 'warmup'
                last_motion_reason = 'joined_server'
                clear_pending_commands()
                enqueue('/move get')
                write_state(force=True)
                continue

            if DUPLICATE_LOGIN_RE.search(line):
                reset_runtime_after_disconnect(line)
                request_supervisor_restart('duplicate_login')
                continue

            if DISCONNECT_RE.search(line):
                reset_runtime_after_disconnect(line)
                lowered = line.lower()
                if 'cannot send text: not connected to a server' in lowered or 'connection has been lost' in lowered:
                    request_supervisor_restart('session_desync')
                continue

            if SELF_SPAWN_RE.search(line):
                write_state(force=True)
                continue

            maybe_update_location(line)
            maybe_update_weather(line)
            maybe_update_bed_awareness(line)
            maybe_update_player_awareness(line)
            maybe_update_hostile_awareness(line)
            maybe_update_motion_feedback(line)
    except Exception as e:
        log('[main-loop-error]', repr(e))
    finally:
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
        reset_runtime_after_disconnect('supervisor_shutdown')

    rc = proc.poll()
    log('[exit]', f'returncode={rc} connected_once={connected_once} restart_requested={restart_requested}')
    write_state(force=True)
    if restart_requested:
        return 17
    return 0 if rc in (0, None) else 1


if __name__ == '__main__':
    sys.exit(main())
