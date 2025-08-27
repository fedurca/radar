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
import argparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from starlette.websockets import WebSocketDisconnect
from collections import deque
import psutil
import os

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
DEFAULT_NUM_CHIRPS = 32
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
    "en": {"title": "Radar Live View", "status_connecting": "Connecting...", "status_connected_server": "Server Connected", "status_waiting": "Waiting for Device...", "status_reconfiguring": "Reconfiguring Radar...", "status_connected_device": "Connected", "status_disconnected_server": "SERVER DISCONNECTED - Reconnecting...", "header": "Live Radar Feed", "range_label": "Range:", "frate_label": "Frequency:", "sensitivity_label": "Sensitivity:", "num_chirps_label": "Chirps per Frame:", "distance": "Distance", "speed": "Speed", "direction": "Direction", "peak_signal": "Peak Signal", "sensor_uptime": "Sensor Uptime", "program_uptime": "Program Uptime", "log_header": "Diagnostic Log", "static": "Static", "approaching": "Approaching", "receding": "Receding", "toggle_theme": "Toggle Theme", "lang_toggle": "Česky", "hold_label": "Hold Last Value", "save_plot": "Save Plot as JPEG", "cpu_usage": "CPU Usage", "ram_usage": "Memory Usage", "pause_plot": "Pause Plot", "resume_plot": "Resume Plot", "min_label": "Min", "max_label": "Max"},
    "cz": {"title": "Radar Live Vizualizace", "status_connecting": "Připojování...", "status_connected_server": "Server připojen", "status_waiting": "Čekání na zařízení...", "status_reconfiguring": "Rekonfigurace radaru...", "status_connected_device": "Připojeno", "status_disconnected_server": "SERVER ODPOJEN - Pokus o znovupřipojení...", "header": "Živá data z radaru", "range_label": "Rozsah:", "frate_label": "Frekvence:", "sensitivity_label": "Citlivost:", "num_chirps_label": "Chirpů na snímek:", "distance": "Vzdálenost", "speed": "Rychlost", "direction": "Směr", "peak_signal": "Síla signálu", "sensor_uptime": "Doba připojení", "program_uptime": "Doba běhu", "log_header": "Diagnostický Log", "static": "Statický", "approaching": "Přibližování", "receding": "Vzdalování", "toggle_theme": "Přepnout vzhled", "lang_toggle": "English", "hold_label": "Podržet poslední hodnotu", "save_plot": "Uložit graf jako JPEG", "cpu_usage": "Využití CPU", "ram_usage": "Využití paměti", "pause_plot": "Pozastavit graf", "resume_plot": "Spustit graf", "min_label": "Min", "max_label": "Max"}
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
            --color-approaching: #198754; --color-receding: #dc3545; --color-static: #0d6efd; --color-none: transparent;
        }
        body.dark-mode {
            --bg-color: #121212; --text-color: #e0e0e0; --card-bg-color: #1e1e1e;
            --primary-color: #4dabf7; --secondary-bg-color: #333333; --secondary-text-color: #adb5bd;
            --border-color: #444444; --shadow-color: rgba(0, 0, 0, 0.4); --icon-fill: #adb5bd;
            --color-approaching: #28a745; --color-receding: #ff4d4f; --color-static: #4dabf7; --color-none: #333333;
        }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg-color); color: var(--text-color); transition: background-color 0.3s, color 0.3s; }
        .main-container { display: flex; flex-direction: column; height: 100%; width: 100%; padding: 1rem; }
        header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 1rem; flex-shrink: 0; border-bottom: 1px solid var(--border-color); }
        .title-area { text-align: left; }
        .title-area h1 { color: var(--primary-color); margin: 0; font-size: 1.5rem; }
        #status { font-weight: bold; padding: 0.5rem; border-radius: 6px; transition: all 0.3s ease-in-out; display: inline-block; margin-top: 5px; }
        .status-connected { color: #198754; background-color: #d1e7dd; }
        .status-disconnected { color: #dc3545; background-color: #f8d7da; }
        .top-controls { display: flex; gap: 1rem; align-items: center; }
        .top-controls button { font-size: 0.8rem; padding: 0.5rem; cursor: pointer; background-color: var(--secondary-bg-color); border: 1px solid var(--border-color); color: var(--text-color); border-radius: 6px; }
        .content-area { display: flex; flex: 1; gap: 1rem; overflow: hidden; padding-top: 1rem; }
        .left-panel, .right-panel { background: var(--card-bg-color); padding: 1.5rem; border-radius: 8px; box-shadow: 0 4px 12px var(--shadow-color); display: flex; flex-direction: column; }
        .left-panel { flex: 2; } .right-panel { flex: 1; }
        .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; text-align: left; }
        .control-group { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
        .control-group-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; grid-column: 1 / -1; }
        .controls select, .controls input { font-size: 1rem; padding: 0.5rem; border-radius: 6px; border: 1px solid var(--border-color); background-color: var(--card-bg-color); color: var(--text-color); width: 100%; }
        input[type="checkbox"] { width: auto; }
        .data-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1.5rem; }
        .metric { background: var(--secondary-bg-color); padding: 1rem; border-radius: 6px; text-align: center; }
        .metric-label { font-size: 0.9rem; color: var(--secondary-text-color); }
        .metric-value { font-size: 1.8rem; font-weight: bold; color: var(--text-color); }
        .mini-bargraph-container { width: 100%; height: 10px; background-color: var(--border-color); border-radius: 5px; margin-top: 0.75rem; overflow: hidden; }
        .mini-bargraph-bar { height: 100%; width: 0%; border-radius: 5px; transition: width 0.1s linear, background-color 0.3s; }
        .min-max { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 8px; display: flex; justify-content: space-between; padding: 0 5px; }
        .sector-grid { display: grid; grid-template-columns: repeat(64, 1fr); gap: 1px; width: 100%; height: 40px; margin-top: 1.5rem; border: 1px solid var(--border-color); border-radius: 6px; padding: 2px; }
        .sector { background-color: var(--secondary-bg-color); border-radius: 1px; transition: background-color 0.2s; }
        .bargraph-axis { display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--secondary-text-color); padding: 0 5px; margin-top: 5px; }
        #plot-container { margin-top: 1.5rem; }
        #plot-canvas { width: 100%; height: 120px; background-color: var(--secondary-bg-color); border-radius: 6px; border: 1px solid var(--border-color); }
        .plot-controls { text-align: right; margin-top: 0.5rem; }
        .plot-controls button { font-size: 0.8rem; padding: 0.5rem; cursor: pointer; background-color: var(--secondary-bg-color); border: 1px solid var(--border-color); color: var(--text-color); border-radius: 6px; margin-left: 0.5rem; }
        #log-container { display: flex; flex-direction: column; height: 100%; }
        #log-header { margin-top: 0; text-align: left; }
        #log { flex: 1; background: var(--secondary-bg-color); color: var(--text-color); padding: 1rem; border-radius: 6px; overflow-y: scroll; font-family: "SF Mono", "Menlo", monospace; font-size: 0.8rem; text-align: left; }
        .log-entry { border-bottom: 1px solid var(--border-color); padding-bottom: 4px; margin-bottom: 4px; word-break: break-all; }
        .log-entry.error { color: var(--color-receding); } .log-entry.success { color: var(--color-approaching); } .log-entry.data { opacity: 0.7; }
        .approaching { background-color: var(--color-approaching); }
        .receding { background-color: var(--color-receding); }
        .static { background-color: var(--color-static); }
        .none { background-color: var(--secondary-bg-color); }
        #theme-toggle { background: transparent; border: none; width: 40px; height: 40px; cursor: pointer; }
        #theme-toggle svg { width: 24px; height: 24px; fill: var(--icon-fill); }
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
                <button id="theme-toggle" data-lang="toggle_theme" title="Toggle Theme">
                    <svg class="sun-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zm-9-7c.55 0 1-.45 1-1V3c0-.55-.45-1-1-1s-1 .45-1 1v2c0 .55.45 1 1 1zm0 12v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.64 7.05l-1.41-1.41c-.39-.39-1.02-.39-1.41 0s-.39 1.02 0 1.41l1.41 1.41c.39.39 1.02.39 1.41 0s.39-1.02 0-1.41zm12.72 12.72l-1.41-1.41c-.39-.39-1.02-.39-1.41 0s-.39 1.02 0 1.41l1.41 1.41c.39.39 1.02.39 1.41 0s.39-1.02 0-1.41zM19.78 5.64l-1.41 1.41c-.39-.39-.39 1.02 0 1.41s1.02.39 1.41 0l1.41-1.41c.39-.39.39-1.02 0-1.41s-1.02-.39-1.41 0zM7.05 19.78l-1.41 1.41c-.39-.39-.39 1.02 0 1.41s1.02.39 1.41 0l1.41-1.41c.39-.39.39-1.02 0-1.41s-1.02-.39-1.41 0z"/></svg>
                    <svg class="moon-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M9.37 5.51A7.35 7.35 0 0 0 9.1 7.5c0 4.08 3.32 7.4 7.4 7.4.68 0 1.35-.09 2-.26-1.43 1.1-3.24 1.76-5.14 1.76A8.01 8.01 0 0 1 4.38 8.28c.17-.35.36-.69.57-1.02.43-.68.95-1.29 1.54-1.82.59-.53 1.25-1 1.95-1.39.02 0 .04-.01.06-.01.02 0 .03 0 .05.01.27.06.53.13.78.22.02.01.03.02.05.03.25.1.49.2.73.32.23.11.45.23.67.35.21.12.42.24.62.37.2.13.39.26.58.39.2.14.38.28.56.43.18.15.35.3.52.46.16.16.32.32.47.49.15.17.29.35.43.53zm-3.82 9.67c.88.54 1.86.95 2.91 1.18.42.09.85.16 1.28.21.3.03.6.06.9.06 1.75 0 3.39-.59 4.67-1.58-.57.25-1.17.45-1.79.6-1.52.37-3.09.56-4.69.56-2.9 0-5.63-.94-7.79-2.58.23.01.46.02.69.02.83 0 1.64-.15 2.4-.44z"/></svg>
                </button>
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
                        <label for="num-chirps-slider" data-lang="num_chirps_label">Chirps per Frame:</label>
                        <input type="range" id="num-chirps-slider" min="8" max="64" step="4" value="32">
                        <span id="num-chirps-value">32</span>
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
                    <div class="metric">
                        <div class="metric-label" data-lang="distance">Distance</div>
                        <span id="distance" class="metric-value">---</span> cm
                        <div class="min-max"><span data-lang="min_label">Min</span>: <span id="min_dist">--</span> | <span data-lang="max_label">Max</span>: <span id="max_dist">--</span></div>
                        <div class="mini-bargraph-container"><div id="distance-mini-bar" class="mini-bargraph-bar"></div></div>
                    </div>
                    <div class="metric">
                        <div class="metric-label" data-lang="speed">Speed</div>
                        <span id="speed" class="metric-value">---</span> m/s
                        <div class="min-max"><span data-lang="min_label">Min</span>: <span id="min_speed">--</span> | <span data-lang="max_label">Max</span>: <span id="max_speed">--</span></div>
                        <div class="mini-bargraph-container"><div id="speed-mini-bar" class="mini-bargraph-bar"></div></div>
                    </div>
                    <div class="metric">
                        <div class="metric-label" data-lang="peak_signal">Peak Signal</div>
                        <span id="peak" class="metric-value">---</span>
                        <div class="min-max"><span data-lang="min_label">Min</span>: <span id="min_peak">--</span> | <span data-lang="max_label">Max</span>: <span id="max_peak">--</span></div>
                        <div class="mini-bargraph-container"><div id="peak-mini-bar" class="mini-bargraph-bar"></div></div>
                    </div>
                </div>
                <div id="sector-grid" class="sector-grid"></div>
                <div class="bargraph-axis"><span id="axis-start">0cm</span><span id="axis-end"></span></div>
                <div id="plot-container">
                    <canvas id="plot-canvas"></canvas>
                    <div class="plot-controls">
                        <button id="pause-plot-btn" data-lang="pause_plot">Pause Plot</button>
                        <button id="save-plot-btn" data-lang="save_plot">Save Plot as JPEG</button>
                    </div>
                </div>
            </div>
            <div class="right-panel" id="log-container">
                 <div class="data-grid" style="grid-template-columns: 1fr 1fr; margin-top: 0;">
                    <div class="metric"><div class="metric-label" data-lang="direction">Direction</div><span id="direction" class="metric-value">---</span></div>
                    <div class="metric"><div class="metric-label" data-lang="sensor_uptime">Sensor Uptime</div><span id="sensor_uptime" class="metric-value">---</span></div>
                </div>
                 <div class="data-grid" style="grid-template-columns: 1fr; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border-color);">
                    <div class="metric"><div class="metric-label" data-lang="program_uptime">Program Uptime</div><span id="program_uptime" class="metric-value">---</span></div>
                    <div class="metric"><div class="metric-label" data-lang="cpu_usage">CPU Usage</div><span id="cpu" class="metric-value">---</span> %<div class="mini-bargraph-container"><div id="cpu-mini-bar" class="mini-bargraph-bar"></div></div></div>
                    <div class="metric"><div class="metric-label" data-lang="ram_usage">RAM Usage</div><span id="ram" class="metric-value">---</span> MB<div class="mini-bargraph-container"><div id="ram-mini-bar" class="mini-bargraph-bar"></div></div></div>
                </div>
                <h2 id="log-header" data-lang="log_header">Diagnostic Log</h2>
                <div id="log"></div>
            </div>
        </div>
    </div>

    <script>
        (() => {
            const ui = { status: document.getElementById('status'), distance: document.getElementById('distance'), speed: document.getElementById('speed'), direction: document.getElementById('direction'), peak: document.getElementById('peak'), sensor_uptime: document.getElementById('sensor_uptime'), program_uptime: document.getElementById('program_uptime'), cpu: document.getElementById('cpu'), ram: document.getElementById('ram'), bar_dist: document.getElementById('distance-mini-bar'), bar_speed: document.getElementById('speed-mini-bar'), bar_peak: document.getElementById('peak-mini-bar'), bar_cpu: document.getElementById('cpu-mini-bar'), bar_ram: document.getElementById('ram-mini-bar'), log: document.getElementById('log'), rangeSelector: document.getElementById('range-selector'), frateSelector: document.getElementById('frate-selector'), sensitivitySlider: document.getElementById('sensitivity-slider'), sensitivityValue: document.getElementById('sensitivity-value'), numChirpsSlider: document.getElementById('num-chirps-slider'), numChirpsValue: document.getElementById('num-chirps-value'), themeToggle: document.getElementById('theme-toggle'), langToggle: document.getElementById('lang-toggle'), holdToggle: document.getElementById('hold-toggle'), plotCanvas: document.getElementById('plot-canvas'), savePlotBtn: document.getElementById('save-plot-btn'), pausePlotBtn: document.getElementById('pause-plot-btn'), min_dist: document.getElementById('min_dist'), max_dist: document.getElementById('max_dist'), min_speed: document.getElementById('min_speed'), max_speed: document.getElementById('max_speed'), min_peak: document.getElementById('min_peak'), max_peak: document.getElementById('max_peak'), sectorGrid: document.getElementById('sector-grid'), axisStart: document.getElementById('axis-start'), axisEnd: document.getElementById('axis-end') };
            let maxDistanceCm = 800, maxSpeedMs = 3, maxPeak = 10, maxRamMb = 256, langDict = {}, lastValidData = null, isPlotPaused = false;
            const rangePresets = __RANGE_PRESETS__, defaultRange = "__DEFAULT_RANGE_KEY__";
            const frameRates = __FRAME_RATES__, defaultFrameRate = __DEFAULT_FRAME_RATE__, defaultNumChirps = __DEFAULT_NUM_CHIRPS__;
            const plotCtx = ui.plotCanvas.getContext('2d');
            
            for(let i=0; i<64; i++) { const sector = document.createElement('div'); sector.className = 'sector'; ui.sectorGrid.appendChild(sector); }
            
            async function setLanguage(lang) {
                try {
                    const response = await fetch(`/lang/${lang}`);
                    langDict = await response.json();
                    document.querySelectorAll('[data-lang]').forEach(el => { const key = el.getAttribute('data-lang'); if (langDict[key]) el.textContent = langDict[key]; });
                    document.documentElement.lang = lang; localStorage.setItem('language', lang);
                } catch (e) { console.error("Could not set language", e); }
            }
            ui.langToggle.addEventListener('click', () => { setLanguage(document.documentElement.lang === 'en' ? 'cz' : 'en'); });

            function applyTheme(theme) { document.body.classList.toggle('dark-mode', theme === 'dark'); }
            ui.themeToggle.addEventListener('click', () => { const newTheme = document.body.classList.contains('dark-mode') ? 'light' : 'dark'; localStorage.setItem('theme', newTheme); applyTheme(newTheme); });
            
            rangePresets.forEach(key => { const option = document.createElement('option'); option.value = key; option.textContent = key; if (key === defaultRange) option.selected = true; ui.rangeSelector.appendChild(option); });
            frameRates.forEach(rate => { const option = document.createElement('option'); option.value = rate; option.textContent = rate + ' Hz'; if (rate === defaultFrameRate) option.selected = true; ui.frateSelector.appendChild(option); });
            ui.numChirpsSlider.value = defaultNumChirps; ui.numChirpsValue.textContent = defaultNumChirps;

            function sendConfig(ws) {
                if (ws.readyState !== WebSocket.OPEN) return;
                const rangeKey = ui.rangeSelector.value, frate = parseInt(ui.frateSelector.value, 10), sensitivity = parseFloat(ui.sensitivitySlider.value), num_chirps = parseInt(ui.numChirpsSlider.value, 10);
                const rangeValueStr = rangeKey.split('m')[0].replace(',', '.');
                maxDistanceCm = parseFloat(rangeValueStr) * 100;
                ui.axisEnd.textContent = maxDistanceCm.toFixed(0) + 'cm';
                ws.send(JSON.stringify({ action: 'reconfigure', range_key: rangeKey, frate: frate, sensitivity: sensitivity, num_chirps: num_chirps }));
            }
            
            function drawPlot(history) {
                const w = ui.plotCanvas.width, h = ui.plotCanvas.height;
                const isDarkMode = document.body.classList.contains('dark-mode');
                const colors = { static: getComputedStyle(document.documentElement).getPropertyValue('--color-static'), approaching: getComputedStyle(document.documentElement).getPropertyValue('--color-approaching'), receding: getComputedStyle(document.documentElement).getPropertyValue('--color-receding') };
                
                plotCtx.fillStyle = isDarkMode ? '#1e1e1e' : '#ffffff';
                plotCtx.fillRect(0, 0, w, h);
                plotCtx.lineWidth = 0.5;
                plotCtx.strokeStyle = isDarkMode ? '#555' : '#ccc';
                plotCtx.fillStyle = isDarkMode ? '#adb5bd' : '#6c757d';
                plotCtx.font = "12px sans-serif";
                
                const y_padding = 30;
                plotCtx.beginPath(); plotCtx.moveTo(y_padding, 0); plotCtx.lineTo(y_padding, h-20); plotCtx.stroke();
                plotCtx.textAlign = "right"; plotCtx.textBaseline = "middle";
                [0, 0.5, 1].forEach(p => { const y = h - 20 - (p * (h-20)); plotCtx.fillText((p * maxDistanceCm).toFixed(0), y_padding-5, y); });
                const frate = parseInt(ui.frateSelector.value, 10) || 20;
                const timeSpan = history.length / frate;
                plotCtx.beginPath(); plotCtx.moveTo(y_padding, h-20); plotCtx.lineTo(w, h-20); plotCtx.stroke();
                plotCtx.textAlign = "center"; plotCtx.textBaseline = "top";
                 [0, 0.5, 1].forEach(p => { const x = y_padding + (p * (w-y_padding)); plotCtx.fillText(`-${(timeSpan * (1-p)).toFixed(1)}s`, x, h-15); });

                if (!history || history.length < 2) return;
                plotCtx.lineWidth = 2;
                for (let i = 1; i < history.length; i++) {
                    const [y1, dir1] = history[i-1], [y2, dir2] = history[i];
                    const x1 = y_padding + (i-1)/(history.length-1)*(w-y_padding), x2 = y_padding + i/(history.length-1)*(w-y_padding);
                    const v1 = (h-20) - (y1/maxDistanceCm)*(h-20), v2 = (h-20) - (y2/maxDistanceCm)*(h-20);
                    let dirClass = 'static';
                    if(dir2 === 'Přibližování' || dir2 === 'Approaching') dirClass = 'approaching';
                    else if(dir2 === 'Vzdalování' || dir2 === 'Receding') dirClass = 'receding';
                    plotCtx.strokeStyle = colors[dirClass];
                    plotCtx.beginPath(); plotCtx.moveTo(x1,v1); plotCtx.lineTo(x2,v2); plotCtx.stroke();
                }
            }

            function updateUI(data) {
                ui.program_uptime.textContent = data.program_uptime || '---';
                ui.sensor_uptime.textContent = data.sensor_uptime || '---';
                const statusKey = (data.status || 'connecting').replace(/ /g, '_');
                ui.status.textContent = langDict[`status_${statusKey}`] || data.status;
                ui.status.className = data.status === 'connected' ? 'status-connected' : 'status-disconnected';
                
                const isDataValid = data.status === 'connected' && data.peak > 0;
                const displayData = isDataValid ? data : (ui.holdToggle.checked && lastValidData) ? lastValidData : data;

                if(isDataValid) { lastValidData = data; } 
                else if(ui.holdToggle.checked && lastValidData) {
                    lastValidData.program_uptime = data.program_uptime;
                    lastValidData.sensor_uptime = data.sensor_uptime;
                }

                if (displayData) {
                    ui.distance.textContent = displayData.distance_cm.toFixed(1);
                    ui.speed.textContent = displayData.speed_ms.toFixed(2);
                    ui.direction.textContent = langDict[displayData.direction.toLowerCase()] || displayData.direction;
                    ui.peak.textContent = displayData.peak.toFixed(4);
                    ui.cpu.textContent = data.cpu_percent.toFixed(1);
                    ui.ram.textContent = data.ram_mb.toFixed(1);

                    ui.min_dist.textContent = (displayData.min_dist_cm === null ? '--' : displayData.min_dist_cm.toFixed(1)); ui.max_dist.textContent = (displayData.max_dist_cm || 0).toFixed(1);
                    ui.min_speed.textContent = (displayData.min_speed_ms === null ? '--' : displayData.min_speed_ms.toFixed(2)); ui.max_speed.textContent = (displayData.max_speed_ms || 0).toFixed(2);
                    ui.min_peak.textContent = (displayData.min_peak === null ? '--' : displayData.min_peak.toFixed(4)); ui.max_peak.textContent = (displayData.max_peak || 0).toFixed(4);

                    const dist_p = Math.min(100, Math.max(0, (displayData.distance_cm / maxDistanceCm) * 100));
                    const speed_p = Math.min(100, Math.max(0, (Math.abs(displayData.speed_ms) / maxSpeedMs) * 100));
                    const peak_p = Math.min(100, Math.max(0, (displayData.peak / maxPeak) * 100));
                    
                    ui.bar_dist.style.width = `${dist_p}%`; ui.bar_speed.style.width = `${speed_p}%`;
                    ui.bar_peak.style.width = `${peak_p}%`; ui.bar_cpu.style.width = `${data.cpu_percent}%`;
                    ui.bar_ram.style.width = `${Math.min(100, data.ram_mb / 256 * 100)}%`;

                    let cssClass = 'static';
                    if(displayData.direction === 'Přibližování' || displayData.direction === 'Approaching') cssClass = 'approaching';
                    else if (displayData.direction === 'Vzdalování' || displayData.direction === 'Receding') cssClass = 'receding';
                    
                    [ui.bar_dist, ui.bar_speed, ui.bar_peak].forEach(el => {
                        el.classList.remove('approaching', 'receding', 'static');
                        el.classList.add(cssClass);
                    });
                    
                    ui.sectorGrid.childNodes.forEach((sector, i) => {
                        sector.className = 'sector ' + (displayData.sector_states[i] || 'none');
                    });
                }
                
                if(!isPlotPaused) { drawPlot(data.history); }
            }

            function connect() {
                const ws = new WebSocket(`ws://${window.location.host}/ws`);
                
                ws.onopen = () => { setLanguage(localStorage.getItem('language') || 'en').then(() => sendConfig(ws)); };
                ui.rangeSelector.onchange = () => sendConfig(ws);
                ui.frateSelector.onchange = () => sendConfig(ws);
                ui.numChirpsSlider.oninput = () => { ui.numChirpsValue.textContent = ui.numChirpsSlider.value; };
                ui.numChirpsSlider.onchange = () => sendConfig(ws);
                ui.sensitivitySlider.oninput = () => { ui.sensitivityValue.textContent = ui.sensitivitySlider.value; };
                ui.sensitivitySlider.onchange = () => sendConfig(ws);
                ui.savePlotBtn.onclick = () => { const link = document.createElement('a'); link.download = `radar_plot_${new Date().toISOString()}.jpeg`; link.href = ui.plotCanvas.toDataURL('image/jpeg', 0.9); link.click(); };
                ui.pausePlotBtn.onclick = () => { isPlotPaused = !isPlotPaused; ui.pausePlotBtn.textContent = isPlotPaused ? (langDict.resume_plot || 'Resume Plot') : (langDict.pause_plot || 'Pause Plot'); };

                function addLog(msg, type = 'info') {
                    const entry = document.createElement('div');
                    entry.className = `log-entry ${type}`;
                    entry.textContent = msg;
                    ui.log.prepend(entry);
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
            connect();
        })();
    </script>
</body>
</html>
"""
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
shared_state = {"frate": DEFAULT_FRAME_RATE, "range_key": DEFAULT_RANGE_KEY, "peak_threshold": DEFAULT_PEAK_THRESHOLD, "num_chirps": DEFAULT_NUM_CHIRPS, "reconfigure": True }
state_lock = threading.Lock()
data_history = deque(maxlen=200)
process = psutil.Process(os.getpid())
sector_states = ['none'] * 64

def log_and_broadcast(level, message, loop):
    log_message = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level.upper()}] {message}"
    print(log_message)
    if not loop.is_closed():
        asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "log", "level": level, "message": log_message}), loop)

def run_radar_loop(loop: asyncio.AbstractEventLoop):
    global data_history, sector_states
    def broadcast_sync(message: dict):
        program_uptime = datetime.now() - START_TIME
        message['program_uptime'] = str(program_uptime).split('.')[0]
        with state_lock:
            message['history'] = list(data_history)
            message['sector_states'] = list(sector_states)
        if not loop.is_closed(): asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    
    time.sleep(1)
    smoothed_distance, smoothed_speed, device, connection_start_time = None, None, None, None
    stats = {}
    def reset_stats(): return { 'min_dist_cm': None, 'max_dist_cm': 0, 'min_speed_ms': None, 'max_speed_ms': 0, 'min_peak': None, 'max_peak': 0 }
    stats = reset_stats()
        
    while True:
        try:
            with state_lock:
                reconfigure_needed = shared_state["reconfigure"]
                if reconfigure_needed:
                    if device:
                        try: device.stop_acquisition()
                        except Exception: pass
                    device = None; shared_state["reconfigure"] = False
                    stats = reset_stats(); sector_states = ['none'] * 64

            if device is None:
                log_and_broadcast("info", "Pokus o připojení k radaru...", loop)
                device = DeviceFmcw()
                connection_start_time = datetime.now()
                log_and_broadcast("success", f"Radar připojen: {device.get_sensor_type()}.", loop)
                with state_lock:
                    frate, range_key, num_chirps = shared_state['frate'], shared_state['range_key'], shared_state['num_chirps']
                
                max_range, range_res = RANGE_PRESETS[range_key]
                metrics = FmcwMetrics(range_resolution_m=range_res, max_range_m=max_range, max_speed_m_s=3, speed_resolution_m_s=0.2, center_frequency_Hz=60_750_000_000)
                sequence = device.create_simple_sequence(FmcwSimpleSequenceConfig())
                
                chirp_loop_temp = sequence.loop.sub_sequence.contents
                device.sequence_from_metrics(metrics, chirp_loop_temp)
                try: chirp_duration_s = chirp_loop_temp.loop.sub_sequence.contents.chirp.duration_s
                except AttributeError: chirp_duration_s = 0.000128
                
                required_time_s = chirp_duration_s * num_chirps
                available_time_s = 1.0 / frate
                
                if required_time_s > available_time_s * 0.9:
                    original_chirps = num_chirps
                    num_chirps = int((available_time_s * 0.9) / chirp_duration_s)
                    log_and_broadcast("warning", f"Konfigurace nekompatibilní. Počet chirpů snížen z {original_chirps} na {num_chirps}.", loop)
                    with state_lock: shared_state['num_chirps'] = num_chirps
                
                log_and_broadcast("info", f"Finální konfigurace: {range_key} @ {frate} Hz, Chirps: {num_chirps}", loop)
                sequence.loop.repetition_time_s = 1 / frate
                chirp_loop = sequence.loop.sub_sequence.contents
                device.sequence_from_metrics(metrics, chirp_loop)
                chirp_loop.loop.num_repetitions = num_chirps
                chirp = chirp_loop.loop.sub_sequence.contents.chirp
                chirp.sample_rate_Hz=1_000_000; chirp.rx_mask=1; chirp.tx_mask=1; chirp.tx_power_level=31
                chirp.if_gain_dB=33; chirp.lp_cutoff_Hz=500000; chirp.hp_cutoff_Hz=80000
                device.set_acquisition_sequence(sequence)
                device.start_acquisition()
                algo = DopplerAlgo(chirp.num_samples, chirp_loop.loop.num_repetitions, metrics)
            
            frame_contents = device.get_next_frame()
            antenna_samples = frame_contents[0][0, :, :]
            distance_m, speed_ms, peak_value = algo.compute_doppler_map(antenna_samples)
            
            with state_lock: current_peak_threshold = shared_state['peak_threshold']
            
            data_payload = {}
            sensor_uptime = datetime.now() - connection_start_time
            data_payload['sensor_uptime'] = str(sensor_uptime).split('.')[0]
            data_payload['cpu_percent'] = psutil.cpu_percent()
            data_payload['ram_mb'] = process.memory_info().rss / (1024 * 1024)
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
                
                stats['max_dist_cm'] = max(stats['max_dist_cm'], smoothed_distance)
                stats['max_speed_ms'] = max(stats['max_speed_ms'], smoothed_speed)
                stats['max_peak'] = max(stats['max_peak'], peak_value)
                if stats['min_dist_cm'] is None or smoothed_distance < stats['min_dist_cm']: stats['min_dist_cm'] = smoothed_distance
                if stats['min_speed_ms'] is None or smoothed_speed < stats['min_speed_ms']: stats['min_speed_ms'] = smoothed_speed
                if stats['min_peak'] is None or peak_value < stats['min_peak']: stats['min_peak'] = peak_value
                
                data_payload.update({"status": "connected", "distance_cm": smoothed_distance, "speed_ms": smoothed_speed, "direction": direction, "peak": peak_value, **stats})
                log_string = (f"Vzdálenost: {data_payload['distance_cm']:.1f}cm, Rychlost: {data_payload['speed_ms']:+.2f}m/s, Směr: {direction}, Peak: {peak_value:.4f}")
                log_and_broadcast("data", log_string, loop)
                with state_lock:
                    data_history.append((smoothed_distance, direction))
                    max_range_cm_current = RANGE_PRESETS[shared_state['range_key']][0] * 100
                    sector_index = int((smoothed_distance / max_range_cm_current) * 64)
                    css_direction = 'static'
                    if direction == "Přibližování": css_direction = "approaching"
                    elif direction == "Vzdalování": css_direction = "receding"
                    if 0 <= sector_index < 64: sector_states[sector_index] = css_direction
            else:
                data_payload.update({"status": "connected", "distance_cm": 0.0, "speed_ms": 0.0, "direction": "---", "peak": 0.0, **stats})
                with state_lock: data_history.append((0, "---"))

            broadcast_sync(data_payload)
        
        except Exception as e:
            log_and_broadcast("error", f"Smyčka radaru selhala: {e}", loop)
            broadcast_sync({"status": "waiting_for_device", "program_uptime": str(datetime.now() - START_TIME).split('.')[0], "cpu_percent": psutil.cpu_percent(), "ram_mb": process.memory_info().rss / (1024 * 1024)})
            smoothed_distance, smoothed_speed, device, connection_start_time = None, None, None, None
            stats = reset_stats()
            with state_lock: sector_states = ['none'] * 64
            time.sleep(3)

# ===========================================================================
# FastAPI App a Endpoints
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
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
    ).replace(
        '__DEFAULT_NUM_CHIRPS__', str(DEFAULT_NUM_CHIRPS)
    )
    return HTMLResponse(content=content)

@app.get("/lang/{lang_code}", response_class=JSONResponse)
async def get_lang(lang_code: str):
    return LANGUAGES.get(lang_code, LANGUAGES["en"])

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(base64.b64decode(FAVICON_B64), media_type="image/png")

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
                if data.get('num_chirps') != shared_state.get('num_chirps'):
                    shared_state['num_chirps'] = data['num_chirps']; reconfigure_needed = True
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