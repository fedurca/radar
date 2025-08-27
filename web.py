import argparse
import sys
import numpy as np
from scipy import signal
from datetime import datetime, timedelta
import time
import asyncio
import threading
import uvicorn
import base64
import json
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, Response
from starlette.websockets import WebSocketDisconnect

from ifxradarsdk import get_version_full
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

# ===========================================================================
# --- UŽIVATELSKÁ KONFIGURACE ---
# ===========================================================================
EMA_ALPHA = 0.4
PEAK_THRESHOLD = 0.2

RANGE_PRESETS = {
    "0.5m (Vysoká přesnost)": (0.5, 0.02), "1.6m (Standardní)": (1.6, 0.05),
    "3m (Místnost)": (3.0, 0.10), "5m": (5.0, 0.15),
    "8m (Výchozí)": (8.0, 0.20), "10m": (10.0, 0.25),
    "12m": (12.0, 0.30), "15m (Maximální dosah)": (15.0, 0.40),
}
DEFAULT_RANGE_KEY = "8m (Výchozí)"

FRAME_RATES_HZ = [5, 10, 20, 30, 40, 50, 60]
DEFAULT_FRAME_RATE = 20

# --- NEW: Konfigurace pro Watchdog ---
WATCHDOG_TIMEOUT_S = 10 # Po kolika sekundách bez dat restartovat
# ===========================================================================

# ===========================================================================
# --- Globální proměnné pro Watchdog a Uptime ---
# ===========================================================================
START_TIME = datetime.now()
last_frame_time = datetime.now()
watchdog_lock = threading.Lock()
# ===========================================================================

# ===========================================================================
# --- HTML, FAVICON A WebSocket Manager ---
# ===========================================================================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Radar Live View</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f0f2f5; color: #1c1e21; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background: #fff; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); text-align: center; min-width: 500px; }
        h1 { color: #0d6efd; margin-top:0; }
        #status { font-weight: bold; padding: 0.5rem; border-radius: 6px; margin-bottom: 1rem; transition: all 0.3s ease-in-out; }
        .status-connected { color: #198754; background-color: #d1e7dd; }
        .status-disconnected { color: #dc3545; background-color: #f8d7da; }
        .controls { margin-top: 1.5rem; display: flex; justify-content: center; align-items: center; gap: 20px; }
        .control-group { display: flex; align-items: center; gap: 10px; }
        .controls select { font-size: 1rem; padding: 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
        .data-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.5rem 0; }
        .metric { background: #e9ecef; padding: 1rem; border-radius: 6px; }
        .metric-label { font-size: 0.9rem; color: #6c757d; }
        .metric-value { font-size: 2rem; font-weight: bold; color: #0d6efd; }
        .full-width { grid-column: 1 / -1; }
    </style>
</head>
<body>
    <div class="container">
        <div id="status" class="status-disconnected">Connecting...</div>
        <h1>Live Radar Feed</h1>
        <div class="controls">
            <div class="control-group">
                <label for="range-selector">Rozsah:</label>
                <select id="range-selector"></select>
            </div>
            <div class="control-group">
                <label for="frate-selector">Frekvence:</label>
                <select id="frate-selector"></select>
            </div>
        </div>
        <div class="data-grid">
            <div class="metric"><div class="metric-label">Vzdálenost</div><span id="distance" class="metric-value">---</span> cm</div>
            <div class="metric"><div class="metric-label">Rychlost</div><span id="speed" class="metric-value">---</span> m/s</div>
            <div class="metric"><div class="metric-label">Směr</div><span id="direction" class="metric-value">---</span></div>
            <div class="metric full-width"><div class="metric-label">Síla Signálu</div><span id="peak" class="metric-value">---</span></div>
            <div class="metric full-width"><div class="metric-label">Doba Běhu</div><span id="uptime" class="metric-value">---</span></div>
        </div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const distanceEl = document.getElementById('distance');
        const speedEl = document.getElementById('speed');
        const directionEl = document.getElementById('direction');
        const peakEl = document.getElementById('peak');
        const uptimeEl = document.getElementById('uptime');
        const rangeSelector = document.getElementById('range-selector');
        const frateSelector = document.getElementById('frate-selector');

        const rangePresets = __RANGE_PRESETS__;
        const defaultRange = "__DEFAULT_RANGE_KEY__";
        const frameRates = __FRAME_RATES__;
        const defaultFrameRate = __DEFAULT_FRAME_RATE__;
        
        rangePresets.forEach(key => {
            const option = document.createElement('option');
            option.value = key; option.textContent = key;
            if (key === defaultRange) option.selected = true;
            rangeSelector.appendChild(option);
        });
        frameRates.forEach(rate => {
            const option = document.createElement('option');
            option.value = rate; option.textContent = rate + ' Hz';
            if (rate === defaultFrameRate) option.selected = true;
            frateSelector.appendChild(option);
        });

        function sendConfig(ws) {
            const rangeKey = rangeSelector.value;
            const frate = parseInt(frateSelector.value, 10);
            ws.send(JSON.stringify({ action: 'reconfigure', range_key: rangeKey, frate: frate }));
        }

        function connect() {
            const ws = new WebSocket(`ws://${window.location.host}/ws`);

            ws.onopen = () => { statusEl.textContent = 'Server připojen'; statusEl.className = 'status-connected'; sendConfig(ws); };
            rangeSelector.onchange = () => sendConfig(ws);
            frateSelector.onchange = () => sendConfig(ws);

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                uptimeEl.textContent = data.uptime || '---';

                if (data.status === 'waiting_for_device') {
                    statusEl.textContent = 'Čekání na připojení zařízení...';
                    statusEl.className = 'status-disconnected';
                } else if (data.status === 'reconfiguring') {
                    statusEl.textContent = 'Rekonfigurace radaru...';
                    statusEl.className = 'status-disconnected';
                } else if (data.status === 'connected') {
                    statusEl.textContent = 'Připojeno';
                    statusEl.className = 'status-connected';
                    distanceEl.textContent = data.distance_cm.toFixed(1);
                    speedEl.textContent = data.speed_ms.toFixed(2);
                    directionEl.textContent = data.direction;
                    peakEl.textContent = data.peak.toFixed(4);
                }
            };

            ws.onclose = () => {
                statusEl.textContent = 'SERVER ODPOJEN - Pokus o znovupřipojení...';
                statusEl.className = 'status-disconnected';
                setTimeout(connect, 3000);
            };
        }
        connect();
    </script>
</body>
</html>
"""
FAVICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAAAsTAAALEwEAmpwYAAAAVUlEQVRYw+3VwQkAIBAEwP3/p5xABCKdG1pr1fB00QYxJgCIiAgAEREBABEBABEBABEBABEBABEBABEBABEBABEBABEBABEBABEBABF9cgILAEJMfX28AAAAAElFTSuQmCC"

class ConnectionManager:
    def __init__(self): self.active_connections: list[WebSocket] = []
    async def connect(self, websocket: WebSocket): await websocket.accept(); self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections: self.active_connections.remove(websocket)
    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try: await connection.send_json(message)
            except Exception:
                if connection in self.active_connections: self.active_connections.remove(connection)

manager = ConnectionManager()
# ===========================================================================

# ===========================================================================
# Algoritmus
# ===========================================================================
class DopplerAlgo:
    def __init__(self, num_samples_per_chirp, num_chirps_per_frame, metrics):
        self.num_samples_per_chirp = int(num_samples_per_chirp)
        self.num_chirps_per_frame = int(num_chirps_per_frame)
        self.range_window = signal.windows.blackmanharris(self.num_samples_per_chirp)
        self.doppler_window = signal.windows.blackmanharris(self.num_chirps_per_frame)
        self.range_axis = self._calculate_range_axis(metrics.range_resolution_m)
        self.speed_axis = self._calculate_speed_axis(metrics.max_speed_m_s)
    def _calculate_range_axis(self, range_resolution_m): return np.arange(self.num_samples_per_chirp // 2) * range_resolution_m
    def _calculate_speed_axis(self, max_speed_m_s): return np.linspace(-max_speed_m_s, max_speed_m_s, self.num_chirps_per_frame)
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
        try: return self.range_axis[range_idx], self.speed_axis[doppler_idx], peak_value
        except IndexError: return 0.0, 0.0, 0.0
# ===========================================================================

# ===========================================================================
# Vlákno pro měření radaru
# ===========================================================================
radar_thread = None
stop_radar_thread = threading.Event()
thread_lock = threading.Lock()

def run_radar_loop(frate, loop: asyncio.AbstractEventLoop, range_key: str):
    global last_frame_time
    def broadcast_sync(message: dict):
        uptime = datetime.now() - START_TIME
        message['uptime'] = str(uptime).split('.')[0] # Přidání uptime do každé zprávy
        if not loop.is_closed(): asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    
    smoothed_distance, smoothed_speed = None, None
    try:
        print(f"[{datetime.now():%H:%M:%S}] [INFO] Pokus o připojení k radaru...")
        with DeviceFmcw() as device:
            print(f"[{datetime.now():%H:%M:%S}] [SUCCESS] Radar připojen. Konfigurace: {range_key} @ {frate} Hz")
            # ... (zbytek konfigurace radaru) ...
            max_range, range_res = RANGE_PRESETS[range_key]
            metrics = FmcwMetrics(range_resolution_m=range_res, max_range_m=max_range, max_speed_m_s=3, speed_resolution_m_s=0.2, center_frequency_Hz=60_750_000_000)
            sequence = device.create_simple_sequence(FmcwSimpleSequenceConfig())
            sequence.loop.repetition_time_s = 1 / frate
            chirp_loop = sequence.loop.sub_sequence.contents
            device.sequence_from_metrics(metrics, chirp_loop)
            chirp = chirp_loop.loop.sub_sequence.contents.chirp
            chirp.sample_rate_Hz=1_000_000; chirp.rx_mask=1; chirp.tx_mask=1; chirp.tx_power_level=31
            chirp.if_gain_dB=33; chirp.lp_cutoff_Hz=500000; chirp.hp_cutoff_Hz=80000
            device.set_acquisition_sequence(sequence)
            device.start_acquisition()
            algo = DopplerAlgo(chirp.num_samples, chirp_loop.loop.num_repetitions, metrics)
            
            while not stop_radar_thread.is_set():
                frame_contents = device.get_next_frame()
                with watchdog_lock: last_frame_time = datetime.now() # Aktualizace času pro watchdog
                
                antenna_samples = frame_contents[0][0, :, :]
                distance_m, speed_ms, peak_value = algo.compute_doppler_map(antenna_samples)
                
                data_payload = None
                if peak_value >= PEAK_THRESHOLD:
                    distance_cm = distance_m * 100
                    if smoothed_distance is None: smoothed_distance, smoothed_speed = distance_cm, speed_ms
                    else:
                        smoothed_distance = EMA_ALPHA * distance_cm + (1 - EMA_ALPHA) * smoothed_distance
                        smoothed_speed = EMA_ALPHA * speed_ms + (1 - EMA_ALPHA) * smoothed_speed
                    if abs(smoothed_speed) < metrics.speed_resolution_m_s: direction = "Statický"
                    elif smoothed_speed < 0: direction = "Přibližování"
                    else: direction = "Vzdalování"
                    data_payload = {"status": "connected", "distance_cm": smoothed_distance, "speed_ms": smoothed_speed, "direction": direction, "peak": peak_value}
                if data_payload: broadcast_sync(data_payload)
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] [ERROR] Smyčka radaru selhala: {e}")
        broadcast_sync({"status": "waiting_for_device", "uptime": str(datetime.now() - START_TIME).split('.')[0]})
    print(f"[{datetime.now():%H:%M:%S}] [INFO] Vlákno radaru bylo ukončeno.")

# ===========================================================================
# --- NEW: Watchdog vlákno ---
# ===========================================================================
def watchdog_thread_func():
    """Sleduje radarové vlákno a restartuje aplikaci, pokud zamrzne."""
    while True:
        time.sleep(WATCHDOG_TIMEOUT_S / 2)
        with watchdog_lock:
            time_since_last_frame = datetime.now() - last_frame_time
        
        if time_since_last_frame.total_seconds() > WATCHDOG_TIMEOUT_S:
            print(f"[{datetime.now():%H:%M:%S}] [FATAL] WATCHDOG: Nebyla přijata žádná data déle než {WATCHDOG_TIMEOUT_S} sekund. Restartuji aplikaci...")
            # Bezpečný restart aplikace
            os.execv(sys.executable, ['python'] + sys.argv)
# ===========================================================================

# ===========================================================================
# FastAPI App a Endpoints
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global radar_thread, stop_radar_thread, last_frame_time
    loop = asyncio.get_running_loop()
    
    # Spuštění watchdog vlákna
    watchdog = threading.Thread(target=watchdog_thread_func, daemon=True)
    watchdog.start()
    
    # První spuštění radarového vlákna
    with thread_lock:
        last_frame_time = datetime.now()
        stop_radar_thread.clear()
        radar_thread = threading.Thread(target=run_radar_loop, args=(DEFAULT_FRAME_RATE, loop, DEFAULT_RANGE_KEY), daemon=True)
        radar_thread.start()
    yield
    # Cleanup při vypnutí
    stop_radar_thread.set()
    if radar_thread and radar_thread.is_alive(): radar_thread.join()
    print(f"\n[{datetime.now():%H:%M:%S}] [INFO] Server se vypíná.")

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def get():
    content = HTML_CONTENT.replace(
        "__RANGE_PRESETS__", json.dumps(list(RANGE_PRESETS.keys()))
    ).replace(
        '__DEFAULT_RANGE_KEY__', DEFAULT_RANGE_KEY
    ).replace(
        "__FRAME_RATES__", json.dumps(FRAME_RATES_HZ)
    ).replace(
        '__DEFAULT_FRAME_RATE__', str(DEFAULT_FRAME_RATE)
    )
    return HTMLResponse(content=content)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(base64.b64decode(FAVICON_B64), media_type="image/x-icon")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global radar_thread, stop_radar_thread, last_frame_time
    await manager.connect(websocket)
    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await websocket.receive_json()
            with thread_lock:
                range_key = data.get('range_key', DEFAULT_RANGE_KEY)
                frate = data.get('frate', DEFAULT_FRAME_RATE)
                print(f"[{datetime.now():%H:%M:%S}] [INFO] Obdržen požadavek na rekonfiguraci: Rozsah={range_key}, Frekvence={frate} Hz")
                
                if radar_thread and radar_thread.is_alive():
                    stop_radar_thread.set()
                    radar_thread.join(timeout=2)
                
                await manager.broadcast({"status": "reconfiguring", "uptime": str(datetime.now() - START_TIME).split('.')[0]})
                time.sleep(0.5)
                
                last_frame_time = datetime.now()
                stop_radar_thread.clear()
                radar_thread = threading.Thread(target=run_radar_loop, args=(frate, loop, range_key), daemon=True)
                radar_thread.start()
    except (WebSocketDisconnect, asyncio.CancelledError):
        manager.disconnect(websocket)

if __name__ == '__main__':
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now():%H:%M:%S}] [INFO] Ukončeno uživatelem (Ctrl+C).")
# ===========================================================================