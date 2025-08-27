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
import json
import os
import io
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from starlette.websockets import WebSocketDisconnect
from starlette.responses import StreamingResponse

# --- NEW: Import pro vykreslování grafů ---
import matplotlib
matplotlib.use('Agg') # Použití neinteraktivního backendu pro běh na serveru
import matplotlib.pyplot as plt

from ifxradarsdk import get_version_full
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

# ===========================================================================
# --- KONFIGURACE ---
# ===========================================================================
EMA_ALPHA = 0.4
DEFAULT_PEAK_THRESHOLD = 0.2
DEFAULT_RANGE_KEY = "8m (Výchozí)"
DEFAULT_FRAME_RATE = 20
START_TIME = datetime.now()

RANGE_PRESETS = {
    "0.5m (Vysoká přesnost)": (0.5, 0.05), "1.6m (Standardní)": (1.6, 0.05),
    "3m (Místnost)": (3.0, 0.10), "5m": (5.0, 0.15),
    "8m (Výchozí)": (8.0, 0.20), "10m": (10.0, 0.25),
    "12m": (12.0, 0.30), "15m (Maximální dosah)": (15.0, 0.40),
}
FRAME_RATES_HZ = [5, 10, 20, 30, 40, 50, 60]
# ===========================================================================

# ===========================================================================
# --- PŘEKLADY ---
# ===========================================================================
LANGUAGES = {
    "en": {"title": "Radar Live View", "status_connecting": "Connecting...", "status_connected_server": "Server Connected", "status_waiting": "Waiting for Device...", "status_reconfiguring": "Reconfiguring Radar...", "status_connected_device": "Connected", "status_disconnected_server": "SERVER DISCONNECTED - Reconnecting...", "header": "Live Radar Feed", "range_label": "Range:", "frate_label": "Frequency:", "sensitivity_label": "Sensitivity:", "distance": "Distance", "speed": "Speed", "direction": "Direction", "peak_signal": "Peak Signal", "sensor_uptime": "Sensor Uptime", "program_uptime": "Program Uptime", "log_header": "Diagnostic Log", "static": "Static", "approaching": "Approaching", "receding": "Receding", "toggle_theme": "Toggle Theme", "lang_toggle": "Česky", "hold_label": "Hold Last Value"},
    "cz": {"title": "Radar Live Vizualizace", "status_connecting": "Připojování...", "status_connected_server": "Server připojen", "status_waiting": "Čekání na zařízení...", "status_reconfiguring": "Rekonfigurace radaru...", "status_connected_device": "Připojeno", "status_disconnected_server": "SERVER ODPOJEN - Pokus o znovupřipojení...", "header": "Živá data z radaru", "range_label": "Rozsah:", "frate_label": "Frekvence:", "sensitivity_label": "Citlivost:", "distance": "Vzdálenost", "speed": "Rychlost", "direction": "Směr", "peak_signal": "Síla signálu", "sensor_uptime": "Doba připojení", "program_uptime": "Doba běhu", "log_header": "Diagnostický Log", "static": "Statický", "approaching": "Přibližování", "receding": "Vzdalování", "toggle_theme": "Přepnout vzhled", "lang_toggle": "English", "hold_label": "Podržet poslední hodnotu"}
}
# ===========================================================================

# ===========================================================================
# --- HTML, FAVICON A WebSocket Manager ---
# ===========================================================================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title data-lang="title">Radar Live View</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>
        :root {
            --bg-color: #f0f2f5; --text-color: #1c1e21; --card-bg-color: #ffffff;
            --primary-color: #0d6efd; --secondary-bg-color: #e9ecef; --secondary-text-color: #6c757d;
            --border-color: #dee2e6; --shadow-color: rgba(0, 0, 0, 0.1); --icon-fill: #6c757d;
            --color-approaching: #198754; --color-receding: #dc3545; --color-static: #0d6efd;
        }
        body.dark-mode {
            --bg-color: #121212; --text-color: #e0e0e0; --card-bg-color: #1e1e1e;
            --primary-color: #4dabf7; --secondary-bg-color: #333333; --secondary-text-color: #adb5bd;
            --border-color: #444444; --shadow-color: rgba(0, 0, 0, 0.4); --icon-fill: #adb5bd;
            --color-approaching: #28a745; --color-receding: #ff4d4f; --color-static: #4dabf7;
        }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg-color); color: var(--text-color); transition: background-color 0.3s, color 0.3s; }
        .main-container { display: flex; flex-direction: column; height: 100%; width: 100%; padding: 1rem; }
        header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 1rem; flex-shrink: 0; }
        .title-area h1 { color: var(--primary-color); margin: 0; }
        #status { font-weight: bold; padding: 0.5rem; border-radius: 6px; transition: all 0.3s ease-in-out; }
        .status-connected { color: #198754; background-color: #d1e7dd; }
        .status-disconnected { color: #dc3545; background-color: #f8d7da; }
        .top-controls { display: flex; gap: 1rem; align-items: center; }
        .top-controls button { font-size: 0.8rem; padding: 0.5rem; cursor: pointer; background-color: var(--secondary-bg-color); border: 1px solid var(--border-color); color: var(--text-color); border-radius: 6px; }
        .content-area { display: flex; flex: 1; gap: 1rem; overflow: hidden; }
        .left-panel, .right-panel { background: var(--card-bg-color); padding: 1.5rem; border-radius: 8px; box-shadow: 0 4px 12px var(--shadow-color); display: flex; flex-direction: column; }
        .left-panel { flex: 2; } .right-panel { flex: 1; }
        .controls { margin-top: 1rem; display: grid; grid-template-columns: 1fr; gap: 1.5rem; text-align: left; }
        .control-group { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
        .control-group-row { display: flex; align-items: center; gap: 10px; }
        .controls select, .controls input { font-size: 1rem; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border-color); background-color: var(--card-bg-color); color: var(--text-color); width: 100%; }
        input[type="checkbox"] { width: auto; }
        .data-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1.5rem; }
        .metric { background: var(--secondary-bg-color); padding: 1rem; border-radius: 6px; text-align: center; }
        .metric-label { font-size: 0.9rem; color: var(--secondary-text-color); }
        .metric-value { font-size: 1.8rem; font-weight: bold; color: var(--text-color); }
        .mini-bargraph-container { width: 100%; height: 10px; background-color: var(--border-color); border-radius: 5px; margin-top: 0.75rem; overflow: hidden; }
        .mini-bargraph-bar { height: 100%; width: 0%; border-radius: 5px; transition: width 0.1s linear, background-color 0.3s; }
        #plot-container { margin-top: 1.5rem; }
        #plot { width: 100%; border-radius: 6px; border: 1px solid var(--border-color); }
        #log-container { display: flex; flex-direction: column; height: 100%; }
        #log-header { margin-top: 0; }
        #log { flex: 1; background: var(--secondary-bg-color); color: var(--text-color); padding: 1rem; border-radius: 6px; overflow-y: scroll; font-family: "SF Mono", "Menlo", monospace; font-size: 0.8rem; text-align: left; }
        .log-entry { border-bottom: 1px solid var(--border-color); padding-bottom: 4px; margin-bottom: 4px; word-break: break-all; }
        .log-entry.error { color: var(--color-receding); } .log-entry.success { color: var(--color-approaching); } .log-entry.data { opacity: 0.7; }
        .approaching { background-color: var(--color-approaching); }
        .receding { background-color: var(--color-receding); }
        .static { background-color: var(--color-static); }
        #theme-toggle { position: absolute; top: 15px; right: 15px; background: var(--secondary-bg-color); border: 1px solid var(--border-color); border-radius: 50%; width: 40px; height: 40px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        #theme-toggle svg { width: 20px; height: 20px; fill: var(--icon-fill); }
        .dark-mode .sun-icon { display: block; } .dark-mode .moon-icon { display: none; }
        .sun-icon { display: none; } .moon-icon { display: block; }
    </style>
</head>
<body>
    <div class="main-container">
        <header>
            <div class="title-area">
                <h1 data-lang="header">Live Radar Feed</h1>
                <div id="status" class="status-disconnected" data-lang="status_connecting">Connecting...</div>
            </div>
            <div class="top-controls">
                <button id="lang-toggle" data-lang="lang_toggle">Česky</button>
                <button id="theme-toggle" data-lang="toggle_theme" title="Toggle Theme"></button>
            </div>
        </header>
        <div class="content-area">
            <div class="left-panel">
                <div class="controls">
                    <div class="control-group">
                        <label for="range-selector" data-lang="range_label">Range:</label>
                        <select id="range-selector"></select>
                    </div>
                    <div class="control-group">
                        <label for="frate-selector" data-lang="frate_label">Frequency:</label>
                        <select id="frate-selector"></select>
                    </div>
                    <div class="control-group">
                        <label for="sensitivity-slider" data-lang="sensitivity_label">Sensitivity:</label>
                        <input type="range" id="sensitivity-slider" min="0.01" max="5.0" step="0.01" value="0.2">
                        <span id="sensitivity-value">0.2</span>
                    </div>
                     <div class="control-group-row">
                        <input type="checkbox" id="hold-toggle" checked>
                        <label for="hold-toggle" data-lang="hold_label">Hold Last Value</label>
                    </div>
                </div>
                <div class="data-grid">
                    <div class="metric"><div class="metric-label" data-lang="distance">Distance</div><span id="distance" class="metric-value">---</span> cm<div class="mini-bargraph-container"><div id="distance-mini-bar" class="mini-bargraph-bar"></div></div></div>
                    <div class="metric"><div class="metric-label" data-lang="speed">Speed</div><span id="speed" class="metric-value">---</span> m/s<div class="mini-bargraph-container"><div id="speed-mini-bar" class="mini-bargraph-bar"></div></div></div>
                    <div class="metric"><div class="metric-label" data-lang="direction">Direction</div><span id="direction" class="metric-value">---</span></div>
                    <div class="metric"><div class="metric-label" data-lang="peak_signal">Peak Signal</div><span id="peak" class="metric-value">---</span><div class="mini-bargraph-container"><div id="peak-mini-bar" class="mini-bargraph-bar"></div></div></div>
                    <div class="metric"><div class="metric-label" data-lang="sensor_uptime">Sensor Uptime</div><span id="sensor_uptime" class="metric-value">---</span></div>
                    <div class="metric"><div class="metric-label" data-lang="program_uptime">Program Uptime</div><span id="program_uptime" class="metric-value">---</span></div>
                </div>
                <div id="plot-container">
                    <img id="plot" src="" alt="Live Plot"/>
                </div>
            </div>
            <div class="right-panel" id="log-container">
                <h2 id="log-header" data-lang="log_header">Diagnostic Log</h2>
                <div id="log"></div>
            </div>
        </div>
    </div>

    <script>
        (() => {
            const ui = {
                status: document.getElementById('status'), distance: document.getElementById('distance'),
                speed: document.getElementById('speed'), direction: document.getElementById('direction'),
                peak: document.getElementById('peak'), sensor_uptime: document.getElementById('sensor_uptime'),
                program_uptime: document.getElementById('program_uptime'),
                bar_dist: document.getElementById('distance-mini-bar'),
                bar_speed: document.getElementById('speed-mini-bar'),
                bar_peak: document.getElementById('peak-mini-bar'),
                plot: document.getElementById('plot'),
                log: document.getElementById('log'), rangeSelector: document.getElementById('range-selector'),
                frateSelector: document.getElementById('frate-selector'),
                sensitivitySlider: document.getElementById('sensitivity-slider'),
                sensitivityValue: document.getElementById('sensitivity-value'),
                themeToggle: document.getElementById('theme-toggle'), langToggle: document.getElementById('lang-toggle'),
                holdToggle: document.getElementById('hold-toggle')
            };
            let maxDistanceCm = 800, maxSpeedMs = 3, maxPeak = 10, langDict = {}, lastValidData = null;
            const rangePresets = __RANGE_PRESETS__, defaultRange = "__DEFAULT_RANGE_KEY__";
            const frameRates = __FRAME_RATES__, defaultFrameRate = __DEFAULT_FRAME_RATE__;
            
            async function setLanguage(lang) {
                try {
                    const response = await fetch(`/lang/${lang}`);
                    langDict = await response.json();
                    document.querySelectorAll('[data-lang]').forEach(el => {
                        const key = el.getAttribute('data-lang');
                        if (langDict[key]) el.textContent = langDict[key];
                    });
                    document.documentElement.lang = lang;
                    localStorage.setItem('language', lang);
                } catch (e) { console.error("Could not set language", e); }
            }
            ui.langToggle.addEventListener('click', () => {
                const newLang = document.documentElement.lang === 'en' ? 'cz' : 'en';
                setLanguage(newLang);
            });

            function applyTheme(theme) { document.body.classList.toggle('dark-mode', theme === 'dark'); }
            ui.themeToggle.addEventListener('click', () => {
                const newTheme = document.body.classList.contains('dark-mode') ? 'light' : 'dark';
                localStorage.setItem('theme', newTheme);
                applyTheme(newTheme);
            });
            
            rangePresets.forEach(key => {
                const option = document.createElement('option');
                option.value = key; option.textContent = key;
                if (key === defaultRange) option.selected = true;
                ui.rangeSelector.appendChild(option);
            });
            frameRates.forEach(rate => {
                const option = document.createElement('option');
                option.value = rate; option.textContent = rate + ' Hz';
                if (rate === defaultFrameRate) option.selected = true;
                ui.frateSelector.appendChild(option);
            });

            function sendConfig(ws) {
                if (ws.readyState !== WebSocket.OPEN) return;
                const rangeKey = ui.rangeSelector.value;
                const frate = parseInt(ui.frateSelector.value, 10);
                const sensitivity = parseFloat(ui.sensitivitySlider.value);
                const rangeValueStr = rangeKey.split('m')[0].replace(',', '.');
                maxDistanceCm = parseFloat(rangeValueStr) * 100;
                ws.send(JSON.stringify({ action: 'reconfigure', range_key: rangeKey, frate: frate, sensitivity: sensitivity }));
            }
            
            function updateUI(data) {
                ui.program_uptime.textContent = data.program_uptime || '---';
                const statusKey = (data.status || 'connecting').replace(/ /g, '_');
                ui.status.textContent = langDict[`status_${statusKey}`] || data.status;
                ui.status.className = data.status === 'connected' ? 'status-connected' : 'status-disconnected';
                
                const isDataValid = data.status === 'connected' && data.peak > 0;
                const displayData = (isDataValid) ? data : (ui.holdToggle.checked && lastValidData) ? lastValidData : data;

                if(isDataValid) { lastValidData = data; } 
                else if(ui.holdToggle.checked && lastValidData) {
                    lastValidData.program_uptime = data.program_uptime;
                    lastValidData.sensor_uptime = "0:00:00";
                }

                ui.distance.textContent = displayData.distance_cm.toFixed(1);
                ui.speed.textContent = displayData.speed_ms.toFixed(2);
                ui.direction.textContent = langDict[displayData.direction.toLowerCase()] || displayData.direction;
                ui.peak.textContent = displayData.peak.toFixed(4);
                ui.sensor_uptime.textContent = displayData.sensor_uptime || '---';

                const dist_p = Math.min(100, Math.max(0, (displayData.distance_cm / maxDistanceCm) * 100));
                const speed_p = Math.min(100, Math.max(0, (Math.abs(displayData.speed_ms) / maxSpeedMs) * 100));
                const peak_p = Math.min(100, Math.max(0, (displayData.peak / maxPeak) * 100));
                
                ui.bar_dist.style.width = `${dist_p}%`;
                ui.bar_speed.style.width = `${speed_p}%`;
                ui.bar_peak.style.width = `${peak_p}%`;

                const barsToColor = [ui.bar_dist, ui.bar_speed, ui.bar_peak];
                barsToColor.forEach(el => el.classList.remove('approaching', 'receding', 'static'));
                let cssClass = 'static';
                if(displayData.direction === 'Přibližování' || displayData.direction === 'Approaching') cssClass = 'approaching';
                else if (displayData.direction === 'Vzdalování' || displayData.direction === 'Receding') cssClass = 'receding';
                barsToColor.forEach(el => el.classList.add(cssClass));
            }

            function connect() {
                const ws = new WebSocket(`ws://${window.location.host}/ws`);
                
                ws.onopen = () => { setLanguage(localStorage.getItem('language') || 'en').then(() => sendConfig(ws)); };
                ui.rangeSelector.onchange = () => sendConfig(ws);
                ui.frateSelector.onchange = () => sendConfig(ws);
                ui.sensitivitySlider.oninput = () => { ui.sensitivityValue.textContent = ui.sensitivitySlider.value; };
                ui.sensitivitySlider.onchange = () => sendConfig(ws);

                function addLog(msg, type = 'info') {
                    const entry = document.createElement('div');
                    entry.className = `log-entry ${type}`;
                    entry.textContent = msg;
                    ui.log.prepend(entry);
                    if (ui.log.children.length > 100) ui.log.removeChild(ui.log.lastChild);
                }

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.type === 'log') { addLog(data.message, data.level); return; }
                    updateUI(data);
                };
                ws.onclose = () => {
                    ui.status.textContent = langDict.status_disconnected_server || 'SERVER DISCONNECTED - Reconnecting...';
                    ui.status.className = 'status-disconnected';
                    setTimeout(connect, 3000);
                };
            }
            
            applyTheme(localStorage.getItem('theme') || 'light');
            
            // --- NEW: Start live plot updater ---
            setInterval(() => {
                ui.plot.src = "/plot.jpeg?" + new Date().getTime();
            }, 200); // Update plot 5 times per second

            connect();
        })();
    </script>
</body>
</html>
"""
# --- MODIFIED: Nová favicona ---
FAVICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAACXBIWXMAAAsTAAALEwEAmpwYAAABJklEQVR4nO2WQQ6CMBBEvx/dDDfTwh3cR+h54D7Ok0h2Z+i5Y2/oKk6lBCRIaEppU3pA0+f9PTM9sP3/LgB4A7ADc2EMAl0GIJNW07a9A+5aGgBBCHmEiBERAUBEBAAiIgAiIgAiIgAiIgAiIgAiIgAiIgAiIgDqdseA3W4nSSaTUSqV+g0wGAwiCEII4dI0rRkMhsHGGF5/bdt+CA8Mh8NQTdO8ATg4ODg0mUxyAcYYKJVKhEIh9fUvQghuHMPh4eFDQ0ND3gFc143+/v7T0dHxP/cDwF+g83g87g8ODg4MBoPDw8PDf0xPT9/c3Nw8MjIyMj4+3t/b2/t3AHDW7/ffAEZGRkZHR0dHR0dGRkZGJiYm3gBcfwEFXchKqX2rwwAAAABJRU5ErkJggg=="

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

# --- NEW: Globální proměnné pro Watchdog a historii grafu ---
last_frame_time = datetime.now()
watchdog_lock = threading.Lock()
data_history = deque(maxlen=200) # Historie pro 200 posledních měření
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
shared_state = {"frate": DEFAULT_FRAME_RATE, "range_key": DEFAULT_RANGE_KEY, "peak_threshold": DEFAULT_PEAK_THRESHOLD, "reconfigure": True }
state_lock = threading.Lock()

def log_and_broadcast(level, message, loop):
    log_message = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level.upper()}] {message}"
    print(log_message)
    if not loop.is_closed(): asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "log", "level": level, "message": log_message}), loop)

def run_radar_loop(loop: asyncio.AbstractEventLoop):
    global last_frame_time, data_history
    def broadcast_sync(message: dict):
        program_uptime = datetime.now() - START_TIME
        message['program_uptime'] = str(program_uptime).split('.')[0]
        if not loop.is_closed(): asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    
    time.sleep(1)
    smoothed_distance, smoothed_speed, device, connection_start_time = None, None, None, None
        
    while True:
        try:
            with state_lock:
                reconfigure_needed = shared_state["reconfigure"]
                if reconfigure_needed:
                    if device:
                        try: device.stop_acquisition()
                        except Exception: pass
                    device = None; shared_state["reconfigure"] = False

            if device is None:
                log_and_broadcast("info", "Pokus o připojení k radaru...", loop)
                device = DeviceFmcw()
                connection_start_time = datetime.now()
                log_and_broadcast("success", f"Radar připojen: {device.get_sensor_type()}.", loop)
                with state_lock:
                    frate = shared_state['frate']
                    range_key = shared_state['range_key']
                log_and_broadcast("info", f"Konfigurace: {range_key} @ {frate} Hz", loop)
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
            
            frame_contents = device.get_next_frame()
            with watchdog_lock: last_frame_time = datetime.now()
            
            antenna_samples = frame_contents[0][0, :, :]
            distance_m, speed_ms, peak_value = algo.compute_doppler_map(antenna_samples)
            
            with state_lock: current_peak_threshold = shared_state['peak_threshold']
            
            data_payload = {}
            sensor_uptime = datetime.now() - connection_start_time
            data_payload['sensor_uptime'] = str(sensor_uptime).split('.')[0]
            direction = "---"

            if peak_value >= current_peak_threshold:
                distance_cm = distance_m * 100
                if smoothed_distance is None: smoothed_distance, smoothed_speed = distance_cm, speed_ms
                else:
                    smoothed_distance = EMA_ALPHA * distance_cm + (1 - EMA_ALPHA) * smoothed_distance
                    smoothed_speed = EMA_ALPHA * speed_ms + (1 - EMA_ALPHA) * smoothed_speed
                if abs(smoothed_speed) < metrics.speed_resolution_m_s: direction = "Statický"
                elif smoothed_speed < 0: direction = "Přibližování"
                else: direction = "Vzdalování"
                data_payload.update({"status": "connected", "distance_cm": smoothed_distance, "speed_ms": smoothed_speed, "direction": direction, "peak": peak_value})
            else:
                data_payload.update({"status": "connected", "distance_cm": 0.0, "speed_ms": 0.0, "direction": "---", "peak": 0.0})
            
            broadcast_sync(data_payload)
            with watchdog_lock:
                data_history.append((data_payload['distance_cm'], direction))
        
        except Exception as e:
            log_and_broadcast("error", f"Smyčka radaru selhala: {e}", loop)
            broadcast_sync({"status": "waiting_for_device", "program_uptime": str(datetime.now() - START_TIME).split('.')[0]})
            smoothed_distance, smoothed_speed, device, connection_start_time = None, None, None, None
            time.sleep(3)

# ===========================================================================
# Watchdog vlákno
# ===========================================================================
def watchdog_thread_func():
    while True:
        time.sleep(WATCHDOG_TIMEOUT_S / 2)
        with watchdog_lock:
            time_since_last_frame = datetime.now() - last_frame_time
        if time_since_last_frame.total_seconds() > WATCHDOG_TIMEOUT_S:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [FATAL] WATCHDOG: Restartuji aplikaci z důvodu zamrznutí radarového vlákna.")
            os.execv(sys.executable, ['python'] + sys.argv)
# ===========================================================================

# ===========================================================================
# FastAPI App a Endpoints
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    
    watchdog_thread = threading.Thread(target=watchdog_thread_func, daemon=True)
    watchdog_thread.start()

    radar_thread = threading.Thread(target=run_radar_loop, args=(loop,), daemon=True)
    radar_thread.start()
    yield
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] [INFO] Server se vypíná.")

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

@app.get("/lang/{lang_code}", response_class=JSONResponse)
async def get_lang(lang_code: str):
    return LANGUAGES.get(lang_code, LANGUAGES["en"])

# --- NEW: Endpoint pro vykreslování grafu ---
@app.get("/plot.jpeg")
async def get_plot():
    with watchdog_lock:
        history = list(data_history)
        with state_lock:
            range_key = shared_state['range_key']
    
    max_range_m, _ = RANGE_PRESETS[range_key]
    max_range_cm = max_range_m * 100

    fig, ax = plt.subplots(figsize=(8, 2), dpi=90)
    fig.patch.set_facecolor('#E9ECEF') # Světlé pozadí
    ax.set_facecolor('#FFFFFF') # Bílé pozadí grafu

    if document.body.classList.contains('dark-mode'): # Jednoduchá kontrola, lepší řešení by vyžadovalo parametr
        fig.patch.set_facecolor('#333333')
        ax.set_facecolor('#1E1E1E')
        ax.tick_params(colors='white')
        ax.spines['bottom'].set_color('white')
        ax.spines['left'].set_color('white')

    ax.set_ylim(0, max_range_cm)
    ax.set_xlim(0, data_history.maxlen)
    ax.get_xaxis().set_visible(False)
    ax.set_ylabel('Vzdálenost (cm)', color='gray')

    colors = {'Přibližování': '#198754', 'Vzdalování': '#dc3545', 'Statický': '#0d6efd', '---': '#6c757d'}

    for i in range(1, len(history)):
        y1, dir1 = history[i-1]
        y2, dir2 = history[i]
        color = colors.get(dir2, '#6c757d')
        ax.plot([i-1, i], [y1, y2], color=color, linewidth=2)
        
    buf = io.BytesIO()
    fig.savefig(buf, format='jpeg', bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    
    return StreamingResponse(buf, media_type="image/jpeg")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await websocket.receive_json()
            with state_lock:
                reconfigure_needed = False
                if data.get('range_key') != shared_state.get('range_key'):
                    shared_state['range_key'] = data['range_key']; reconfigure_needed = True
                if data.get('frate') != shared_state.get('frate'):
                    shared_state['frate'] = data['frate']; reconfigure_needed = True
                if 'sensitivity' in data:
                    shared_state['peak_threshold'] = data['sensitivity']
                if reconfigure_needed:
                    shared_state['reconfigure'] = True
                    log_and_broadcast("info", f"Přijata nová konfigurace: {shared_state}", loop)
    except (WebSocketDisconnect, asyncio.CancelledError):
        manager.disconnect(websocket)

# ===========================================================================
# Spuštění
# ===========================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Radar Web Server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    cli_args = parser.parse_args()
    try:
        uvicorn.run(app, host=cli_args.host, port=cli_args.port)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] [INFO] Ukončeno uživatelem (Ctrl+C).")