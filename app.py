from flask import Flask, render_template
from flask_socketio import SocketIO
from collections import deque
from datetime import datetime
import threading

app = Flask(__name__)
app.config["SECRET_KEY"] = "heartrate-hackathon"
socketio = SocketIO(app, cors_allowed_origins="*")

DATA_BUFFER = deque(maxlen=200)
BUFFER_LOCK = threading.Lock()

SPIKE_THRESHOLD = 120
spike_cooldown = False

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("heart_rate_data")
def handle_heart_rate(data):
    global spike_cooldown
    bpm = data.get("bpm")
    ts  = data.get("timestamp", datetime.now().strftime("%H:%M:%S"))
    if bpm is None:
        return
    reading = {"bpm": bpm, "timestamp": ts, "activity": data.get("activity")}
    with BUFFER_LOCK:
        DATA_BUFFER.append(reading)
    socketio.emit("new_reading", reading)
    if bpm >= SPIKE_THRESHOLD and not spike_cooldown:
        spike_cooldown = True
        socketio.emit("spike_alert", {"bpm": bpm, "timestamp": ts, "message": f"⚠️ Spike detected: {bpm} BPM"})
        threading.Timer(10, reset_cooldown).start()
    elif bpm < SPIKE_THRESHOLD:
        spike_cooldown = False

def reset_cooldown():
    global spike_cooldown
    spike_cooldown = False

@socketio.on("request_history")
def handle_history_request():
    with BUFFER_LOCK:
        history = list(DATA_BUFFER)
    socketio.emit("history_data", {"readings": history})

@socketio.on("ai_analysis")
def handle_ai_analysis(data):
    socketio.emit("ai_analysis", data)

# ── Forward chat messages to the browser ─────────────────────────────────────
@socketio.on("chat_message")
def handle_chat_message(data):
    socketio.emit("chat_message", data)

def get_buffer_snapshot():
    with BUFFER_LOCK:
        return list(DATA_BUFFER)

if __name__ == "__main__":
    print("🫀 Heart Rate Monitor server starting on http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)