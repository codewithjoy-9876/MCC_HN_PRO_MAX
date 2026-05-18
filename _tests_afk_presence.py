import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcc_supervisor as mod  # noqa: E402


MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


def reset_state():
    mod.joined = True
    mod.connected_once = True
    mod.joined_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    mod.active_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    mod.next_bed_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    mod.last_bed_attempt_at = MIN_TS
    mod.last_bed_reason = ''
    mod.last_bed_reason_at = MIN_TS
    mod.last_sleep_success_at = MIN_TS
    mod.last_weather_hint = 'unknown'
    mod.last_weather_at = MIN_TS
    mod.last_disconnect_reason = ''
    mod.last_disconnect_at = MIN_TS
    mod.last_hostile_hint = ''
    mod.last_hostile_at = MIN_TS
    mod.last_seen_player = ''
    mod.last_seen_player_at = MIN_TS
    mod.known_players.clear()
    mod.bot_location = None
    mod.last_position_at = MIN_TS
    mod.last_progress_at = MIN_TS
    while True:
        try:
            mod.cmd_q.get_nowait()
        except Exception:
            break


def test_bed_reason_awareness():
    reset_state()
    mod.maybe_update_bed_awareness('You can sleep only at night or during thunderstorms')
    assert mod.last_bed_reason == 'not_night_or_thunderstorm'
    assert mod.last_weather_hint == 'clear_or_day'


def test_player_awareness():
    reset_state()
    mod.maybe_update_player_awareness('Steve joined the game')
    assert 'Steve' in mod.known_players
    assert mod.last_seen_player == 'Steve'
    mod.maybe_update_player_awareness('* Alex hello')
    assert 'Alex' in mod.known_players
    assert mod.last_seen_player == 'Alex'


def test_location_progress():
    reset_state()
    mod.maybe_update_location('X:1.0 Y:64.0 Z:1.0')
    mod.maybe_update_location('X:2.0 Y:64.0 Z:1.0')
    state = mod.state_payload()
    assert state['movement']['moving'] is True
    assert state['bot_location'] == (2.0, 64.0, 1.0)


def test_disconnect_state():
    reset_state()
    mod.reset_runtime_after_disconnect('Connection has been lost')
    state = mod.state_payload()
    assert state['joined'] is False
    assert state['disconnect']['last_reason'] == 'Connection has been lost'


def test_watchdog_reconnect_timeout():
    reset_state()
    mod.joined = False
    mod.last_disconnect_at = datetime.now(timezone.utc) - timedelta(seconds=mod.REJOIN_GRACE_SECONDS + 5)
    assert mod.watchdog_reason() == 'reconnect_timeout'


def test_watchdog_position_stale():
    reset_state()
    mod.joined = True
    mod.joined_at = datetime.now(timezone.utc) - timedelta(seconds=mod.NO_POSITION_UPDATE_SECONDS + 5)
    mod.last_position_at = datetime.now(timezone.utc) - timedelta(seconds=mod.NO_POSITION_UPDATE_SECONDS + 5)
    mod.last_progress_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert mod.watchdog_reason() == 'position_updates_stalled'


if __name__ == '__main__':
    test_bed_reason_awareness()
    test_player_awareness()
    test_location_progress()
    test_disconnect_state()
    test_watchdog_reconnect_timeout()
    test_watchdog_position_stale()
    print('afk presence tests PASSED')
