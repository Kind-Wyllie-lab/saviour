"""
Tests for src/modules/variants/sound/sound_module.py.

SoundModule.__init__ probes real ALSA hardware, so tests construct via
SoundModule.__new__(SoundModule) and set only what the method under test
reads (same pattern as test_camera_base.py). In scope: the HiFiBerry
detection / hardware_fault wiring, the missing-sounds-dir guard, and the
no-sound-selected guard on _play_sound.
"""

from unittest.mock import MagicMock, mock_open, patch

from src.modules.variants.sound.sound_module import SoundModule

_MOD = "src.modules.variants.sound.sound_module"


def _make_sound(**attrs) -> SoundModule:
    m = SoundModule.__new__(SoundModule)
    m.logger = MagicMock()
    m._sounds_dir = "/nonexistent/sounds"
    m.hardware_fault = None
    m.sound_to_play = None
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestGetAvailableSounds:
    def test_missing_sounds_dir_returns_empty_list_not_crash(self):
        m = _make_sound()
        assert m._get_available_sounds() == []
        m.logger.warning.assert_called_once()

    def test_lists_only_files(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"x")
        (tmp_path / "b.wav").write_bytes(b"x")
        (tmp_path / "subdir").mkdir()
        m = _make_sound(_sounds_dir=str(tmp_path))
        assert sorted(m._get_available_sounds()) == ["a.wav", "b.wav"]


class TestDetectHifiberry:
    def test_found_in_proc_asound_cards(self):
        cards = " 0 [sndrpihifiberry]: HiFiBerry DAC+ - snd_rpi_hifiberry_dacplus\n"
        m = _make_sound()
        with patch(f"{_MOD}.open", mock_open(read_data=cards)):
            found, detail = m._detect_hifiberry()
        assert found is True
        assert "hifiberry" in detail.lower()

    def test_falls_back_to_aplay_when_proc_absent(self):
        m = _make_sound()
        aplay = MagicMock(stdout="card 2: sndrpihifiberry [HiFiBerry DAC+]")
        with patch(f"{_MOD}.open", side_effect=OSError), \
             patch(f"{_MOD}.subprocess.run", return_value=aplay):
            found, detail = m._detect_hifiberry()
        assert found is True and detail == "aplay -l"

    def test_not_found_anywhere(self):
        m = _make_sound()
        aplay = MagicMock(stdout="card 0: Headphones [bcm2835 Headphones]")
        with patch(f"{_MOD}.open", mock_open(read_data="card 0: Headphones\n")), \
             patch(f"{_MOD}.subprocess.run", return_value=aplay):
            found, _ = m._detect_hifiberry()
        assert found is False

    def test_aplay_missing_is_not_fatal(self):
        m = _make_sound()
        with patch(f"{_MOD}.open", side_effect=OSError), \
             patch(f"{_MOD}.subprocess.run", side_effect=FileNotFoundError):
            found, _ = m._detect_hifiberry()
        assert found is False


class TestCheckHifiberry:
    def test_present_clears_hardware_fault(self):
        m = _make_sound(hardware_fault="stale")
        with patch.object(
            m, "_detect_hifiberry", return_value=(True, "sndrpihifiberry")
        ):
            ok, msg = m._check_hifiberry()
        assert ok is True
        assert m.hardware_fault is None
        assert "HiFiBerry" in msg

    def test_absent_sets_hardware_fault_and_blocks_readiness(self):
        m = _make_sound()
        with patch.object(m, "_detect_hifiberry", return_value=(False, "")):
            ok, msg = m._check_hifiberry()
        assert ok is False
        assert m.hardware_fault == msg == "No HiFiBerry sound card detected"


class TestPlaySoundGuard:
    def test_returns_error_when_no_sound_selected(self):
        m = _make_sound(sound_to_play=None)
        assert m._play_sound() == {
            "result": "error", "message": "no sound file available",
        }
