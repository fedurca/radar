import argparse
import sys
import numpy as np
from scipy import signal
from datetime import datetime
import asyncio
import threading
import uvicorn
import base64
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, Response
from starlette.websockets import WebSocketDisconnect

from ifxradarsdk import get_version_full
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

# ===========================================================================
# --- USER CONFIGURATION ---
# ===========================================================================
# --- IMPORTANT: Verify this is the correct port on your Mac using: ls /dev/tty.* ---
SERIAL_PORT = "/dev/tty.usbmodem2101"

EMA_ALPHA = 0.4
PEAK_THRESHOLD = 0.2
FIXED_DISTANCE_CM = 50
DISTANCE_TOLERANCE_CM = 5
RANGE_START_CM = 20
RANGE_END_CM = 100
DIRECTION_FILTER = 'both'
# ===========================================================================

# ===========================================================================
# --- HTML, FAVICON, and WebSocket Manager ---
# ===========================================================================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Radar Live View</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f0f2f5; color: #1c1e21; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background: #fff; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); text-align: center; min-width: 450px; }
        h1 { color: #0d6efd; }
        #status { font-weight: bold; padding: 0.5rem; border-radius: 6px; margin-bottom: 1rem; }
        .status-connected { color: #198754; background-color: #d1e7dd; }
        .status-disconnected { color: #dc3545; background-color: #f8d7da; }
        .data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 2rem 0; }
        .metric { background: #e9ecef; padding: 1rem; border-radius: 6px; }
        .metric-label { font-size: 0.9rem; color: #6c757d; }
        .metric-value { font-size: 2rem; font-weight: bold; color: #0d6efd; }
        #event-log { margin-top: 1rem; text-align: left; background: #212529; color: #f8f9fa; padding: 1rem; border-radius: 6px; height: 120px; overflow-y: scroll; font-family: "SF Mono", "Menlo", monospace; font-size: 0.9rem; }
        .event { border-bottom: 1px solid #495057; padding-bottom: 5px; margin-bottom: 5px; }
        .event.alert { color: #ffc107; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div id="status" class="status-disconnected">Connecting...</div>
        <h1>Live Radar Feed</h1>
        <div class="data-grid">
            <div class="metric"><div class="metric-label">Distance</div><span id="distance" class="metric-value">---</span> cm</div>
            <div class="metric"><div class="metric-label">Speed</div><span id="speed" class="metric-value">---</span> m/s</div>
            <div class="metric"><div class="metric-label">Direction</div><span id="direction" class="metric-value">---</span></div>
            <div class="metric"><div class="metric-label">Peak Signal</div><span id="peak" class="metric-value">---</span></div>
        </div>
        <h2>Event Log</h2>
        <div id="event-log"></div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const distanceEl = document.getElementById('distance');
        const speedEl = document.getElementById('speed');
        const directionEl = document.getElementById('direction');
        const peakEl = document.getElementById('peak');
        const eventLog = document.getElementById('event-log');

        const ws = new WebSocket(`ws://${window.location.host}/ws`);

        ws.onmessage = function(event) {
            const data = JSON.parse(event.data);
            
            if (data.status === 'waiting') {
                statusEl.textContent = 'Waiting for Device...';
                statusEl.className = 'status-disconnected';
            } else {
                statusEl.textContent = 'Connected';
                statusEl.className = 'status-connected';

                distanceEl.textContent = data.distance_cm.toFixed(1);
                speedEl.textContent = data.speed_ms.toFixed(2);
                directionEl.textContent = data.direction;
                peakEl.textContent = data.peak.toFixed(4);

                if (data.event) {
                    const newEvent = document.createElement('div');
                    newEvent.className = 'event alert';
                    newEvent.innerHTML = `<strong>${data.timestamp}</strong>: ${data.event}`;
                    eventLog.prepend(newEvent);
                }
            }
        };

        ws.onopen = function(event) {
            const newEvent = document.createElement('div');
            newEvent.className = 'event';
            newEvent.textContent = 'WebSocket connection established.';
            eventLog.prepend(newEvent);
        };

        ws.onclose = function(event) {
            statusEl.textContent = 'SERVER DISCONNECTED';
            statusEl.className = 'status-disconnected';
        };
    </script>
</body>
</html>
"""

FAVICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAALEwAACxMBAJqcGAAAA6ZJREFUWIWVl1tsVFUUhr9zz5kzbWcbChZCsGBSCAnFRKE0iIgBCQZEYwISiwoaBMMHE4wflAQjERFEoxGNFSTgAypgDBgRQUUiCYigENpSKe300pmedk7PuffMH7vTzlBK+5A/ueTcc/7v/c/Zo2PG8M+Cyf8sQjIjiI5IG8HkJZJ2A4kQEj0xQJ2FLClpBZEgJKeBVEhI8pba2v1d2+79/RwAE88AiBDS2wBw1522j5/aW2+98XQO56QEkpWw6+f7L1y0/cuo23/v2o62yzzhQQG84wBUNk4AOKx314/yqI/fqgP5d+e+WwPAg/2b1v/z0y/V7P8HlEwVwHflXn0+AqB38/E3T1T2/e/r0b2b1j/z089VP+t/j1SMC8A7ZcK99y8/ffNqOAEoZ2D83p9+fHHzd5/6w/3b1285XfL+9/e/GgEACn58cfN3n/rDP5x7/f5pY+Pl2/c2+7+f+7gLgBQAQAgFkEwGIAUAgKkDgH8BwC4BwFcA8BYAcFcAaA0Ae1qA2f8G2a21Nl4u4N0BwGvK2A5/t7v9gX/Mvj72/bPnN156D8o/AECJ8fK1o2fWf3zw12+Wj00+e+a5nZtbP1YDAAghycnY2v2fPnvmuZ2bWz82Pj4+f3bx/M2bX71+3b/z8s7N48ePp/y/r1x968qVX7ZtZ/9f16/7d14+8sWzZ+t/3b79D4A7AKCszM8/f+S6r5+/ffqPkyc+P3v2rD/JOfn1a9e+Wlr6h5MnPj979qxdlsvl8uXLFx/+6KOP1qx58sSJU/03W1u/bNv2v927d3c0Gg0A2TIZAEopgBACAPQCAIQQAABKKT8/v0eapslEImEEAhEAREQAAIAQ4uXlpaUUBgARAIAL8fLyoqPjR1pbe7/x8fE3AODl5UU/P/+v6XT6OAAwPz//99nZ2d8BwOzs7O+KxeI/AwAvL+/fJpNJ/g/4AfD92z740eHhAAAAAElFTSuQmCC"

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print("[DEBUG] New client connected. Total clients:", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        print("[DEBUG] Client disconnected. Total clients:", len(self.active_connections))

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()
# ===========================================================================

# ===========================================================================
# Algorithm Class
# ===========================================================================
class DopplerAlgo:
    def __init__(self, num_samples_per_chirp, num_chirps_per_frame, metrics):
        self.num_samples_per_chirp = int(num_samples_per_chirp)
        self.num_chirps_per_frame = int(num_chirps_per_frame)
        self.range_window = signal.windows.blackmanharris(self.num_samples_per_chirp)
        self.doppler_window = signal.windows.blackmanharris(self.num_chirps_per_frame)
        self.range_axis = self._calculate_range_axis(metrics.range_resolution_m)
        self.speed_axis = self._calculate_speed_axis(metrics.max_speed_m_s)

    def _calculate_range_axis(self, range_resolution_m):
        return np.arange(self.num_samples_per_chirp // 2) * range_resolution_m

    def _calculate_speed_axis(self, max_speed_m_s):
        return np.linspace(-max_speed_m_s, max_speed_m_s, self.num_chirps_per_frame)

    def compute_doppler_map(self, frame_data):
        range_data = frame_data * self.range_window
        range_fft = np.fft.fft(range_data, axis=1)[:, :self.num_samples_per_chirp // 2]
        range_fft_mti = range_fft - np.mean(range_fft, axis=0)
        doppler_data = range_fft_mti * self.doppler_window[:, np.newaxis]
        range_doppler_map = np.fft.fftshift(np.fft.fft(doppler_data, axis=0), axes=0)
        range_doppler_map_abs = np.abs(range_doppler_map)
        peak_idx = np.unravel_index(np.argmax(range_doppler_map_abs), range_doppler_map_abs.shape)
        doppler_idx, range_idx = peak_idx
        peak_value = range_doppler_map_abs[peak_idx]
        try:
            return self.range_axis[range_idx], self.speed_axis[doppler_idx], peak_value
        except IndexError:
            return 0.0, 0.0, 0.0
# ===========================================================================

# ===========================================================================
# Radar Measurement Thread
# ===========================================================================
def run_radar_loop(frate):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def broadcast_sync(message: dict):
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)

    smoothed_distance = None
    smoothed_speed = None
        
    while True: # --- MODIFIED: Outer loop for resilient reconnection ---
        try:
            print(f"[INFO] Attempting to connect to radar on port {SERIAL_PORT}...")
            with DeviceFmcw(port=SERIAL_PORT) as device:
                print("[SUCCESS] Radar device connected.")
                metrics = FmcwMetrics(
                    range_resolution_m=0.05, max_range_m=1.6, max_speed_m_s=3,
                    speed_resolution_m_s=0.2, center_frequency_Hz=60_750_000_000,
                )
                sequence = device.create_simple_sequence(FmcwSimpleSequenceConfig())
                sequence.loop.repetition_time_s = 1 / frate
                chirp_loop = sequence.loop.sub_sequence.contents
                device.sequence_from_metrics(metrics, chirp_loop)
                chirp = chirp_loop.loop.sub_sequence.contents.chirp
                chirp.sample_rate_Hz = 1_000_000; chirp.rx_mask = 1; chirp.tx_mask = 1
                chirp.tx_power_level = 31; chirp.if_gain_dB = 33
                chirp.lp_cutoff_Hz = 500000; chirp.hp_cutoff_Hz = 80000
                device.set_acquisition_sequence(sequence)
                
                algo = DopplerAlgo(chirp.num_samples, chirp_loop.loop.num_repetitions, metrics)
                
                while True: # Inner loop for reading frames
                    frame_contents = device.get_next_frame()
                    antenna_samples = frame_contents[0][0, :, :]
                    distance_m, speed_ms, peak_value = algo.compute_doppler_map(antenna_samples)
                    
                    event_message = None
                    if peak_value >= PEAK_THRESHOLD:
                        distance_cm = distance_m * 100
                        if smoothed_distance is None:
                            smoothed_distance, smoothed_speed = distance_cm, speed_ms
                        else:
                            smoothed_distance = EMA_ALPHA * distance_cm + (1 - EMA_ALPHA) * smoothed_distance
                            smoothed_speed = EMA_ALPHA * speed_ms + (1 - EMA_ALPHA) * smoothed_speed

                        if abs(smoothed_speed) < metrics.speed_resolution_m_s: direction = "static"
                        elif smoothed_speed < 0: direction = "approaching"
                        else: direction = "receding"
                        
                        if abs(smoothed_distance - FIXED_DISTANCE_CM) <= DISTANCE_TOLERANCE_CM:
                            event_message = f"EVENT: Target at fixed distance ({smoothed_distance:.0f} cm)"
                        if RANGE_START_CM <= smoothed_distance <= RANGE_END_CM and (DIRECTION_FILTER == 'both' or DIRECTION_FILTER == direction):
                            event_message = f"EVENT: Movement in range ({direction} at {smoothed_distance:.0f} cm)"
                    else:
                        smoothed_distance, smoothed_speed, direction, peak_value = 0.0, 0.0, "---", 0.0

                    data_payload = { "status": "connected", "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                     "distance_cm": smoothed_distance if smoothed_distance is not None else 0.0,
                                     "speed_ms": smoothed_speed if smoothed_speed is not None else 0.0,
                                     "direction": direction if 'direction' in locals() and smoothed_distance is not None else "---",
                                     "peak": peak_value, "event": event_message }
                    broadcast_sync(data_payload)
        
        except Exception as e:
            print(f"[ERROR] Radar loop failed: {e}. Device disconnected.")
            # --- NEW: Send 'waiting' status to web clients ---
            broadcast_sync({"status": "waiting"})
            # Reset smoothing on disconnect
            smoothed_distance = None
            smoothed_speed = None
            time.sleep(5) # Wait 5 seconds before trying to reconnect

# ===========================================================================
# FastAPI App and Endpoints
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    radar_thread = threading.Thread(target=run_radar_loop, args=(60,), daemon=True)
    radar_thread.start()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def get():
    return HTMLResponse(content=HTML_CONTENT)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(base64.b64decode(FAVICON_B64), media_type="image/x-icon")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("[DEBUG] WebSocket disconnected.")

# Main entry point for running the server
if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)
# ===========================================================================