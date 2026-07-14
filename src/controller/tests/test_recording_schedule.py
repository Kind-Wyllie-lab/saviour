"""
Tests for scheduled-session start/stop decisions in src/controller/recording.py

Covers same-day windows and windows that cross midnight (e.g. 22:00-06:00),
where a naive lexicographic "HH:MM" comparison stops the session seconds
after it starts.
"""

from src.controller.recording import Recording, RecordingSession, SessionState


def _session(**overrides) -> RecordingSession:
    defaults = dict(
        session_name="dark_cycle",
        target="camera",
        state=SessionState.SCHEDULED,
        scheduled=True,
        scheduled_start_time="22:00",
        scheduled_end_time="06:00",
        scheduled_last_start_date=None,
        scheduled_days=[],
    )
    defaults.update(overrides)
    return RecordingSession(**defaults)


TODAY = "2026-07-14"
YESTERDAY = "2026-07-13"
WEEKDAY = 1  # arbitrary, matches "every day" (scheduled_days=[])


def _action(session, current_time):
    return Recording._scheduled_session_action(
        session, TODAY, YESTERDAY, current_time, WEEKDAY
    )


class TestSameDayWindow:
    def test_starts_when_start_time_reached(self):
        session = _session(scheduled_start_time="09:00", scheduled_end_time="17:00")
        assert _action(session, "09:00") == "start"

    def test_does_not_start_before_start_time(self):
        session = _session(scheduled_start_time="09:00", scheduled_end_time="17:00")
        assert _action(session, "08:59") is None

    def test_stops_when_end_time_reached_same_day(self):
        session = _session(
            scheduled_start_time="09:00", scheduled_end_time="17:00",
            state=SessionState.ACTIVE, scheduled_last_start_date=TODAY,
        )
        assert _action(session, "17:00") == "stop"

    def test_does_not_stop_before_end_time(self):
        session = _session(
            scheduled_start_time="09:00", scheduled_end_time="17:00",
            state=SessionState.ACTIVE, scheduled_last_start_date=TODAY,
        )
        assert _action(session, "16:59") is None


class TestMidnightCrossingWindow:
    """22:00-06:00: scheduled_end_time < scheduled_start_time."""

    def test_starts_when_start_time_reached(self):
        session = _session()
        assert _action(session, "22:00") == "start"

    def test_does_not_stop_immediately_after_starting(self):
        # This is the exact bug: "22:05" >= "06:00" is true lexicographically.
        session = _session(state=SessionState.ACTIVE, scheduled_last_start_date=TODAY)
        assert _action(session, "22:05") is None

    def test_does_not_stop_late_in_the_evening_of_start_day(self):
        session = _session(state=SessionState.ACTIVE, scheduled_last_start_date=TODAY)
        assert _action(session, "23:59") is None

    def test_does_not_stop_before_dawn_end_time(self):
        # Now past midnight: started "yesterday", still before the 06:00 end time.
        session = _session(state=SessionState.ACTIVE, scheduled_last_start_date=YESTERDAY)
        assert _action(session, "03:00") is None

    def test_stops_at_dawn_end_time(self):
        session = _session(state=SessionState.ACTIVE, scheduled_last_start_date=YESTERDAY)
        assert _action(session, "06:00") == "stop"

    def test_stops_after_dawn_end_time(self):
        session = _session(state=SessionState.ACTIVE, scheduled_last_start_date=YESTERDAY)
        assert _action(session, "10:00") == "stop"

    def test_does_not_restart_same_day_after_dawn_stop(self):
        # Already started "yesterday" and it's not yet today's start time —
        # must not re-trigger start.
        session = _session(scheduled_last_start_date=YESTERDAY)
        assert _action(session, "10:00") is None

    def test_restarts_tonight_after_being_stopped(self):
        # Session stopped at dawn and returned to SCHEDULED; last_start_date
        # is still "yesterday" relative to tonight's start.
        session = _session(state=SessionState.SCHEDULED, scheduled_last_start_date=YESTERDAY)
        assert _action(session, "22:00") == "start"


class TestDayFilterAndAlreadyStarted:
    def test_does_not_start_on_excluded_weekday(self):
        session = _session(scheduled_days=[3, 4, 5])  # WEEKDAY=1 not included
        assert _action(session, "22:00") is None

    def test_does_not_start_twice_same_day(self):
        session = _session(scheduled_last_start_date=TODAY)
        assert _action(session, "22:00") is None
