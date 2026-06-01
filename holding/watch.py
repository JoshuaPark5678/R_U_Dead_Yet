"""
watch.py — Watch tracker
"""

import socket
import json
import time
import threading
import os
from datetime import datetime
import socketio

WATCH_IP = "172.20.10.2"
PORT = 9876
ANALYSIS_INTERVAL = 60  # seconds
FLASK_SERVER = "http://localhost:5000"

# ── SocketIO client ───────────────────────────────────────────────────────────
sio = socketio.Client()

def connect_to_flask():
    try:
        sio.connect(FLASK_SERVER)
        print(f"✅ Connected to Flask server at {FLASK_SERVER}")
    except Exception as e:
        print(f"❌ Could not connect to Flask: {e}")
        print("   Make sure app.py is running first.")

# ── Storage setup ─────────────────────────────────────────────────────────────
SESSION_ID  = datetime.now().strftime("%Y%m%d_%H%M%S")
DATA_DIR    = "sessions"
RAW_FILE    = os.path.join(DATA_DIR, f"{SESSION_ID}_raw.jsonl")
REPORT_FILE = os.path.join(DATA_DIR, f"{SESSION_ID}_reports.txt")
os.makedirs(DATA_DIR, exist_ok=True)

def save_raw(window: dict):
    with open(RAW_FILE, "a") as f:
        f.write(json.dumps(window) + "\n")

def save_report(minute: int, report: str, windows: list):
    with open(REPORT_FILE, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"MINUTE {minute}  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Windows collected: {len(windows)}\n")
        f.write('='*60 + "\n")
        f.write(report + "\n")

# ── Shared state ──────────────────────────────────────────────────────────────
windows_lock    = threading.Lock()
pending_windows = []
all_windows     = []

# ── Parse hr from new format: hr is a list e.g. [71.0] ───────────────────────
def parse_hr(w: dict):
    hr = w.get("hr", [])
    if not hr:
        return None, None, None
    mean = sum(hr) / len(hr)
    return mean, min(hr), max(hr)

# ── Format a window for display / AI prompt ───────────────────────────────────
def fmt_window(w: dict) -> str:
    mean, lo, hi = parse_hr(w)
    ac = w.get("accel", {})
    gy = w.get("gyro", {})
    ts_s = w.get("ts", 0) / 1000
    t = time.strftime("%H:%M:%S", time.localtime(ts_s))

    hr_str = (f"HR {mean:.0f} bpm" if mean else "HR: no reading")
    ac_str = (f"accel std={ac['std_mag']:.3f} peak={ac['peak_mag']:.2f}"
              if ac.get("std_mag") is not None else "accel: no data")
    gy_str = (f"gyro mean={gy['mean_mag']:.3f}"
              if gy.get("mean_mag") is not None else "")

    return f"[{t}] {w.get('activity','?'):8s} | {hr_str} | {ac_str} | {gy_str}"

# ── Emit a reading to Flask ───────────────────────────────────────────────────
def emit_reading(w: dict):
    mean, _, _ = parse_hr(w)
    if mean is None:
        return

    ts_s = w.get("ts", 0) / 1000
    ts   = time.strftime("%H:%M:%S", time.localtime(ts_s))

    sio.emit("heart_rate_data", {
        "bpm":        round(mean),
        "timestamp":  ts,
        "activity":   w.get("activity", "?"),
        "accel_std":  w.get("accel", {}).get("std_mag"),
        "accel_peak": w.get("accel", {}).get("peak_mag"),
    })

# ── Analysis loop ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a personal health and fitness assistant analyzing real-time smartwatch data.
Every minute you receive a batch of 1-second sensor windows from the user's watch.
Each window contains:
- Heart rate (bpm)
- Accelerometer stats: std_mag = movement intensity (near 0 = still), peak_mag = impact spike
- Gyroscope stats: wrist rotation intensity
- Activity label (STILL / WALKING / ACTIVE)

Respond in this exact format:

SUMMARY
2-3 sentences on what the person was doing and their vitals this minute.

INSIGHTS
- One notable observation (e.g. HR trend, long stillness, movement spike)
- Another if relevant

SUGGESTIONS
- One specific actionable suggestion based on the data

GOAL
One short motivational micro-goal for the next minute.

Be concise, friendly, and specific to the actual numbers."""

def analyze(windows: list, minute: int):
    print(f"\n{'='*60}")
    print(f"  MINUTE {minute} — analyzing {len(windows)} windows...")
    print('='*60)

    summary_lines = "\n".join(fmt_window(w) for w in windows)
    user_msg = (
        f"Minute {minute} data ({len(windows)} x 1s windows):\n\n{summary_lines}"
    )

    try:
        import anthropic
        claude = anthropic.Anthropic()
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}]
        )
        report = response.content[0].text
        print(report)
        save_report(minute, report, windows)

        if sio.connected:
            sio.emit("ai_analysis", {"analysis": report, "trigger": f"minute-{minute}"})

    except Exception as e:
        print(f"[Claude error] {e}")

def analysis_loop():
    minute = 1
    while True:
        time.sleep(ANALYSIS_INTERVAL)
        with windows_lock:
            batch = pending_windows.copy()
            pending_windows.clear()

        if batch:
            analyze(batch, minute)
            minute += 1
        else:
            print(f"\n[Minute {minute}] No data yet, skipping.")

# ── Main recv loop ────────────────────────────────────────────────────────────
def main():
    print(f"Session ID : {SESSION_ID}")
    print(f"Raw data   : {RAW_FILE}")
    print(f"Reports    : {REPORT_FILE}")

    connect_to_flask()

    threading.Thread(target=analysis_loop, daemon=True).start()
    print(f"Analysis fires every {ANALYSIS_INTERVAL}s.\n")

    print(f"Connecting to watch at {WATCH_IP}:{PORT}...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((WATCH_IP, PORT))
        print(f"✅ Connected to watch. Streaming...\n")

        buf = ""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                print("Watch disconnected.")
                break

            buf += chunk.decode("utf-8", errors="ignore")

            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    w = json.loads(line)
                    save_raw(w)
                    with windows_lock:
                        pending_windows.append(w)
                        all_windows.append(w)

                    emit_reading(w)
                    print(fmt_window(w))

                except json.JSONDecodeError:
                    print(f"[bad line] {line}")

if __name__ == "__main__":
    main()