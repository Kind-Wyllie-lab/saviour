"""sync_pulser: the PIO PWM (sysfs) buzzer path, against a fake sysfs tree."""

import os
import sys
import types
from unittest.mock import patch

import pytest

from src.modules import sync_pulser as sp


def _chip(root, n, pin, compatible=b"raspberrypi,pwm-pio-rp1\x00"):
    chip = root / f"pwmchip{n}"
    node = chip / "device" / "of_node"
    node.mkdir(parents=True)
    (node / "compatible").write_bytes(compatible)
    (node / "gpios").write_bytes(b"\x00\x00\x00\x2e" + pin.to_bytes(4, "big") + b"\x00" * 4)
    pwm = chip / "pwm0"
    pwm.mkdir()
    for name in ("enable", "period", "duty_cycle"):
        (pwm / name).write_text("0")
    return chip


def test_find_pio_pwm_matches_the_overlay_pin(tmp_path):
    _chip(tmp_path, 0, 4)
    want = _chip(tmp_path, 1, 17)
    _chip(tmp_path, 2, 17, compatible=b"brcm,bcm2835-pwm\x00")   # not PIO
    assert sp.find_pio_pwm(17, root=str(tmp_path)) == str(want)
    assert sp.find_pio_pwm(22, root=str(tmp_path)) is None


def test_ultrasonic_pulse_drives_the_pio_channel(tmp_path):
    chip = _chip(tmp_path, 0, 17)
    gz = types.SimpleNamespace(DigitalOutputDevice=None, PWMOutputDevice=None)
    with patch.object(sp, "find_pio_pwm", return_value=str(chip)), \
         patch.dict(sys.modules, {"gpiozero": gz}):
        p = sp.SyncPulser(17, None, drive="pwm", tone_hz=25_000)
        edge = p.pulse(1)
    assert p.pwm_backend == "pio"
    assert (chip / "pwm0" / "period").read_text() == "40000"
    assert (chip / "pwm0" / "duty_cycle").read_text() == "20000"
    # the fd-based writes land at offset 0 then 1: "1" then "0"
    assert (chip / "pwm0" / "enable").read_text().startswith("1")
    assert edge.off_ns > edge.on_ns
    p.close()
    assert p.pwm_backend is None and p._buzzer is None


def test_ultrasonic_tone_without_the_overlay_is_refused():
    with patch.object(sp, "find_pio_pwm", return_value=None):
        with pytest.raises(ValueError, match="dtoverlay=pwm-pio,gpio=17"):
            sp.SyncPulser(17, None, drive="pwm", tone_hz=25_000)
        sp.SyncPulser(17, None, drive="pwm", tone_hz=4_000)   # software is fine


def test_sysfs_pwm_exports_the_channel_when_missing(tmp_path):
    chip = tmp_path / "pwmchip0"
    chip.mkdir()
    real_write = sp._SysfsPwm._write

    def fake_write(path, value):
        if path.endswith("export"):
            os.makedirs(chip / "pwm0")
            for name in ("enable", "period", "duty_cycle"):
                (chip / "pwm0" / name).write_text("0")
            return
        real_write(path, value)

    with patch.object(sp._SysfsPwm, "_write", staticmethod(fake_write)):
        pwm = sp._SysfsPwm(str(chip), 25_000)
    assert (chip / "pwm0" / "period").read_text() == "40000"
    pwm.close()
