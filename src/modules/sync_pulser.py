"""
GPIO driver for the A/V sync test rig: a piezo buzzer and/or an LED fired
together, with the edge stamped on the PTP-disciplined wall clock.

Wiring and usage: docs/AV_SYNC_TEST.md. Config (microphone module):

    sync_pulse.buzzer_pin   BCM pin driving the buzzer (null = none)
    sync_pulse.led_pin      BCM pin driving the LED (null = none; may equal
                            buzzer_pin when both hang off one transistor)
    sync_pulse.drive        "dc"  -- active buzzer (own oscillator): pin high
                            "pwm" -- passive piezo: square wave at tone_hz
    sync_pulse.tone_hz      tone for "pwm" drive

"pwm" uses the pin's PIO PWM channel when there is one (Pi 5,
`dtoverlay=pwm-pio,gpio=<pin>` in /boot/firmware/config.txt): hardware-timed,
any frequency, so the buzz can be ultrasonic (25 kHz: silent to people,
~50 dB over the noise on an AudioMoth from a PS1240). Without the overlay it
falls back to gpiozero's software PWM, which can't go above 10 kHz.
"""

from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass

SOFTWARE_PWM_MAX_HZ = 10_000
_PWM_CLASS = "/sys/class/pwm"


def find_pio_pwm(pin: int, root: str = _PWM_CLASS) -> str | None:
    """The pwmchip directory whose PIO PWM drives BCM `pin`, or None. The
    pwm-pio overlay's device-tree node names its pin in `gpios`
    (<phandle pin flags>, big-endian u32 cells)."""
    for chip in sorted(glob.glob(os.path.join(root, "pwmchip*"))):
        node = os.path.join(chip, "device", "of_node")
        try:
            with open(os.path.join(node, "compatible"), "rb") as f:
                if b"pwm-pio" not in f.read():
                    continue
            with open(os.path.join(node, "gpios"), "rb") as f:
                cells = f.read()
        except OSError:
            continue
        if len(cells) >= 8 and int.from_bytes(cells[4:8], "big") == pin:
            return chip
    return None


class _SysfsPwm:
    """One sysfs PWM channel as a buzzer: square wave at `tone_hz`, 50 %
    duty, switched by writing the channel's `enable` (an open fd, so the
    stamped write is a single syscall)."""

    def __init__(self, chip: str, tone_hz: float):
        self.path = os.path.join(chip, "pwm0")
        if not os.path.isdir(self.path):
            self._write(os.path.join(chip, "export"), "0")
            for _ in range(50):       # udev makes the files a moment later
                if os.path.exists(os.path.join(self.path, "enable")):
                    break
                time.sleep(0.01)
        period = int(round(1e9 / tone_hz))
        self._write(os.path.join(self.path, "enable"), "0")
        self._write(os.path.join(self.path, "duty_cycle"), "0")
        self._write(os.path.join(self.path, "period"), str(period))
        self._write(os.path.join(self.path, "duty_cycle"), str(period // 2))
        self._enable = os.open(os.path.join(self.path, "enable"), os.O_WRONLY)

    @staticmethod
    def _write(path: str, value: str) -> None:
        with open(path, "w") as f:
            f.write(value)

    @property
    def value(self) -> int:
        return 0

    @value.setter
    def value(self, v) -> None:
        os.write(self._enable, b"1" if v else b"0")

    def off(self) -> None:
        self.value = 0

    def close(self) -> None:
        if self._enable is not None:
            os.close(self._enable)
            self._enable = None


@dataclass
class PulseEdge:
    on_ns: int  # best estimate of the on edge (midpoint of the bracket)
    on_spread_ns: int  # width of the time.time_ns() bracket around the writes
    off_ns: int


class SyncPulser:
    """Drives the buzzer/LED pins. gpiozero is imported lazily so modules
    without the rig (and tests) never touch GPIO."""

    def __init__(
        self,
        buzzer_pin: int | None,
        led_pin: int | None,
        drive: str = "dc",
        tone_hz: float = 4000.0,
    ):
        if buzzer_pin is None and led_pin is None:
            raise ValueError("sync_pulse: neither buzzer_pin nor led_pin is configured")
        if drive not in ("dc", "pwm"):
            raise ValueError(f"sync_pulse.drive must be 'dc' or 'pwm', not {drive!r}")
        self.buzzer_pin = buzzer_pin
        self.led_pin = led_pin if led_pin != buzzer_pin else None
        self.drive = drive
        self.tone_hz = float(tone_hz)
        if (drive == "pwm" and buzzer_pin is not None
                and self.tone_hz > SOFTWARE_PWM_MAX_HZ
                and find_pio_pwm(buzzer_pin) is None):
            raise ValueError(
                f"sync_pulse.tone_hz {self.tone_hz:.0f} needs a hardware PWM "
                f"channel on GPIO{buzzer_pin}: add "
                f"'dtoverlay=pwm-pio,gpio={buzzer_pin}' to "
                f"/boot/firmware/config.txt and reboot (software PWM stops at "
                f"{SOFTWARE_PWM_MAX_HZ} Hz)")
        self._buzzer = None
        self._led = None
        # "pio" (hardware-timed PWM channel), "software" (gpiozero) or None
        self.pwm_backend: str | None = None

    def _open(self) -> None:
        if self._buzzer is not None or self._led is not None:
            return
        import gpiozero

        if self.buzzer_pin is not None:
            chip = find_pio_pwm(self.buzzer_pin) if self.drive == "pwm" else None
            if chip is not None:
                self._buzzer = _SysfsPwm(chip, self.tone_hz)
                self.pwm_backend = "pio"
            elif self.drive == "pwm":
                self._buzzer = gpiozero.PWMOutputDevice(
                    self.buzzer_pin, frequency=self.tone_hz, initial_value=0
                )
                self.pwm_backend = "software"
            else:
                self._buzzer = gpiozero.DigitalOutputDevice(
                    self.buzzer_pin, initial_value=False
                )
        if self.led_pin is not None:
            self._led = gpiozero.DigitalOutputDevice(self.led_pin, initial_value=False)

    def _set(self, on: bool) -> None:
        # LED first: it is the faster of the two and the camera-side reference.
        if self._led is not None:
            self._led.value = 1 if on else 0
        if isinstance(self._buzzer, _SysfsPwm):
            self._buzzer.value = 1 if on else 0
        elif self._buzzer is not None:
            self._buzzer.value = (0.5 if self.drive == "pwm" else 1) if on else 0

    def pulse(self, duration_ms: float) -> PulseEdge:
        self._open()
        t0 = time.time_ns()
        self._set(True)
        t1 = time.time_ns()
        time.sleep(max(0.0, duration_ms) / 1000.0)
        self._set(False)
        t2 = time.time_ns()
        return PulseEdge(on_ns=(t0 + t1) // 2, on_spread_ns=t1 - t0, off_ns=t2)

    def close(self) -> None:
        for dev in (self._buzzer, self._led):
            if dev is not None:
                try:
                    dev.off()
                    dev.close()
                except Exception:
                    pass
        self._buzzer = self._led = None
        self.pwm_backend = None
