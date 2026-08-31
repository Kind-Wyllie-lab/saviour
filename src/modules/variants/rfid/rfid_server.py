"""
LID650/665 RS485 RFID bus – web interface.

Usage
-----
    python rfid_server.py                   # auto-detect port, serve on :5000
    python rfid_server.py --port /dev/ttyUSB0 --baud 19200 --web-port 8080
    python rfid_server.py --no-autoconnect  # open UI first, connect manually

The web UI is served at http://localhost:<web-port>/
Real-time updates are pushed over a Socket.IO WebSocket.
"""

import argparse
import logging
import threading
import time

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from rfid_bus import BAUD_DEFAULT, RS485Bus, UnitInfo
from rfid_db import RFIDDatabase

log = logging.getLogger(__name__)

# ── Application setup ─────────────────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rfid-lid665-monitor'

# threading async_mode works without eventlet/gevent — straightforward for
# integration into larger projects later.
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

bus: RS485Bus    = RS485Bus()
db:  RFIDDatabase = None      # set in main() after arg parsing


# ── Bus → SocketIO bridge ─────────────────────────────────────────────────────

def _on_unit_found(unit: UnitInfo) -> None:
    db.upsert_unit(unit.address, unit.firmware_version,
                   unit.serial_number or '', unit.last_seen)
    socketio.emit('unit_update', unit.to_dict())


def _on_transponder_read(unit_addr: int, tid: bytes,
                         type_name: str, ts: float) -> None:
    tid_hex = tid.hex().upper()
    read_id = db.add_read(unit_addr, tid_hex, type_name, ts)
    db.upsert_unit(unit_addr, last_seen=ts)
    socketio.emit('transponder_read', {
        'id':              read_id,
        'unit_address':    unit_addr,
        'unit_address_hex': f'{unit_addr:02X}h',
        'transponder_id':  tid_hex,
        'transponder_type': type_name,
        'packet_ts':       ts,
        'uploaded':        False,
    })


def _on_bus_status(connected: bool, port: str) -> None:
    socketio.emit('bus_status', {
        'connected': connected,
        'port':      port,
        'baud':      bus.baud,
    })


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    return jsonify({
        'connected':       bus.is_connected,
        'port':            bus.port,
        'baud':            bus.baud,
        'available_ports': bus.find_ports(),
        'unit_count':      len(bus.units),
        'pending_reads':   db.pending_count() if db else 0,
    })


@app.route('/api/units')
def api_units():
    return jsonify([u.to_dict() for u in bus.units.values()])


@app.route('/api/reads')
def api_reads():
    limit   = int(request.args.get('limit', 200))
    pending = request.args.get('pending') == '1'
    return jsonify(db.get_reads(limit=limit, pending_only=pending))


@app.route('/api/connect', methods=['POST'])
def api_connect():
    body = request.get_json(silent=True) or {}
    port = body.get('port', '').strip()
    baud = int(body.get('baud', BAUD_DEFAULT))
    if bus.is_connected:
        bus.disconnect()
    ok = bus.connect(port=port or '', baud=baud)
    return jsonify({
        'ok':        ok,
        'port':      bus.port,
        'baud':      bus.baud,
        'connected': bus.is_connected,
    })


@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    bus.disconnect()
    return jsonify({'ok': True})


@app.route('/api/scan', methods=['POST'])
def api_scan():
    if not bus.is_connected:
        return jsonify({'ok': False, 'error': 'Not connected'}), 400
    bus.scan_bus()
    return jsonify({'ok': True})


@app.route('/api/units/<int:addr>/serial', methods=['POST'])
def api_query_serial(addr: int):
    """Manually request the serial number for one unit."""
    if not bus.is_connected:
        return jsonify({'ok': False, 'error': 'Not connected'}), 400
    bus.get_serial_number(addr)
    return jsonify({'ok': True})


@app.route('/api/reads/<int:read_id>/mark-uploaded', methods=['POST'])
def api_mark_uploaded(read_id: int):
    db.mark_uploaded(read_id)
    return jsonify({'ok': True})


@app.route('/api/reads/mark-all-uploaded', methods=['POST'])
def api_mark_all_uploaded():
    reads = db.get_reads(limit=100_000, pending_only=True)
    db.mark_many_uploaded([r['id'] for r in reads])
    return jsonify({'ok': True, 'count': len(reads)})


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on('connect')
def on_ws_connect():
    log.debug('WebSocket client connected')
    socketio.emit('bus_status', {
        'connected': bus.is_connected,
        'port':      bus.port,
        'baud':      bus.baud,
    })
    # Push current unit list so the page is immediately populated
    for unit in bus.units.values():
        socketio.emit('unit_update', unit.to_dict())


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global db

    parser = argparse.ArgumentParser(
        description='LID650/665 RS485 RFID bus web monitor')
    parser.add_argument('--web-port', type=int, default=5000,
                        help='HTTP/WS port (default 5000)')
    parser.add_argument('--port', default='',
                        help='Serial port (auto-detect if omitted)')
    parser.add_argument('--baud', type=int, default=BAUD_DEFAULT,
                        help=f'Baud rate (default {BAUD_DEFAULT})')
    parser.add_argument('--db', default='rfid_data.db',
                        help='SQLite database file (default rfid_data.db)')
    parser.add_argument('--no-autoconnect', action='store_true',
                        help='Do not connect to the bus on startup')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    db = RFIDDatabase(args.db)
    log.info('Database: %s', args.db)

    bus.on_unit_found       = _on_unit_found
    bus.on_transponder_read = _on_transponder_read
    bus.on_bus_status       = _on_bus_status

    if not args.no_autoconnect:
        if bus.connect(port=args.port, baud=args.baud):
            # Brief pause for units to send startup messages, then scan
            threading.Timer(1.5, bus.scan_bus).start()

    url = f'http://localhost:{args.web_port}'
    print(f'\n  RFID Bus Monitor  →  {url}\n')
    socketio.run(app, host='0.0.0.0', port=args.web_port,
                 debug=args.debug, use_reloader=False)


if __name__ == '__main__':
    main()
