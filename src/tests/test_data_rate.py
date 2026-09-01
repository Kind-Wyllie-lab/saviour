"""Tests for src/shared/data_rate.py — per-module recording data-rate estimates."""

from src.shared.data_rate import (
    bytes_per_s_to_mb_per_min,
    estimate_recording_bytes_per_s,
    runway_minutes,
)


class TestEstimateRecordingBytesPerS:
    def test_camera_from_bitrate_and_fps(self):
        bps, note = estimate_recording_bytes_per_s(
            "loom_camera", {"camera": {"bitrate_mb": 2, "fps": 30}}
        )
        # 2 Mbit/s -> 250_000 B/s video, + 30 * 120 B/s CSV sidecar
        assert bps == 250_000 + 30 * 120
        assert "H.264" in note

    def test_camera_flat_dotted_config(self):
        bps, _ = estimate_recording_bytes_per_s(
            "camera", {"camera.bitrate_mb": 8, "camera.fps": 60}
        )
        assert bps == 1_000_000 + 60 * 120

    def test_camera_uses_defaults_when_keys_absent(self):
        bps, _ = estimate_recording_bytes_per_s("camera", {})
        assert bps == 250_000 + 30 * 120  # 2 Mbit / 30 fps defaults

    def test_habitat_camera_note_flags_motion_triggered(self):
        _, note = estimate_recording_bytes_per_s("habitat_camera", {})
        assert "motion-triggered" in note

    def test_microphone_scales_with_audiomoth_count(self):
        cfg = {"audiomoth": {"sample_rate": 192000},
               "audiomoth_labels": {"a": 1, "b": 2, "c": 3}}
        bps, note = estimate_recording_bytes_per_s("microphone", cfg)
        assert bps == 3 * 192000 * 2  # 3 mics, 16-bit mono
        assert "3× AudioMoth" in note

    def test_microphone_assumes_one_when_no_labels(self):
        bps, note = estimate_recording_bytes_per_s(
            "microphone", {"audiomoth": {"sample_rate": 192000}}
        )
        assert bps == 192000 * 2
        assert "assuming 1" in note

    def test_event_log_types_are_negligible_but_nonzero(self):
        for t in ("ttl", "rfid", "apa_arduino"):
            bps, note = estimate_recording_bytes_per_s(t, {})
            assert 0 < bps < 10_000
            assert "negligible" in note

    def test_unknown_type_returns_none(self):
        bps, note = estimate_recording_bytes_per_s("weather_station", {})
        assert bps is None
        assert "no estimate" in note

    def test_empty_type_returns_none(self):
        assert estimate_recording_bytes_per_s("", {})[0] is None
        assert estimate_recording_bytes_per_s(None, {})[0] is None

    def test_non_dict_config_does_not_raise(self):
        bps, _ = estimate_recording_bytes_per_s("camera", None)
        assert bps == 250_000 + 30 * 120


class TestConversions:
    def test_bytes_per_s_to_mb_per_min(self):
        assert bytes_per_s_to_mb_per_min(1_000_000) == 60.0
        assert bytes_per_s_to_mb_per_min(None) is None

    def test_runway_minutes(self):
        assert runway_minutes(600, 60) == 10.0

    def test_runway_none_when_rate_or_space_missing(self):
        assert runway_minutes(None, 60) is None
        assert runway_minutes(600, None) is None
        assert runway_minutes(600, 0) is None
        assert runway_minutes(0, 60) is None
