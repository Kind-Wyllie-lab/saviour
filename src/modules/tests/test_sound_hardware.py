"""Tests for src/modules/variants/sound/sound_hardware.py (no HAT needed)."""

import subprocess
from unittest.mock import MagicMock

import numpy as np
import soundfile

from src.modules.variants.sound import sound_hardware as hw

HDMI = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 1 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1
                      vc4-hdmi-1
"""
DAC8X = HDMI + """ 2 [sndrpihifiberry]: simple-card - snd_rpi_hifiberry_dac8x
                      snd_rpi_hifiberry_dac8x
"""
DACPRO = HDMI + """ 2 [RPiDACPro      ]: RPi-DAC-Pro - RPi DAC Pro
                      RPi DAC Pro
"""


def _aplay(channels_line):
    return MagicMock(return_value=subprocess.CompletedProcess(
        [], 0, stdout=b"", stderr=f"HW Params of device:\n{channels_line}\n".encode()))


def test_parse_cards():
    cards = hw.parse_asound_cards(DAC8X)
    assert [c["id"] for c in cards] == ["vc4hdmi0", "vc4hdmi1", "sndrpihifiberry"]
    assert cards[2]["index"] == 2 and "dac8x" in cards[2]["longname"]


def test_dac8x_four_outputs_from_alsa():
    d = hw.detect(DAC8X, run=_aplay("CHANNELS: [2 8]"))
    assert d["present"] and d["board"] == "HiFiBerry DAC8x"
    assert (d["channels"], d["outputs"], d["channels_from"]) == (8, 4, "alsa")
    assert d["device"] == "hw:CARD=sndrpihifiberry,DEV=0"
    assert d["presence_verified"] is False and "can't confirm" in d["detail"]


def test_dac_pro_one_output():
    d = hw.detect(DACPRO, run=_aplay("CHANNELS: 2"))
    assert d["board"] == "Raspberry Pi DAC Pro" and d["outputs"] == 1
    assert d["presence_verified"] is True


def test_board_count_when_alsa_cant_say():
    d = hw.detect(DAC8X, run=None)
    assert (d["channels"], d["outputs"], d["channels_from"]) == (8, 4, "board")
    failing = MagicMock(side_effect=OSError("busy"))
    assert hw.detect(DACPRO, run=failing)["outputs"] == 1


def test_only_hdmi_is_no_card():
    d = hw.detect(HDMI, run=None)
    assert d["present"] is False and d["outputs"] == 0


def test_unknown_card_defaults_to_stereo():
    text = HDMI + " 3 [Device         ]: USB-Audio - USB Audio Device\n    x\n"
    d = hw.detect(text, run=None)
    assert d["present"] and d["card_id"] == "Device" and d["outputs"] == 1
    assert d["channels_from"] == "default"


def test_parse_channels():
    assert hw.parse_channels("CHANNELS: [2 8]") == (2, 8)
    assert hw.parse_channels("FORMAT: S16\nCHANNELS: 2\n") == (2, 2)
    assert hw.parse_channels("nothing") is None


def test_sound_files_lengths(tmp_path):
    soundfile.write(tmp_path / "tone.wav", np.zeros(24_000, dtype=np.float32), 48_000)
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / ".hidden.wav").write_bytes(b"")
    (tmp_path / "folder").mkdir()
    assert hw.sound_files(str(tmp_path)) == {"tone.wav": 0.5}
    assert hw.sound_files(str(tmp_path / "missing")) == {}
