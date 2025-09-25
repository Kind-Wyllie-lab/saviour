from flask import Flask
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Example Raspberry Pi modules
modules = {
    "pi1": {"id": "pi_df76", "name": "Pi 1", "status": "online", "temperature": 42},
    "pi2": {"id": "pi_yn81", "name": "Pi 2", "status": "offline", "temperature": None},
}

# Event: client asks for modules
@socketio.on("get_modules")
def handle_get_modules():
    emit("modules_update", modules)  # send back initial snapshot

# Example: broadcast updates when a Pi changes
def update_module(module_id, status):
    modules[module_id]["status"] = status
    socketio.emit("modules_update", modules)  # broadcast to all clients

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", debug=True)
