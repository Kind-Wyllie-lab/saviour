# SAVIOUR — Synchronised Audio Video Input Output Recorder

**Purpose:** A modular, networked multi-sensor data capture platform for behavioural science labs. Built for the Kind Lab / SIDB / University of Edinburgh "Habitat" project, which studies up to 50 rodents in a large enclosure. Replaces manual, unsynchronised sensor work with autonomous, PTP-synchronised recording.

**Expected users:** Neuroscience researchers and lab technicians. Not a consumer product.

---

## Hardware

All nodes are Raspberry Pi 5s on a PoE switch (single cable = power + data). Module types: camera (Picamera2 / IMX500 AI), microphone (AudioMoth USV), TTL I/O, RFID. Storage via Samba share on the controller Pi or an external NAS.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.13 |
| Messaging | ZeroMQ (PUB/SUB) |
| Discovery | Zeroconf/mDNS |
| Time sync | PTP (ptp4l/phc2sys) |
| Web backend | Flask + SocketIO |
| Web frontend | React + JSX (Vite/esbuild) |
| Config | Layered JSON + dotenv |
| File export | Samba (CIFS) |
| ML (camera) | YOLOv11 rat detector |
| Some modules | Arduino (serial) |

---

## Architecture — Two Roles

**Controller** (`src/controller/`) — one Pi per system. Acts as PTP master, discovers modules via Zeroconf, issues commands, monitors health, coordinates recording sessions, queues exports, serves the React GUI.

**Module** (`src/modules/`) — one Pi per sensor. Acts as PTP slave. Registers via Zeroconf, connects to controller over ZMQ, executes commands (start/stop recording, export, reboot, etc.), sends periodic heartbeats.

Communication uses JSON messages over ZMQ PUB/SUB (see docs/PROTOCL_V1.md):
- Controller → module: topic `cmd/<module_id>` or `cmd/all`
- Module → controller: topic `status/<module_id>`

---

## Directory Structure (key parts)

```
src/
  controller/
    controller.py       # Abstract base Controller class
    web.py / frontend/  # Flask+SocketIO API + React GUI
    communication.py    # ZMQ hub
    network.py          # Zeroconf
    health.py / ptp.py / recording.py / export_queue.py
    examples/           # basic, habitat, apa, acoustic_startle controllers
  modules/
    module.py           # Abstract base Module class
    communication.py / network.py / health.py / ptp.py
    recording.py / export.py / command.py
    examples/           # camera, microphone, ttl, sound, rfid, apa_camera, arduino, template
docs/
  PROTOCOL_V1.md        # ZMQ message protocol spec
  CONFIG_STRUCTURE.md   # Config schema
  SRS.md                # System Requirements Spec
```

---

## Extending SAVIOUR

**New module type:** subclass `Module`, implement the three abstract recording methods + `configure_module_special`. Use `examples/template/` as a starting point.

**New experiment controller:** subclass `Controller`, implement `configure_controller` and `_register_special_socket_events`. See any `examples/` directory.
