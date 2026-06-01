"""
mock_watch.py — Simulated Wearable Device
Sends realistic heart-rate data to the Flask server via SocketIO.

Pattern (loops every ~3 minutes):
  Phase 1 — Resting      (60s):  steady 60–75 BPM
  Phase 2 — Rising       (30s):  gradual climb 75 → 110 BPM
  Phase 3 — Active       (45s):  sustained 95–115 BPM with variation
  Phase 4 — Spike        (10s):  sudden burst 120–145 BPM  ← triggers AI alert
  Phase 5 — Recovery     (45s):  gradual drop back to resting
"""

import socketio
import time
import random
import math
from datetime import datetime

SERVER_URL = "http://localhost:5000"
SEND_INTERVAL = 1.0  # seconds between readings

sio = socketio.Client()


@sio.event
def connect():
    print("✅ Connected to server — starting heart-rate simulation")


@sio.event
def disconnect():
    print("❌ Disconnected from server")


def generate_bpm(phase_time: float) -> int:
    """
    phase_time: seconds elapsed in the current cycle (0 → ~190s)
    Returns a realistic BPM int for that moment.
    """
    # Soft noise for human variability
    noise = random.gauss(0, 1.5)

    if phase_time < 60:
        # Phase 1: Resting — 60-75 BPM with gentle sine drift
        base = 67 + 5 * math.sin(phase_time / 10)
        return int(base + noise)

    elif phase_time < 90:
        # Phase 2: Rising — linear climb 75 → 110
        progress = (phase_time - 60) / 30
        base = 75 + progress * 35
        return int(base + noise * 2)

    elif phase_time < 135:
        # Phase 3: Active — sustained 95–115 with variation
        base = 105 + 8 * math.sin(phase_time / 5)
        return int(base + noise * 3)

    elif phase_time < 145:
        # Phase 4: Spike — 120–145 BPM
        base = 130 + random.uniform(-10, 15)
        return int(base)

    elif phase_time < 190:
        # Phase 5: Recovery — drop back to resting
        progress = (phase_time - 145) / 45
        base = 130 - progress * 60
        return int(max(base, 62) + noise)

    else:
        # Cycle complete — back to resting baseline
        return int(67 + noise)


def run():
    try:
        sio.connect(SERVER_URL)
    except Exception as e:
        print(f"❌ Could not connect to {SERVER_URL}: {e}")
        print("   Make sure server.py is running first.")
        return

    print("📡 Sending heart-rate data. Press Ctrl+C to stop.\n")
    print(f"{'Time':<10} {'Phase':<18} {'BPM':>5}")
    print("─" * 36)

    cycle_start = time.time()

    try:
        while True:
            now = time.time()
            phase_time = (now - cycle_start) % 190  # 190s full cycle

            bpm = generate_bpm(phase_time)

            # Clamp to physiologically plausible range
            bpm = max(45, min(180, bpm))

            ts = datetime.now().strftime("%H:%M:%S")

            sio.emit("heart_rate_data", {"bpm": bpm, "timestamp": ts})

            # Console label for which phase we're in
            if   phase_time < 60:  phase = "Resting"
            elif phase_time < 90:  phase = "Rising"
            elif phase_time < 135: phase = "Active"
            elif phase_time < 145: phase = "⚠ SPIKE"
            else:                  phase = "Recovery"

            print(f"{ts:<10} {phase:<18} {bpm:>5}")
            time.sleep(SEND_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n🛑 Mock watch stopped.")
        sio.disconnect()


if __name__ == "__main__":
    run()