import socket
import json
import time
import threading
import os
from datetime import datetime
from openai import OpenAI
import socketio as socketio_client

WATCH_IP = "172.20.10.2"
PORT = 9876
ANALYSIS_INTERVAL = 30
FLASK_SERVER = "http://localhost:5000"

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise ValueError(
        "GROQ_API_KEY environment variable is not set. "
        "Please set it before running this script.\n"
        "Example (PowerShell): $env:GROQ_API_KEY='your-api-key-here'"
    )

client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_api_key)

MOVEMENT_STD_THRESHOLD = 0.15
MOVEMENT_PEAK_THRESHOLD = 0.9
GYRO_THRESHOLD = 0.08
MOVEMENT_WINDOW_RATIO = 0.4

sio = socketio_client.Client()

def connect_to_flask():
    try:
        sio.connect(FLASK_SERVER)
        print(f"✅ Connected to Flask server at {FLASK_SERVER}")
    except Exception as e:
        print(f"❌ Could not connect to Flask: {e}")
        print("   Make sure app.py is running first.")

def get_hr_values(window: dict) -> list:
    hr = window.get("hr")
    if isinstance(hr, list):
        return [float(x) for x in hr if x is not None]
    if isinstance(hr, dict):
        return [x for x in (hr.get("mean"), hr.get("min"), hr.get("max")) if x is not None]
    return []

def emit_reading(w: dict):
    hr_values = get_hr_values(w)
    if not hr_values:
        return
    mean = sum(hr_values) / len(hr_values)
    ts_s = w.get("ts", 0) / 1000
    ts   = time.strftime("%H:%M:%S", time.localtime(ts_s))
    try:
        sio.emit("heart_rate_data", {
            "bpm":        round(mean),
            "timestamp":  ts,
            "activity":   w.get("activity", "?"),
            "accel_std":  w.get("accel", {}).get("std_mag"),
            "accel_peak": w.get("accel", {}).get("peak_mag"),
        })
    except Exception:
        pass

# ── Emit chat message to Flask ────────────────────────────────────────────────
def emit_chat(w: dict):
    try:
        sio.emit("chat_message", {
            "user": w.get("user", ""),
            "ai":   w.get("ai", ""),
            "ts":   w.get("ts", 0)
        })
    except Exception:
        pass

SESSION_ID   = datetime.now().strftime("%Y%m%d_%H%M%S")
DATA_DIR     = "sessions"
RAW_FILE     = os.path.join(DATA_DIR, f"{SESSION_ID}_raw.jsonl")
REPORT_FILE  = os.path.join(DATA_DIR, f"{SESSION_ID}_reports.txt")
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

windows_lock    = threading.Lock()
pending_windows = []

DISPLAY_SYSTEM_PROMPT = """You are a personal health and fitness assistant analyzing real-time smartwatch data.
Every 30 seconds you receive a batch of 1-second sensor windows from the user's watch.
Each window contains:
- Heart rate samples in a list
- Accelerometer stats: mean_mag, std_mag, peak_mag
- Gyroscope stats: mean_mag, max_mag
- Activity label (STILL / WALKING / ACTIVE)

Respond in this exact format:

SUGGESTIONS
- One specific actionable suggestion based on the data

(new line)

GOAL
One short motivational micro-goal for the next minute.

Be concise, friendly, and specific to the actual numbers."""

def fmt_window(w: dict) -> str:
    hr_values = get_hr_values(w)
    ac = w.get("accel", {})
    gy = w.get("gyro", {})
    ts_s = w.get("ts", 0) / 1000
    t = time.strftime("%H:%M:%S", time.localtime(ts_s))
    if hr_values:
        hr_mean = sum(hr_values) / len(hr_values)
        hr_str = f"HR mean={hr_mean:.0f} min={min(hr_values):.0f} max={max(hr_values):.0f} bpm"
    else:
        hr_str = "HR: no reading"
    ac_str = (f"accel std={ac['std_mag']:.3f} peak={ac['peak_mag']:.2f}"
              if ac.get("std_mag") is not None else "accel: no data")
    gy_str = (f"gyro mean={gy['mean_mag']:.3f}"
              if gy.get("mean_mag") is not None else "")
    return f"[{t}] {w.get('activity','?'):8s} | {hr_str} | {ac_str} | {gy_str}"

def window_has_movement(window: dict) -> bool:
    activity = window.get("activity", "").upper()
    ac = window.get("accel", {}) or {}
    gy = window.get("gyro", {}) or {}
    if activity in {"ACTIVE", "WALKING"}:
        return True
    if ac.get("std_mag", 0) >= MOVEMENT_STD_THRESHOLD:
        return True
    if ac.get("peak_mag", 0) >= MOVEMENT_PEAK_THRESHOLD:
        return True
    if gy.get("mean_mag", 0) >= GYRO_THRESHOLD:
        return True
    return False

def summarize_movement(windows: list) -> tuple:
    moving_windows = sum(1 for w in windows if window_has_movement(w))
    moving = moving_windows >= max(1, int(len(windows) * MOVEMENT_WINDOW_RATIO))
    if moving:
        return True, (f"Movement detected in {moving_windows}/{len(windows)} windows "
                      f"({moving_windows * 100 / len(windows):.0f}% of the sample).")
    return False, f"No clear motion in this batch ({moving_windows}/{len(windows)} moving windows)."

def analyze(windows: list, minute: int):
    print(f"\n{'='*60}")
    print(f"  BATCH {minute} — analyzing {len(windows)} windows...")
    print('='*60)
    _, movement_summary = summarize_movement(windows)
    summary_lines = "\n".join(fmt_window(w) for w in windows)
    total_seconds = sum((w.get("window_ms", 0) or 0) / 1000 for w in windows)
    avg_window_ms = sum((w.get("window_ms", 0) or 0) for w in windows) / max(1, len(windows))
    user_msg = (
        f"Batch {minute} data ({len(windows)} x {avg_window_ms:.0f}ms windows = "
        f"~{total_seconds:.0f}s of data):\n\n{summary_lines}\n\n{movement_summary}"
    )
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": DISPLAY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg}
            ],
            max_tokens=400,
            temperature=0.7
        )
        report = response.choices[0].message.content.strip()
        print(report)
        if sio.connected:
            sio.emit("ai_analysis", {"analysis": report, "trigger": f"batch-{minute}"})
        save_report(minute, report, windows)
    except Exception as e:
        print(f"[error] {e}")

def analysis_loop():
    batch_num = 1
    while True:
        time.sleep(ANALYSIS_INTERVAL)
        with windows_lock:
            batch = pending_windows.copy()
            pending_windows.clear()
        if batch:
            analyze(batch, batch_num)
            batch_num += 1
        else:
            print(f"\n[Batch {batch_num}] No data collected yet, skipping.")

def main():
    print(f"Session ID : {SESSION_ID}")
    print(f"Raw data   : {RAW_FILE}")
    print(f"Reports    : {REPORT_FILE}")
    connect_to_flask()
    threading.Thread(target=analysis_loop, daemon=True).start()
    print(f"Analysis fires every {ANALYSIS_INTERVAL}s.\n")
    print(f"\nConnecting to {WATCH_IP}:{PORT}...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((WATCH_IP, PORT))
        print(f"Connected to watch. Streaming...\n")
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

                    # ── Route by type ─────────────────────────────────────────
                    if w.get("type") == "chat":
                        emit_chat(w)
                        print(f"[chat] {w.get('user')} → {w.get('ai')[:60]}...")
                    else:
                        with windows_lock:
                            pending_windows.append(w)
                        emit_reading(w)
                        print(fmt_window(w))

                except json.JSONDecodeError:
                    print(f"[bad line] {line}")

if __name__ == "__main__":
    main()