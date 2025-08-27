import argparse
import sys
import numpy as np
from scipy import signal
from datetime import datetime
import time
import asyncio
import threading
import uvicorn
import base64
import subprocess
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
        .container { background: #fff; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); text-align: center; min-width: 450px; }
        h1 { color: #0d6efd; }
        #status { font-weight: bold; padding: 0.5rem; border-radius: 6px; margin-bottom: 1rem; transition: all 0.3s ease-in-out; }
        .status-connected { color: #198754; background-color: #d1e7dd; }
        .status-disconnected { color: #dc3545; background-color: #f8d7da; }
        .data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 2rem 0; }
        .metric { background: #e9ecef; padding: 1rem; border-radius: 6px; }
        .metric-label { font-size: 0.9rem; color: #6c757d; }
        .metric-value { font-size: 2rem; font-weight: bold; color: #0d6efd; }
    </style>
</head>
<body>
    <div class="container">
        <div id="status" class="status-disconnected">Connecting...</div>
        <h1>Live Radar Feed</h1>
        <div class="data-grid">
            <div class="metric"><div class="metric-label">Vzdálenost</div><span id="distance" class="metric-value">---</span> cm</div>
            <div class="metric"><div class="metric-label">Rychlost</div><span id="speed" class="metric-value">---</span> m/s</div>
            <div class="metric"><div class="metric-label">Směr</div><span id="direction" class="metric-value">---</span></div>
            <div class="metric"><div class="metric-label">Síla Signálu</div><span id="peak" class="metric-value">---</span></div>
        </div>
    </div>

    <script>
        const statusEl = document.getElementById('status');
        const distanceEl = document.getElementById('distance');
        const speedEl = document.getElementById('speed');
        const directionEl = document.getElementById('direction');
        const peakEl = document.getElementById('peak');

        function connect() {
            const ws = new WebSocket(`ws://${window.location.host}/ws`);

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                
                if (data.status === 'waiting_for_device') {
                    statusEl.textContent = 'Čekání na připojení zařízení...';
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

            ws.onopen = function(event) {
                statusEl.textContent = 'Server připojen';
                statusEl.className = 'status-connected';
            };

            ws.onclose = function(event) {
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
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        # Kopie seznamu pro případ, že se změní během iterace
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                # Klient se mezitím odpojil
                self.active_connections.remove(connection)

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
# Vlákno pro měření radaru
# ===========================================================================
def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

# --- FIX: Vláknu se nyní předává hlavní smyčka událostí serveru ---
def run_radar_loop(frate, loop: asyncio.AbstractEventLoop):
    def broadcast_sync(message: dict):
        # Používá předanou smyčku pro bezpečné odeslání zpráv
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    
    time.sleep(2)

    smoothed_distance, smoothed_speed = None, None
        
    while True:
        try:
            print(f"[{ts()}] [INFO] Pokus o připojení k radaru pomocí auto-detekce...")
            with DeviceFmcw() as device:
                print(f"[{ts()}] [SUCCESS] Radarové zařízení připojeno: {device.get_sensor_type()}")
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
                
                device.start_acquisition()
                print(f"[{ts()}] [INFO] Měření spuštěno, čekání na data...")
                
                algo = DopplerAlgo(chirp.num_samples, chirp_loop.loop.num_repetitions, metrics)
                
                while True:
                    frame_contents = device.get_next_frame()
                    antenna_samples = frame_contents[0][0, :, :]
                    distance_m, speed_ms, peak_value = algo.compute_doppler_map(antenna_samples)
                    
                    data_payload = None
                    if peak_value >= PEAK_THRESHOLD:
                        distance_cm = distance_m * 100
                        if smoothed_distance is None:
                            smoothed_distance, smoothed_speed = distance_cm, speed_ms
                        else:
                            smoothed_distance = EMA_ALPHA * distance_cm + (1 - EMA_ALPHA) * smoothed_distance
                            smoothed_speed = EMA_ALPHA * speed_ms + (1 - EMA_ALPHA) * smoothed_speed

                        if abs(smoothed_speed) < metrics.speed_resolution_m_s: direction = "Statický"
                        elif smoothed_speed < 0: direction = "Přibližování"
                        else: direction = "Vzdalování"
                        
                        data_payload = { "status": "connected",
                                         "distance_cm": smoothed_distance, "speed_ms": smoothed_speed,
                                         "direction": direction, "peak": peak_value }
                        
                        # --- FIX: Logování pouze nenulových hodnot ---
                        output_string = (f"{ts()} | Vzdálenost: {data_payload['distance_cm']:.1f} cm | "
                                         f"Rychlost: {data_payload['speed_ms']:+.2f} m/s | "
                                         f"Směr: {direction} | Peak: {peak_value:.4f}")
                        print(output_string)

                    # Odesíláme data jen pokud existují (nejsou nulová)
                    if data_payload:
                        broadcast_sync(data_payload)
        
        except Exception as e:
            print(f"[{ts()}] [ERROR] Smyčka radaru selhala: {e}")
            broadcast_sync({"status": "waiting_for_device"})
            smoothed_distance, smoothed_speed = None, None
            time.sleep(5)

# ===========================================================================
# FastAPI App a Endpoints
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- FIX: Získání hlavní smyčky událostí a její předání do vlákna ---
    loop = asyncio.get_running_loop()
    radar_thread = threading.Thread(target=run_radar_loop, args=(60, loop), daemon=True)
    radar_thread.start()
    yield
    print(f"\n[{ts()}] [INFO] Server se vypíná.")

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
    except (WebSocketDisconnect, asyncio.CancelledError):
        manager.disconnect(websocket)
        print(f"[{ts()}] [DEBUG] WebSocket odpojen.")

if __name__ == '__main__':
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        print(f"\n[{ts()}] [INFO] Ukončeno uživatelem (Ctrl+C).")
# ===========================================================================