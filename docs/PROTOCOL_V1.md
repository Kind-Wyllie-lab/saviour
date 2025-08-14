## Habitat Control Protocol (HCP) v1

### Purpose
Defines a minimal, robust JSON message envelope and standard message types for controller ↔ module communication. Works over existing ZeroMQ sockets and Zeroconf discovery.

### Transport
- Commands (controller → modules): ZMQ PUB topic `cmd/<module_id>` or `cmd/all`
- Status/Responses (modules → controller): ZMQ PUB topic `status/<module_id>`
- Bulk data not covered (use Samba/NAS or separate channels).

### Envelope (common fields)
All messages must be UTF-8 JSON with these fields:

- proto (int): Protocol version. For this spec use 1.
- type (string): Message category. See Message Types.
- timestamp (float): Unix epoch seconds on the sender.
- from (string): Sender ID (`controller` or module ID like `camera_abcd`).

Additional per-direction fields:
- For commands (controller → module):
  - msg_id (int): Controller-issued unique ID (monotonic, 64-bit safe).
  - to (string): Target module ID or `all`.
  - command (string): Command name, lower_snake_case.
  - params (object): Command parameters (idempotent where possible).
- For responses/status (module → controller):
  - in_reply_to (int, optional): Echo of the original msg_id when replying to a command.
  - status (string, optional): ok | nack | error for ack/result types.
  - result (object, optional): Success payload.
  - error (object/string, optional): Error info. Recommended keys: code, message, details.

### Message types
- command (controller → module): Execute an action.
- ack (module → controller): Prompt acknowledgment that command was parsed/accepted.
- result (module → controller): Outcome of a command (start/complete/event).
- heartbeat (module → controller): Periodic liveness + health metrics.
- ptp_status (module → controller): PTP metrics snapshot or periodic update.
- recordings_list (module → controller): List of on-device recordings.
- recording_started | recording_stopped (module → controller): State changes.
- export_complete (module → controller): Export outcome.
- get_config | set_config (either direction as needed): Config exchange; responses follow ack/result pattern.

### Controller message ID rules
- Only the controller generates msg_id (monotonic counter; wrap at large max, e.g., 2^63-1).
- Modules never generate msg_id; they echo via in_reply_to.
- Commands should be idempotent (e.g., set_gain: 20 instead of increase_gain).

### Recommended timeouts and retries
- Controller waits for ack within ack_timeout_ms (default 500 ms). If not received, retry command up to ack_retries (default 2) with the same msg_id.
- After ack, controller waits for result up to result_timeout_ms (command-specific; default 5 s). Optionally extend for long-running tasks and rely on periodic status/events.
- Module should de-duplicate by in_reply_to and avoid executing the same command twice.

### Heartbeats
- Interval configured per module (default 30 s).
- Minimal fields in result for heartbeat:
  - cpu_temp, cpu_usage, memory_usage, disk_space (percent used or free, be consistent)
  - uptime
  - ptp4l_offset, ptp4l_freq, phc2sys_offset, phc2sys_freq
  - recording (bool), streaming (bool)

### Examples
Command (controller → module):

```json
{
  "proto": 1,
  "type": "command",
  "msg_id": 1057,
  "from": "controller",
  "to": "camera_abcd",
  "timestamp": 1723378123.125,
  "command": "start_recording",
  "params": {"experiment_name": "exp1", "duration_s": 60}
}
```

ACK (module → controller):

```json
{
  "proto": 1,
  "type": "ack",
  "in_reply_to": 1057,
  "from": "camera_abcd",
  "timestamp": 1723378123.142,
  "status": "ok"
}
```

Result/event (module → controller):

```json
{
  "proto": 1,
  "type": "result",
  "in_reply_to": 1057,
  "from": "camera_abcd",
  "timestamp": 1723378123.500,
  "status": "ok",
  "result": {"event": "recording_started", "filename": "rec/exp1_20250811_123456.mp4"}
}
```

Heartbeat (module → controller):

```json
{
  "proto": 1,
  "type": "heartbeat",
  "from": "camera_abcd",
  "timestamp": 1723378123.750,
  "result": {
    "cpu_temp": 51.2,
    "cpu_usage": 14.7,
    "memory_usage": 38.2,
    "disk_space": 72.1,
    "uptime": 12345.6,
    "ptp4l_offset": -22,
    "ptp4l_freq": 120,
    "phc2sys_offset": -18,
    "phc2sys_freq": 85,
    "recording": false,
    "streaming": false
  }
}
```

NACK (parse/validation failure):

```json
{
  "proto": 1,
  "type": "ack",
  "in_reply_to": 1057,
  "from": "camera_abcd",
  "timestamp": 1723378123.140,
  "status": "nack",
  "error": {"code": "invalid_params", "message": "duration_s must be > 0"}
}
```

### Validation
- All receivers validate:
  - proto ∈ supported set (currently {1}).
  - Required fields per type.
  - Types of fields (msg_id int, timestamp number, etc.).
- On validation failure:
  - Modules send ack with status: nack and an error.
  - Controller logs and may retry or surface error to UI.

### Versioning
- proto increments for breaking changes only.
- Non-breaking additions (new status fields or optional params) do not change proto.
- If unsupported version received, reply with NACK error.code = "unsupported_protocol" and include result.supported = [1] (optional).

### Backward compatibility (transition plan)
- Controller may accept legacy status messages temporarily (without proto) while modules upgrade; treat as proto: 0 and convert best-effort.
- Once all modules adopt v1, disable legacy acceptance in config.

### Controller behaviors (guidance)
- Maintain pending_commands: msg_id → {module_id, command, params, deadline, retry_count}.
- Emit lifecycle events to UI: queued, acked, result_ok, result_error, timeout.
- Track per-module state (online/offline, health, PTP, activity) in a centralized manager.

### Security (optional, future)
- Consider HMAC over the JSON payload or a per-cluster pre-shared key.
- Avoid secrets in clear text fields.

### Reserved fields
- Reserved for future use: correlation_id, trace, meta.
