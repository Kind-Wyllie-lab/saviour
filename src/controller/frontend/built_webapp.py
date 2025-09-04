from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit
import os

app = Flask(__name__, static_folder="build")
socketio = SocketIO(app, cors_allowed_origins="*")

# Example modules
modules = {
    "pi1": {"id": "pi_df76", "name": "Pi 1", "status": "online", "temperature": 42},
    "pi2": {"id": "pi_yn81", "name": "Pi 2", "status": "offline", "temperature": None},
}

@socketio.on("get_modules")
def handle_get_modules():
    emit("modules_update", modules)

# Serve React app
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
