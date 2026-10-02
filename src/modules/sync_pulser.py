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
"""

from __future__ import annotations

import time
from dataclasses import dataclass


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
        self._buzzer = None
        self._led = None

    def _open(self) -> None:
        if self._buzzer is not None or self._led is not None:
            return
        import gpiozero

        if self.buzzer_pin is not None:
            if self.drive == "pwm":
                self._buzzer = gpiozero.PWMOutputDevice(
                    self.buzzer_pin, frequency=self.tone_hz, initial_value=0
                )
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
        if self._buzzer is not None:
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
