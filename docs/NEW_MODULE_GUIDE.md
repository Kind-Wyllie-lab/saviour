# Creating a New Module Type for SAVIOUR

This guide walks through adding a new hardware module type — a sensor, actuator, or any peripheral device that records data and reports to the controller. It assumes familiarity with Python but no prior knowledge of SAVIOUR internals.

---

## What a module is

A SAVIOUR module is a Raspberry Pi running a Python process that:

1. **Discovers** the controller over the PoE network via mDNS (Zeroconf).
2. **Receives commands** from the controller over ZeroMQ (start recording, stop recording, get config, etc.).
3. **Reports status** back to the controller over ZeroMQ (heartbeat, recording events, config).
4. **Records data** to `/var/lib/saviour/recordings/` and exports files to a Samba share on the controller.

Everything network-related, config-loading, health-reporting, export, and PTP time sync is handled by the base `Module` class. You only need to implement what is unique to your hardware.

---

## Files to create

```
src/modules/examples/<type>/
├── <type>_module.py     # Your module class
└── <type>_config.json   # Module-specific config defaults
```

`<type>` must be a single lowercase word with underscores (e.g. `weight_sensor`, `rfid`). It becomes:
- the module's type string in mDNS advertisements
- the prefix of its module ID (e.g. `weight_sensor_3a2f`)
- the directory/filename the systemd service looks for

---

## Step 1 — Copy the template

```bash
cp -r src/modules/examples/template src/modules/examples/weight_sensor
cd src/modules/examples/weight_sensor
mv template_module.py weight_sensor_module.py
mv template_config.json weight_sensor_config.json
```

---

## Step 2 — The module class skeleton

Open `weight_sensor_module.py`. The minimal required structure is:

```python
import sys, os, time
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check


class WeightSensorModule(Module):

    def __init__(self, module_type="weight_sensor"):
        super().__init__(module_type)

        # Load module-specific config on top of the base config
        self.config.load_module_config("weight_sensor_config.json")

        # Register any module-specific commands
        self.command.set_commands({
            "tare": self._tare,
            "get_reading": self._get_reading,
        })

        # Hardware init goes here
        self._sensor = None   # placeholder


    # ── Commands (called by controller over ZMQ) ─────────────────────────────

    @command()
    def _tare(self) -> dict:
        """Zero the scale."""
        # ... hardware call ...
        return {"result": "success"}

    @command()
    def _get_reading(self) -> dict:
        return {"grams": 0.0}   # replace with real hardware read


    # ── Config change handler ─────────────────────────────────────────────────

    def configure_module_special(self, updated_keys: Optional[list[str]]):
        """Called whenever a config value changes. React to hardware-relevant keys."""
        if "weight_sensor.sample_rate_hz" in (updated_keys or []):
            rate = self.config.get("weight_sensor.sample_rate_hz", 10)
            self.logger.info(f"Sample rate changed to {rate} Hz")
            # ... reconfigure hardware ...


    # ── Recording ─────────────────────────────────────────────────────────────

    def _start_new_recording(self) -> bool:
        # Open file, start hardware streaming
        return True

    def _start_next_recording_segment(self) -> bool:
        # Called periodically for segmented recordings
        return True

    def _stop_recording(self) -> bool:
        # Flush and close file
        return True


    # ── Readiness checks ─────────────────────────────────────────────────────

    @check()
    def _check_sensor_connected(self) -> tuple[bool, str]:
        if self._sensor is None:
            return False, "Sensor not initialised"
        return True, "Sensor connected"


def main():
    mod = WeightSensorModule()
    mod.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mod.stop()

if __name__ == "__main__":
    main()
```

### What `super().__init__()` does for you

- Creates `self.config`, `self.communication`, `self.health`, `self.ptp`, `self.export`, `self.recording`, `self.network`, `self.facade`.
- Registers the built-in commands: `start_recording`, `stop_recording`, `get_config`, `set_config`, `validate_readiness`, `shutdown`, `update_saviour`, `reset_config`, `start_export`.
- Sets up PTP (Precision Time Protocol) as a slave to the controller clock.

You only need to add what is specific to your hardware.

---

## Step 3 — The config file

`weight_sensor_config.json` holds defaults for keys that are unique to your module. Keys in the base config (`src/modules/config/base_config.json`) do not need to be repeated here.

```json
{
    "weight_sensor": {
        "sample_rate_hz": 10,
        "averaging_window": 5,
        "calibration_factor": 1.0
    }
}
```

### Config key naming rules

| Prefix | Meaning | User-editable via UI? |
|--------|---------|----------------------|
| No prefix | User-settable parameter | Yes |
| `_` prefix | Internal default (developer-controlled) | No — silently ignored on save |

Internal keys are re-applied from the module config on every startup even when `active_config.json` already exists, which guarantees they stay in sync with the codebase. Use them for things users should never change (codec names, internal buffer sizes, etc.).

```json
{
    "weight_sensor": {
        "sample_rate_hz": 10,
        "_protocol": "hx711"
    }
}
```

### Reading config values

```python
rate = self.config.get("weight_sensor.sample_rate_hz", 10)   # dot-separated path
cal  = self.config.get("weight_sensor.calibration_factor", 1.0)
```

Keys are accessed with dot notation regardless of nesting depth.

---

## Step 4 — Commands

Commands are Python methods decorated with `@command()`. The `Communication` class calls them automatically when the controller sends a matching ZMQ message.

```python
@command()
def _tare(self) -> dict:
    """Zero the scale. Returns success/error dict."""
    try:
        self._sensor.tare()
        return {"result": "success"}
    except Exception as e:
        return {"result": "error", "output": str(e)}
```

Rules:
- The method name (without leading `_`) becomes the command name: `_tare` → `"tare"`. Override with `@command(name="my_name")`.
- Return a `dict`. It is sent back to the controller as a `cmd_ack` payload.
- Do not block for more than a second or two. Long-running work (file I/O, network calls) should be spawned in a background thread, with a status message sent when done (see "Reporting back" below).

### Adding module-specific commands to the router

```python
# In __init__, after super().__init__():
self.command.set_commands({
    "tare":        self._tare,
    "get_reading": self._get_reading,
})
```

---

## Step 5 — Readiness checks

Before a recording session starts, the controller calls `validate_readiness()`. The base class runs a set of built-in checks (disk space, PTP sync, write access), then calls `_perform_module_specific_checks()` which runs every method decorated with `@check()`.

```python
@check()
def _check_sensor_connected(self) -> tuple[bool, str]:
    """Return (True, description) if OK, (False, reason) if not."""
    if self._sensor is None:
        return False, "Sensor not initialised"
    return True, "Sensor connected and responding"
```

Register your checks in `__init__`:

```python
self.module_checks = {
    self._check_sensor_connected,
}
```

---

## Step 6 — Recording

Three abstract methods must be implemented:

```python
def _start_new_recording(self) -> bool:
    """
    Called at the start of a new recording session.
    Open output file, start hardware, set self.is_recording = True.
    Return True on success.
    """
    session = self.recording.current_filename_prefix
    path = f"{self.facade.get_recording_folder()}/{session}.csv"
    self._out = open(path, "w")
    self.is_recording = True
    return True

def _start_next_recording_segment(self) -> bool:
    """
    Called periodically for segmented recordings (e.g. one file per hour).
    Close the current file and open a new one.
    """
    self._out.close()
    return self._start_new_recording()

def _stop_recording(self) -> bool:
    """
    Called when the controller sends stop_recording, or when the grace-
    period timer fires after a controller disconnect.
    Close file, stop hardware, set self.is_recording = False.
    Return True on success.
    """
    if self._out:
        self._out.close()
    self.is_recording = False
    return True
```

The recording folder and filename prefix are managed by the `Recording` class. Always write files under `self.facade.get_recording_folder()`. Files written there are staged for Samba export automatically.

---

## Step 7 — Reporting back to the controller

To report the outcome of a long-running command, send a `cmd_ack` status message:

```python
@command()
def _run_calibration(self) -> dict:
    """Start calibration in the background; report result when done."""
    def _do_cal():
        ok = self._sensor.calibrate()
        self.communication.send_status({
            "type": "cmd_ack",
            "command": "run_calibration",
            "result": "success" if ok else "error",
            "output": "Calibration complete" if ok else "Calibration failed",
        })
    import threading
    threading.Thread(target=_do_cal, daemon=True).start()
    return {"result": "started"}
```

The controller's `web.py` routes `cmd_ack` messages to the appropriate frontend handler.

---

## Step 8 — Register the type in `saviour-config`

Open `saviour-config` (at the project root) and add your type to the `select_module_type()` menu:

```bash
select_module_type() {
    wt --title "Module Type" \
        --menu "\nWhat type of module is this device?" \
        $H $W 9 \                          # ← increment count
        "camera"         "Basic camera" \
        ...
        "weight_sensor"  "HX711 weight sensor"   # ← add this line
}
```

If your module needs system-level setup (udev rules, extra packages, config files), add a `configure_weight_sensor()` function and call it from `run_configuration()`:

```bash
configure_weight_sensor() {
    progress "Installing weight sensor dependencies…"
    if ! is_installed "python3-smbus"; then
        run_logged apt-get install -y python3-smbus
    fi
}

# In run_configuration():
[ "$DEVICE_TYPE" = "weight_sensor" ] && configure_weight_sensor
```

`saviour-config` generates a systemd service unit that runs:

```
/usr/local/src/saviour/env/bin/python weight_sensor_module.py
```

from `src/modules/examples/weight_sensor/`. The `main()` function at the bottom of your module file is the entry point.

---

## Step 9 — Install on a device

```bash
# On the Pi you want to use as the module
sudo saviour-config
# Choose: module → weight_sensor
# Reboot when prompted
```

The service will start automatically on boot. Check its status with:

```bash
journalctl -u saviour.service -f
```

---

## Step 10 — Running locally for development

You can run the module directly without going through the systemd setup:

```bash
cd /usr/local/src/saviour
source env/bin/activate
cd src/modules/examples/weight_sensor
PYTHONPATH=/usr/local/src/saviour/src python weight_sensor_module.py
```

To test without hardware:
- Mock `self._sensor` in `__init__` with a stub that returns canned values.
- There is no need for a real controller — the module will log "waiting for controller" and enter its discovery loop. Commands can be sent directly over ZMQ for isolated testing.

The module will fail PTP checks if `ptp4l` is not running. If you want to skip that readiness check during development, temporarily return `(True, "PTP skipped in dev")` from `_check_ptp` in the base class, or set `module.ptp_offset_threshold_us` to a very large value in your test config.

---

## Common pitfalls

**Blocking the command thread.** The ZMQ command listener is single-threaded. Any `@command()` method that blocks for more than a second delays all subsequent commands. Offload I/O and hardware calls to background threads; send a `cmd_ack` when done.

**Forgetting to set `self.is_recording`.**  The health heartbeat and readiness checks read this flag. If you do not set it in `_start_new_recording()` and `_stop_recording()`, the controller will not know the recording state.

**Storing a hardware handle before `start()` is called.** Hardware init in `__init__` runs before network discovery. If your hardware driver raises when the device is absent, defer the open to `_start_new_recording()` (or a lazy getter) instead of `__init__`.

**Using `_`-prefixed keys as user settings.** Private keys are silently stripped on every inbound `set_config` call from the frontend. If users need to change a value, do not prefix it with `_`.

**Reusing a stale PulseAudio/PipeWire device ID after reconfiguring audio hardware.** If your module interacts with audio devices, re-discover the device after any configuration change that renames it (see the AudioMoth gotcha in `CLAUDE.md`).

**Not returning a dict from `@command()`.** The command router expects a `dict` return value. Returning `None` or a primitive will cause a serialisation error in the ZMQ send path. Always return at least `{"result": "success"}`.

---

## Quick reference

| Thing you want to do | How |
|----------------------|-----|
| Read a config value | `self.config.get("section.key", default)` |
| React to a config change | Implement `configure_module_special(updated_keys)` |
| Add a remotely-callable command | Decorate with `@command()`, register in `self.command.set_commands({...})` |
| Add a readiness check | Decorate with `@check()`, add to `self.module_checks` |
| Write a recording file | Write under `self.facade.get_recording_folder()` |
| Report an async result | `self.communication.send_status({"type": "cmd_ack", ...})` |
| Log a message | `self.logger.info(...)` / `.warning(...)` / `.error(...)` |
| Get the current session name | `self.recording.current_filename_prefix` |
| Check if connected to controller | `self.is_connected_to_controller` |
