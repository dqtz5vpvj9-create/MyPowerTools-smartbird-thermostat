#!/usr/bin/env python3
"""HTTP service wrapper for the Smart-Bird dew-point thermostat."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
from pathlib import Path
import socketserver
import smtplib
import sys
import threading
import time
import urllib.parse
import urllib.request


if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from test_tools.smartbird_thermostat import (  # noqa: E402
    MODE_EXPERIMENT,
    MODE_PROTECTION,
    SmartBirdThermalConfig,
    build_controller,
)


@dataclass
class EmailNotificationConfig:
    enabled: bool = False
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_starttls: bool = False
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    monitor_interval_sec: float = 30.0
    cooldown_sec: float = 1800.0
    send_recovery: bool = True
    expected_min_devices: int = 0

    @property
    def usable(self) -> bool:
        return bool(
            self.enabled
            and self.smtp_host
            and self.smtp_port
            and self.username
            and self.password
            and (self.sender or self.username)
            and self.recipients
        )


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart-Bird Thermostat</title>
<style>
:root {
  color-scheme: light;
  --bg: #f5f6f2;
  --panel: #ffffff;
  --text: #1d2522;
  --muted: #65706b;
  --border: #d8ddd7;
  --green: #168c5a;
  --red: #c0443e;
  --amber: #b8731a;
  --blue: #2f68b7;
  --ink: #2f3432;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.shell { max-width: 1280px; margin: 0 auto; padding: 20px; }
header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
h1 { font-size: 22px; line-height: 1.1; margin: 0; font-weight: 650; letter-spacing: 0; }
.sub { color: var(--muted); margin-top: 5px; }
.stamp { color: var(--muted); text-align: right; white-space: nowrap; }
.grid { display: grid; gap: 12px; }
.stats { grid-template-columns: repeat(6, minmax(0, 1fr)); margin-bottom: 12px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  min-width: 0;
}
.metric .label { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.metric .value { font-size: 20px; font-weight: 650; min-height: 30px; overflow-wrap: anywhere; }
.metric .detail { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.charts { grid-template-columns: 2fr 1fr; align-items: stretch; }
.panel h2 { font-size: 15px; margin: 0 0 8px; font-weight: 650; letter-spacing: 0; }
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.panel-head h2 { margin: 0; }
.range-control {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f7f9f5;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.range-control button {
  border: 0;
  background: transparent;
  color: var(--muted);
  min-width: 44px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.range-control button.active {
  background: var(--ink);
  color: #fff;
}
.control-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 10px 0 2px;
}
.command-button {
  border: 1px solid var(--border);
  background: #f7f9f5;
  color: var(--text);
  height: 30px;
  padding: 0 10px;
  border-radius: 6px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.command-button:disabled { opacity: 0.55; cursor: progress; }
.toggle-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.toggle-control input { width: 34px; height: 18px; accent-color: var(--green); }
.readout {
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
canvas { display: block; width: 100%; height: 310px; }
#keyChart { height: 180px; }
.events { margin-top: 12px; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th, td {
  border-bottom: 1px solid var(--border);
  padding: 7px 6px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
th { color: var(--muted); font-size: 12px; font-weight: 600; }
tbody tr:last-child td { border-bottom: 0; }
.pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: #eef2ec;
  border: 1px solid var(--border);
  font-size: 12px;
  max-width: 100%;
}
.on { color: var(--green); }
.off { color: var(--red); }
.warn { color: var(--amber); }
.energy { grid-template-columns: minmax(280px, 1fr) minmax(0, 2fr); margin: 12px 0; align-items: stretch; }
.energy-details {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 6px 12px;
  color: var(--muted);
}
.energy-details strong { color: var(--text); font-weight: 600; overflow-wrap: anywhere; }
#energyDeviceChart { height: 170px; }
.energy-history-table { margin-top: 8px; }
.energy-history-table th:nth-child(1), .energy-history-table td:nth-child(1) { width: 90px; }
.energy-history-table th:nth-child(2), .energy-history-table td:nth-child(2) { width: 70px; }
.energy-history-table th:nth-child(3), .energy-history-table td:nth-child(3) { width: 90px; }
.energy-history-table th:nth-child(5), .energy-history-table td:nth-child(5) { width: 90px; }
@media (max-width: 900px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .charts { grid-template-columns: 1fr; }
  .energy { grid-template-columns: 1fr; }
  header { align-items: start; flex-direction: column; }
  .stamp { text-align: left; }
}
</style>
</head>
<body>
<main class="shell">
  <header>
    <div>
      <h1>Smart-Bird Thermostat</h1>
      <div class="sub">Dew-point protection and energy session cooling</div>
    </div>
    <div class="stamp" id="stamp">loading</div>
  </header>

  <section class="grid stats">
    <div class="panel metric"><div class="label">Mode</div><div class="value" id="mode">--</div><div class="detail" id="sessions">--</div></div>
    <div class="panel metric"><div class="label">Switch</div><div class="value" id="key">--</div><div class="detail" id="clients">--</div></div>
    <div class="panel metric"><div class="label">Surface</div><div class="value" id="surface">--</div><div class="detail" id="sensor">--</div></div>
    <div class="panel metric"><div class="label">Dew Point</div><div class="value" id="dew">--</div><div class="detail" id="offThreshold">--</div></div>
    <div class="panel metric"><div class="label">Restart Threshold</div><div class="value" id="onThreshold">--</div><div class="detail" id="runtime">--</div></div>
    <div class="panel metric"><div class="label">Events</div><div class="value" id="eventCount">--</div><div class="detail" id="historyCount">--</div></div>
  </section>

  <section class="grid energy">
    <div class="panel">
      <h2>Energy Server</h2>
      <div class="energy-details">
        <span>Status</span><strong id="energyState">--</strong>
        <span>Endpoint</span><strong id="energyEndpoint">--</strong>
        <span>Backend</span><strong id="energyBackend">--</strong>
        <span>HID State</span><strong id="energyHid">--</strong>
        <span>HID Candidates</span><strong id="energyCandidates">--</strong>
        <span>Devices</span><strong id="energyDevices">--</strong>
        <span>Session</span><strong id="energySession">--</strong>
        <span>Capabilities</span><strong id="energyCapabilities">--</strong>
        <span>Thermal Link</span><strong id="energyThermal">--</strong>
        <span>Error</span><strong id="energyError">--</strong>
      </div>
      <div class="control-row">
        <button class="command-button" id="readMeterButton" type="button">Read Meter</button>
        <span class="readout" id="meterReadout">--</span>
      </div>
    </div>
    <div class="panel">
      <h2>Energy Device History</h2>
      <canvas id="energyDeviceChart"></canvas>
      <table class="energy-history-table">
        <thead><tr><th>Time</th><th>Online</th><th>HID</th><th>Devices</th><th>Candidates</th><th>Session</th></tr></thead>
        <tbody id="energyHistoryRows"><tr><td colspan="6">loading</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="grid charts">
    <div class="panel">
      <div class="panel-head">
        <h2>Temperature History</h2>
        <div class="range-control" id="timeRange">
          <button type="button" data-hours="0.25">15m</button>
          <button type="button" data-hours="1">1h</button>
          <button type="button" data-hours="6" class="active">6h</button>
          <button type="button" data-hours="24">24h</button>
          <button type="button" data-hours="all">All</button>
        </div>
      </div>
      <canvas id="tempChart"></canvas>
    </div>
    <div class="panel">
      <div class="panel-head">
        <h2>Switch State</h2>
        <label class="toggle-control">
          <input id="manualSwitchToggle" type="checkbox">
          <span>Manual</span>
        </label>
      </div>
      <canvas id="keyChart"></canvas>
    </div>
  </section>

  <section class="panel events">
    <h2>Thermostat Events</h2>
    <table>
      <thead><tr><th style="width:150px">Time</th><th style="width:120px">Type</th><th style="width:70px">Key</th><th>Detail</th></tr></thead>
      <tbody id="eventRows"><tr><td colspan="4">loading</td></tr></tbody>
    </table>
  </section>
</main>

<script>
const colors = {
  surface: "#168c5a",
  dew: "#2f68b7",
  off: "#c0443e",
  on: "#b8731a",
  amber: "#b8731a",
  red: "#c0443e",
  blue: "#2f68b7",
  grid: "#d8ddd7",
  text: "#1d2522",
  muted: "#65706b"
};
let selectedRangeHours = 6;
let manualSwitchBusy = false;
let readMeterBusy = false;

function fmtC(value) {
  return Number.isFinite(value) ? value.toFixed(1) + " C" : "--";
}

function fmtTime(ts) {
  if (!ts) return "--";
  return new Date(ts * 1000).toLocaleTimeString();
}

async function getJson(url) {
  const resp = await fetch(url, {cache: "no-store"});
  if (!resp.ok) throw new Error(resp.status + " " + resp.statusText);
  return await resp.json();
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload || {})
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || data.message || (resp.status + " " + resp.statusText));
  return data;
}

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

function historyUrl() {
  if (selectedRangeHours === "all") {
    return "/api/history?limit=7200";
  }
  const since = Math.floor(Date.now() / 1000 - Number(selectedRangeHours) * 3600);
  return "/api/history?limit=7200&since=" + since;
}

function energyHistoryUrl() {
  if (selectedRangeHours === "all") {
    return "/api/energy/history?limit=7200";
  }
  const since = Math.floor(Date.now() / 1000 - Number(selectedRangeHours) * 3600);
  return "/api/energy/history?limit=7200&since=" + since;
}

function latestNumeric(history, key) {
  for (let i = history.length - 1; i >= 0; i--) {
    const raw = history[i][key];
    if (raw === null || raw === undefined || raw === "") continue;
    const value = Number(raw);
    if (Number.isFinite(value)) return value;
  }
  return NaN;
}

function numericOrNaN(raw) {
  if (raw === null || raw === undefined || raw === "") return NaN;
  const value = Number(raw);
  return Number.isFinite(value) ? value : NaN;
}

function renderStatus(status, history, events) {
  const decision = status.last_decision || {};
  const sw = status.switch || {};
  const cfg = status.config || {};
  const active = status.active_session_ids || [];
  const key = status.last_key ?? sw.reported_key ?? sw.desired_key;
  setText("mode", status.mode || "--");
  setText("sessions", active.length ? active.join(", ") : "no active session");
  setText("key", key === 1 ? "ON" : key === 0 ? "OFF" : "--");
  document.getElementById("key").className = "value " + (key === 1 ? "on" : key === 0 ? "off" : "");
  if (!manualSwitchBusy) {
    document.getElementById("manualSwitchToggle").checked = key === 1;
  }
  setText("clients", (sw.client_count ?? 0) + " tcp client(s)");
  setText("surface", fmtC(latestNumeric(history, "surface_c")));
  setText("sensor", (decision.reason || "").match(/sensor=([^ ]+)/)?.[1] || cfg.adb_serial || "--");
  setText("dew", fmtC(latestNumeric(history, "dew_point_c")));
  setText("offThreshold", "off <= " + fmtC(latestNumeric(history, "off_threshold_c")));
  setText("onThreshold", fmtC(latestNumeric(history, "on_threshold_c")));
  setText("runtime", "min on/off " + (cfg.min_on_sec ?? "--") + "/" + (cfg.min_off_sec ?? "--") + "s");
  setText("eventCount", String(status.event_count ?? events.length));
  setText("historyCount", String(status.history_points ?? history.length) + " samples");
  setText("stamp", "updated " + new Date().toLocaleString());
}

function renderEnergy(energy) {
  const backend = energy.backend || {};
  const handshake = energy.handshake || {};
  const thermal = energy.thermal_control || {};
  const caps = backend.capabilities || {};
  const candidates = Array.isArray(backend.hid_candidates) ? backend.hid_candidates : [];
  const devices = Array.isArray(handshake.devices) ? handshake.devices : [];
  const state = energy.online ? "ONLINE" : energy.enabled === false ? "DISABLED" : "OFFLINE";
  const hidState = backend.hid_waiting
    ? "waiting"
    : backend.hid_discovery_only
      ? "discovery-only"
      : devices.length
        ? "connected"
        : "--";
  const backendParts = [
    backend.backend_name || "--",
    backend.session_operations_mode || ""
  ].filter(Boolean);
  const active = Array.isArray(thermal.energy_server_active_session_ids)
    ? thermal.energy_server_active_session_ids
    : [];
  const capabilities = [
    caps.start_session ? "start" : "",
    caps.stop_session ? "stop" : "",
    caps.read_nrg ? "read_nrg" : ""
  ].filter(Boolean).join(", ") || "--";
  const errors = energy.errors || {};
  const errorText = energy.error || Object.keys(errors).map(k => k + ": " + errors[k]).join(" | ") || "--";
  setText("energyState", state);
  document.getElementById("energyState").className = energy.online ? "on" : "warn";
  setText("energyEndpoint", energy.url || "--");
  setText("energyBackend", backendParts.join(" / ") || "--");
  setText("energyHid", hidState);
  setText("energyCandidates", String(candidates.length));
  setText("energyDevices", devices.length ? devices.join(", ") : "no HID devices");
  setText("energySession", handshake.session_id || "--");
  setText("energyCapabilities", capabilities);
  setText("energyThermal", active.length ? active.join(", ") : (thermal.enabled === false ? "disabled" : "idle"));
  setText("energyError", errorText);
}

function formatMeterReadout(payload) {
  const reading = payload.reading || payload;
  const values = reading.values || {};
  const names = Object.keys(values);
  if (!names.length) {
    const devices = Array.isArray(reading.devices) ? reading.devices.join(", ") : "";
    return devices ? "no values: " + devices : "no meter values";
  }
  return names.map(name => {
    const item = values[name] || {};
    if (item.error) return name + ": " + item.error;
    const vbus = item.vbus ?? "--";
    const ibus = item.ibus ?? "--";
    const nrg = item.nrg ?? "--";
    return `${name}: ${vbus} V, ${ibus} A, ${nrg}`;
  }).join(" | ");
}

async function readMeterOnce() {
  if (readMeterBusy) return;
  readMeterBusy = true;
  const button = document.getElementById("readMeterButton");
  button.disabled = true;
  setText("meterReadout", "reading...");
  try {
    const data = await postJson("/api/energy/read", {});
    setText("meterReadout", formatMeterReadout(data));
  } catch (err) {
    setText("meterReadout", "error: " + err.message);
  } finally {
    readMeterBusy = false;
    button.disabled = false;
  }
}

async function setManualSwitch(checked) {
  if (manualSwitchBusy) return;
  manualSwitchBusy = true;
  const toggle = document.getElementById("manualSwitchToggle");
  toggle.disabled = true;
  try {
    await postJson("/api/switch", {
      key: checked ? 1 : 0,
      reason: "manual_ui_switch"
    });
    await refresh();
  } catch (err) {
    setText("stamp", "switch error " + err.message);
    toggle.checked = !checked;
  } finally {
    manualSwitchBusy = false;
    toggle.disabled = false;
  }
}

function drawEnergyDeviceChart(history) {
  const canvas = document.getElementById("energyDeviceChart");
  const {ctx, width, height} = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = history.filter(p => Number.isFinite(Number(p.timestamp)));
  if (points.length < 2) {
    ctx.fillStyle = colors.muted;
    ctx.fillText("waiting for energy samples", 20, 40);
    return;
  }
  const minT = points[0].timestamp;
  const maxT = points[points.length - 1].timestamp || (minT + 1);
  const maxY = Math.max(1, ...points.map(p => Number(p.device_count || 0)), ...points.map(p => Number(p.hid_candidate_count || 0)));
  const pad = {l: 34, r: 14, t: 16, b: 52};
  const x0 = pad.l, y0 = pad.t, w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const xFor = ts => x0 + (ts - minT) / Math.max(1, maxT - minT) * w;
  const yFor = v => y0 + h - v / Math.max(1, maxY) * h;
  ctx.strokeStyle = colors.grid;
  for (let i = 0; i <= maxY; i++) {
    const y = yFor(i);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x0 + w, y);
    ctx.stroke();
    ctx.fillStyle = colors.muted;
    ctx.fillText(String(i), 8, y + 4);
  }
  drawStepLine(ctx, points, "device_count", xFor, yFor, colors.green || colors.surface);
  drawStepLine(ctx, points, "hid_candidate_count", xFor, yFor, colors.blue);
  drawTimeAxis(ctx, x0, y0 + h, w, minT, maxT);
  ctx.font = "12px system-ui";
  ctx.fillStyle = colors.muted;
  ctx.fillText(fmtTime(minT) + " - " + fmtTime(maxT), x0, 12);
  [["devices", colors.surface], ["candidates", colors.blue]].forEach((item, i) => {
    ctx.fillStyle = item[1];
    ctx.fillRect(x0 + i * 94, height - 14, 10, 10);
    ctx.fillStyle = colors.text;
    ctx.fillText(item[0], x0 + 14 + i * 94, height - 5);
  });
}

function drawStepLine(ctx, points, key, xFor, yFor, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  let prevY = 0;
  points.forEach(p => {
    const value = Number(p[key] || 0);
    if (!Number.isFinite(value)) return;
    const x = xFor(p.timestamp);
    const y = yFor(value);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, prevY);
      ctx.lineTo(x, y);
    }
    prevY = y;
  });
  if (started) ctx.stroke();
}

function renderEnergyHistory(history) {
  drawEnergyDeviceChart(history);
  const rows = document.getElementById("energyHistoryRows");
  const latest = history.slice(-12).reverse();
  if (!latest.length) {
    rows.innerHTML = '<tr><td colspan="6">no energy history</td></tr>';
    return;
  }
  rows.innerHTML = latest.map(item => {
    const stale = item.state_uncertain ? " (last known)" : "";
    const devices = Array.isArray(item.devices) && item.devices.length ? item.devices.join(", ") + stale : (item.state_uncertain ? "unknown" : "none");
    const hidState = item.state_uncertain ? "unknown" : item.hid_waiting ? "waiting" : item.hid_discovery_only ? "discovery" : item.device_count ? "connected" : "--";
    return `<tr><td>${fmtTime(item.timestamp)}</td><td class="${item.online ? "on" : "warn"}">${item.online ? "yes" : "no"}</td><td>${hidState}</td><td>${escapeHtml(devices)}</td><td>${item.hid_candidate_count ?? 0}</td><td>${escapeHtml(item.session_id || "--")}</td></tr>`;
  }).join("");
}

function prepareCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return {ctx, width: rect.width, height: rect.height};
}

function drawAxes(ctx, x, y, w, h, minY, maxY) {
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  ctx.font = "12px system-ui";
  ctx.fillStyle = colors.muted;
  for (let i = 0; i <= 4; i++) {
    const py = y + h - h * i / 4;
    ctx.beginPath();
    ctx.moveTo(x, py);
    ctx.lineTo(x + w, py);
    ctx.stroke();
    const label = (minY + (maxY - minY) * i / 4).toFixed(1);
    ctx.fillText(label, 6, py + 4);
  }
}

function drawTimeAxis(ctx, x, y, w, minT, maxT) {
  const span = Math.max(1, maxT - minT);
  const tickCount = Math.max(2, Math.min(6, Math.floor(w / 120) + 1));
  ctx.save();
  ctx.strokeStyle = colors.grid;
  ctx.fillStyle = colors.muted;
  ctx.lineWidth = 1;
  ctx.font = "12px system-ui";
  ctx.textBaseline = "top";
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + w, y);
  ctx.stroke();
  for (let i = 0; i < tickCount; i++) {
    const ratio = tickCount === 1 ? 0 : i / (tickCount - 1);
    const tx = x + ratio * w;
    const ts = minT + ratio * span;
    ctx.beginPath();
    ctx.moveTo(tx, y);
    ctx.lineTo(tx, y + 5);
    ctx.stroke();
    ctx.textAlign = i === 0 ? "left" : i === tickCount - 1 ? "right" : "center";
    ctx.fillText(fmtTime(ts), tx, y + 8);
  }
  ctx.restore();
}

function drawSeries(ctx, points, key, xFor, yFor, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  points.forEach(p => {
    const value = numericOrNaN(p[key]);
    if (!Number.isFinite(value)) return;
    const x = xFor(p.timestamp);
    const y = yFor(value);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  if (started) ctx.stroke();
}

function drawTempChart(history) {
  const canvas = document.getElementById("tempChart");
  const {ctx, width, height} = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = history.filter(p => Number.isFinite(Number(p.timestamp)));
  if (points.length < 2) {
    ctx.fillStyle = colors.muted;
    ctx.fillText("waiting for samples", 20, 40);
    return;
  }
  const keys = ["surface_c", "dew_point_c", "off_threshold_c", "on_threshold_c"];
  const values = [];
  points.forEach(p => keys.forEach(k => {
    const v = numericOrNaN(p[k]);
    if (Number.isFinite(v)) values.push(v);
  }));
  if (!values.length) {
    ctx.fillStyle = colors.muted;
    ctx.fillText("waiting for valid temperature", 20, 40);
    return;
  }
  const minY = Math.floor(Math.min(...values) - 1);
  const maxY = Math.ceil(Math.max(...values) + 1);
  const minT = points[0].timestamp;
  const maxT = points[points.length - 1].timestamp || (minT + 1);
  const pad = {l: 48, r: 18, t: 18, b: 56};
  const x0 = pad.l, y0 = pad.t, w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const xFor = ts => x0 + (ts - minT) / Math.max(1, maxT - minT) * w;
  const yFor = v => y0 + h - (v - minY) / Math.max(1, maxY - minY) * h;
  drawAxes(ctx, x0, y0, w, h, minY, maxY);
  drawSeries(ctx, points, "surface_c", xFor, yFor, colors.surface);
  drawSeries(ctx, points, "dew_point_c", xFor, yFor, colors.dew);
  drawSeries(ctx, points, "off_threshold_c", xFor, yFor, colors.off);
  drawSeries(ctx, points, "on_threshold_c", xFor, yFor, colors.on);
  drawTimeAxis(ctx, x0, y0 + h, w, minT, maxT);
  ctx.font = "12px system-ui";
  ctx.fillStyle = colors.muted;
  ctx.fillText(fmtTime(minT) + " - " + fmtTime(maxT), x0, 14);
  [["surface", colors.surface], ["dew", colors.dew], ["off", colors.off], ["on", colors.on]].forEach((item, i) => {
    ctx.fillStyle = item[1];
    ctx.fillRect(x0 + i * 76, height - 18, 10, 10);
    ctx.fillStyle = colors.text;
    ctx.fillText(item[0], x0 + 14 + i * 76, height - 9);
  });
}

function drawKeyChart(history, events) {
  const canvas = document.getElementById("keyChart");
  const {ctx, width, height} = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = history.filter(p => Number.isFinite(Number(p.timestamp)));
  if (points.length < 2) {
    ctx.fillStyle = colors.muted;
    ctx.fillText("waiting for samples", 20, 40);
    return;
  }
  const pad = {l: 34, r: 14, t: 16, b: 32};
  const x0 = pad.l, y0 = pad.t, w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const minT = points[0].timestamp;
  const maxT = points[points.length - 1].timestamp || (minT + 1);
  const xFor = ts => x0 + (ts - minT) / Math.max(1, maxT - minT) * w;
  const yFor = v => y0 + h - v * h;
  ctx.strokeStyle = colors.grid;
  [0, 1].forEach(v => {
    const y = yFor(v);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x0 + w, y);
    ctx.stroke();
    ctx.fillStyle = colors.muted;
    ctx.fillText(v ? "ON" : "OFF", 4, y + 4);
  });
  ctx.strokeStyle = colors.surface;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;
  let prevY = 0;
  points.forEach(p => {
    const key = Number(p.last_key);
    if (!Number.isFinite(key)) return;
    const x = xFor(p.timestamp);
    const y = yFor(key > 0 ? 1 : 0);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, prevY);
      ctx.lineTo(x, y);
    }
    prevY = y;
  });
  if (started) ctx.stroke();
  events.forEach(e => {
    if (!Number.isFinite(Number(e.timestamp))) return;
    if (e.timestamp < minT || e.timestamp > maxT) return;
    const x = xFor(e.timestamp);
    ctx.strokeStyle = e.type === "blocked" ? colors.amber : e.type === "switch" ? colors.red : colors.blue;
    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y0 + h);
    ctx.stroke();
  });
  drawTimeAxis(ctx, x0, y0 + h, w, minT, maxT);
}

function renderEvents(events) {
  const rows = document.getElementById("eventRows");
  const latest = events.slice(-80).reverse();
  if (!latest.length) {
    rows.innerHTML = '<tr><td colspan="4">no events</td></tr>';
    return;
  }
  rows.innerHTML = latest.map(e => {
    const cls = e.type === "blocked" ? "warn" : e.key === 1 ? "on" : e.key === 0 ? "off" : "";
    const key = e.key === 1 ? "ON" : e.key === 0 ? "OFF" : "";
    return `<tr><td>${fmtTime(e.timestamp)}</td><td><span class="pill">${e.type || ""}</span></td><td class="${cls}">${key}</td><td>${escapeHtml(eventDetail(e))}</td></tr>`;
  }).join("");
}

function eventDetail(e) {
  if (e.type === "energy_session") {
    const sessions = Array.isArray(e.session_ids) ? e.session_ids.join(",") : "";
    const active = Array.isArray(e.active_session_ids) ? e.active_session_ids.join(",") : "";
    const parts = [
      e.source ? "source=" + e.source : "",
      e.action ? "action=" + e.action : "",
      sessions ? "sessions=" + sessions : "",
      "active=" + (active || "none")
    ].filter(Boolean);
    return parts.join(" ");
  }
  return e.reason || e.message || "";
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function refresh() {
  try {
    const [status, historyPayload, eventsPayload, energyPayload, energyHistoryPayload] = await Promise.all([
      getJson("/api/status"),
      getJson(historyUrl()),
      getJson("/api/events?limit=200"),
      getJson("/api/energy/status"),
      getJson(energyHistoryUrl())
    ]);
    const history = historyPayload.history || [];
    const events = eventsPayload.events || [];
    const energyHistory = energyHistoryPayload.history || [];
    renderStatus(status, history, events);
    renderEnergy(energyPayload);
    renderEnergyHistory(energyHistory);
    drawTempChart(history);
    drawKeyChart(history, events);
    renderEvents(events);
  } catch (err) {
    setText("stamp", "error " + err.message);
  }
}

window.addEventListener("resize", refresh);
document.querySelectorAll("#timeRange button").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#timeRange button").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    const value = button.dataset.hours;
    selectedRangeHours = value === "all" ? "all" : Number(value);
    refresh();
  });
});
document.getElementById("readMeterButton").addEventListener("click", readMeterOnce);
document.getElementById("manualSwitchToggle").addEventListener("change", event => {
  setManualSwitch(event.target.checked);
});
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def _bool_from_value(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list_from_value(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    else:
        values = list(value)
    result = []
    seen = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def load_notification_config(path: str | Path | None, logger=None) -> EmailNotificationConfig:
    raw = {}
    cfg_path = Path(path) if path else None
    if cfg_path and cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                raw.update(loaded)
        except Exception as exc:
            if logger:
                logger.warning("failed to load notification config %s: %s", cfg_path, exc)

    import os

    env_map = {
        "enabled": "SMARTBIRD_NOTIFY_ENABLE",
        "smtp_host": "SMARTBIRD_NOTIFY_SMTP_HOST",
        "smtp_port": "SMARTBIRD_NOTIFY_SMTP_PORT",
        "smtp_ssl": "SMARTBIRD_NOTIFY_SMTP_SSL",
        "smtp_starttls": "SMARTBIRD_NOTIFY_SMTP_STARTTLS",
        "username": "SMARTBIRD_NOTIFY_SMTP_USERNAME",
        "password": "SMARTBIRD_NOTIFY_SMTP_PASSWORD",
        "sender": "SMARTBIRD_NOTIFY_SENDER",
        "recipients": "SMARTBIRD_NOTIFY_RECIPIENTS",
        "monitor_interval_sec": "SMARTBIRD_NOTIFY_INTERVAL_SEC",
        "cooldown_sec": "SMARTBIRD_NOTIFY_COOLDOWN_SEC",
        "send_recovery": "SMARTBIRD_NOTIFY_SEND_RECOVERY",
        "expected_min_devices": "SMARTBIRD_NOTIFY_EXPECTED_MIN_DEVICES",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value is not None and value != "":
            raw[key] = value

    return EmailNotificationConfig(
        enabled=_bool_from_value(raw.get("enabled"), False),
        smtp_host=str(raw.get("smtp_host") or "smtp.163.com"),
        smtp_port=int(raw.get("smtp_port") or 465),
        smtp_ssl=_bool_from_value(raw.get("smtp_ssl"), True),
        smtp_starttls=_bool_from_value(raw.get("smtp_starttls"), False),
        username=str(raw.get("username") or ""),
        password=str(raw.get("password") or ""),
        sender=str(raw.get("sender") or raw.get("username") or ""),
        recipients=_list_from_value(raw.get("recipients")),
        monitor_interval_sec=max(5.0, float(raw.get("monitor_interval_sec") or 30.0)),
        cooldown_sec=max(60.0, float(raw.get("cooldown_sec") or 1800.0)),
        send_recovery=_bool_from_value(raw.get("send_recovery"), True),
        expected_min_devices=max(0, int(raw.get("expected_min_devices") or 0)),
    )


def send_smtp_email(config: EmailNotificationConfig, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.sender or config.username
    message["To"] = ", ".join(config.recipients)
    message["Subject"] = subject
    message.set_content(body)
    if config.smtp_ssl:
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15) as smtp:
            smtp.login(config.username, config.password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as smtp:
            if config.smtp_starttls:
                smtp.starttls()
            smtp.login(config.username, config.password)
            smtp.send_message(message)


class ThermostatSessionService:
    def __init__(
        self,
        thermostat,
        logger=None,
        clock=time.time,
        energy_server_url: str | None = "http://127.0.0.1:18988",
        energy_timeout_sec: float = 1.0,
        energy_history_path: str | Path | None = None,
        energy_history_limit: int = 7200,
        notification_config: EmailNotificationConfig | None = None,
        email_sender=None,
    ):
        self.thermostat = thermostat
        self.logger = logger or logging.getLogger(__name__)
        self.clock = clock
        self.energy_server_url = (energy_server_url or "").rstrip("/")
        self.energy_timeout_sec = energy_timeout_sec
        self.energy_history_path = Path(energy_history_path) if energy_history_path else None
        self.energy_history_limit = max(1, int(energy_history_limit))
        self._energy_history = self._load_energy_history()
        self.started_at = self.clock()
        self._lock = threading.RLock()
        self._energy_lock = threading.RLock()
        self._active_session_ids: set[str] = set()
        self.notification_config = notification_config or EmailNotificationConfig()
        self._email_sender = email_sender or (
            lambda subject, body: send_smtp_email(self.notification_config, subject, body)
        )
        self._notification_stop_event = threading.Event()
        self._notification_thread: threading.Thread | None = None
        self._notification_active_keys: set[str] = set()
        self._notification_expected_device_count = self.notification_config.expected_min_devices
        self._notification_expected_device_names: set[str] = set()
        for item in reversed(self._energy_history):
            if self._energy_snapshot_has_reliable_devices(item):
                devices = item.get("devices") if isinstance(item.get("devices"), list) else []
                self._notification_expected_device_count = max(
                    self._notification_expected_device_count,
                    len(devices),
                )
                self._notification_expected_device_names = set(devices)
                break

    def start(self) -> None:
        self.thermostat.start()
        self.thermostat.enter_protection(reason="service_start")
        self._start_notification_monitor()

    def stop(self) -> None:
        self._notification_stop_event.set()
        if self._notification_thread and self._notification_thread.is_alive():
            self._notification_thread.join(timeout=3)
        self.thermostat.stop()

    def _start_notification_monitor(self) -> None:
        if not self.notification_config.usable:
            if self.notification_config.enabled:
                self.logger.warning("notification config is enabled but incomplete")
            return
        if self._notification_thread is not None and self._notification_thread.is_alive():
            return
        self._notification_stop_event.clear()
        self._notification_thread = threading.Thread(
            target=self._notification_loop,
            name="smartbird-notification-monitor",
            daemon=True,
        )
        self._notification_thread.start()

    def _notification_loop(self) -> None:
        while not self._notification_stop_event.wait(self.notification_config.monitor_interval_sec):
            try:
                self._check_notifications_once()
            except Exception:
                self.logger.exception("notification monitor check failed")

    def _check_notifications_once(self) -> None:
        if not self.notification_config.usable:
            return
        anomalies = self._collect_notification_anomalies()
        active = {item["key"]: item for item in anomalies}
        for key, item in active.items():
            if key not in self._notification_active_keys:
                self._send_notification(
                    "[Smart-Bird] " + item["title"],
                    self._format_notification_body(item),
                )
        if self.notification_config.send_recovery:
            for key in sorted(self._notification_active_keys - set(active)):
                self._send_notification(
                    "[Smart-Bird] recovered: " + key,
                    f"异常已恢复: {key}\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                )
        self._notification_active_keys = set(active)

    def _collect_notification_anomalies(self) -> list[dict]:
        anomalies: list[dict] = []
        status = self.status()
        try:
            energy = self.energy_status()
        except Exception as exc:
            energy = {"online": False, "error": str(exc), "url": self.energy_server_url}

        if not energy.get("online"):
            anomalies.append(
                {
                    "key": "energy_server_offline",
                    "title": "Energy Server 离线",
                    "detail": energy.get("error") or "Energy Server status is offline",
                    "payload": energy,
                }
            )
        else:
            snapshot = energy.get("snapshot") or {}
            handshake = energy.get("handshake") or {}
            backend = energy.get("backend") or handshake.get("backend") or {}
            if isinstance(snapshot, dict) and snapshot.get("state_uncertain"):
                devices = None
            else:
                raw_devices = snapshot.get("devices") if isinstance(snapshot, dict) else []
                devices = list(raw_devices) if isinstance(raw_devices, list) else []
            device_count = len(devices) if devices is not None else self._notification_expected_device_count
            if devices is not None and device_count > self._notification_expected_device_count:
                self._notification_expected_device_count = device_count
                self._notification_expected_device_names = set(devices)
            if devices is not None and device_count < self._notification_expected_device_count:
                anomalies.append(
                    {
                        "key": "power_meter_count_decreased",
                        "title": "功耗计数量减少",
                        "detail": (
                            f"expected={self._notification_expected_device_count}, "
                            f"current={device_count}, devices={devices or 'none'}"
                        ),
                        "payload": {
                            "expected_device_count": self._notification_expected_device_count,
                            "expected_devices": sorted(self._notification_expected_device_names),
                            "current_device_count": device_count,
                            "current_devices": devices,
                            "hid_waiting": backend.get("hid_waiting"),
                            "hid_candidates": len(backend.get("hid_candidates") or []),
                        },
                    }
                )

        adb_error = self._probe_adb_unavailable(status)
        if adb_error:
            anomalies.append(
                {
                    "key": "adb_unavailable",
                    "title": "ADB 全部不可用",
                    "detail": adb_error,
                    "payload": {"adb_serial": (status.get("config") or {}).get("adb_serial")},
                }
            )

        switch = status.get("switch") or {}
        try:
            client_count = int(switch.get("client_count") or 0)
        except (TypeError, ValueError):
            client_count = 0
        if client_count <= 0:
            anomalies.append(
                {
                    "key": "smartbird_tcp_disconnected",
                    "title": "Smart-Bird TCP 设备断开",
                    "detail": "Smart-Bird switch has no TCP clients connected",
                    "payload": switch,
                }
            )
        return anomalies

    def _probe_adb_unavailable(self, status: dict) -> str:
        reader = getattr(self.thermostat, "thermal_reader", None)
        if reader is None or not hasattr(reader, "current"):
            decision = status.get("last_decision") or {}
            reason = str(decision.get("reason") or "")
            return reason if "sensor_error" in reason else ""
        try:
            reader.current()
            return ""
        except Exception as exc:
            return str(exc)

    def _format_notification_body(self, item: dict) -> str:
        lines = [
            item["title"],
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"详情: {item.get('detail') or ''}",
            "",
            "状态:",
            json.dumps(item.get("payload") or {}, ensure_ascii=False, indent=2, default=str),
        ]
        return "\n".join(lines)

    def _send_notification(self, subject: str, body: str) -> None:
        try:
            self._email_sender(subject, body)
            self.logger.info("notification email sent: %s", subject)
        except Exception as exc:
            self.logger.warning("failed to send notification email %s: %s", subject, exc)

    def session_event(
        self,
        action: str,
        session_ids,
        reason: str = "",
        source: str = "http",
    ) -> dict:
        ids = _normalize_session_ids(session_ids)
        if action == "start" and not ids:
            ids = ["manual"]
        with self._lock:
            if action == "start":
                self._active_session_ids.update(ids)
            elif action in {"pause", "stop", "end"}:
                self._active_session_ids.difference_update(ids)
            else:
                raise ValueError(f"Unsupported session action: {action}")
            active_ids = sorted(self._active_session_ids)

        event_reason = reason or f"{source}_session_{action}"
        self._record_service_event(
            "energy_session",
            f"{source}_{action}",
            reason=event_reason,
            source=source,
            action=action,
            session_ids=ids,
            active_session_ids=active_ids,
            active_session_count=len(active_ids),
        )
        if active_ids:
            snapshot = self.thermostat.enter_experiment(
                reason=f"{event_reason}: active_sessions={len(active_ids)}"
            )
        else:
            snapshot = self.thermostat.enter_protection(
                reason=f"{event_reason}: no_active_sessions"
            )
        snapshot["active_session_ids"] = active_ids
        snapshot["service"] = self._service_snapshot()
        return snapshot

    def set_mode(self, mode: str, reason: str = "manual") -> dict:
        with self._lock:
            if mode == MODE_PROTECTION:
                self._active_session_ids.clear()
                active_ids = []
            elif mode == MODE_EXPERIMENT:
                self._active_session_ids.add("manual")
                active_ids = sorted(self._active_session_ids)
            else:
                raise ValueError(f"Unsupported mode: {mode}")

        if mode == MODE_EXPERIMENT:
            snapshot = self.thermostat.enter_experiment(reason=reason)
        else:
            snapshot = self.thermostat.enter_protection(reason=reason)
        snapshot["active_session_ids"] = active_ids
        snapshot["service"] = self._service_snapshot()
        return snapshot

    def evaluate(self, reason: str = "manual_evaluate") -> dict:
        decision = self.thermostat.evaluate_once(reason=reason)
        snapshot = self.status(decision)
        return snapshot

    def history(self, limit: int = 720, since: float | None = None) -> dict:
        reader = getattr(self.thermostat, "history", None)
        history = reader(limit, since=since) if callable(reader) else []
        return {"history": history}

    def events(self, limit: int = 200) -> dict:
        reader = getattr(self.thermostat, "events", None)
        events = reader(limit) if callable(reader) else []
        return {"events": events}

    def energy_status(self) -> dict:
        payload = {
            "enabled": bool(self.energy_server_url),
            "online": False,
            "url": self.energy_server_url,
            "checked_at": self.clock(),
        }
        if not self.energy_server_url:
            return payload

        errors = {}
        for key, path in (
            ("handshake", "/handshake"),
            ("backend", "/backend_status"),
            ("thermal_control", "/thermal_control/status"),
        ):
            try:
                payload[key] = self._energy_get_json(path)
            except Exception as exc:
                errors[key] = str(exc)
        payload["online"] = bool(payload.get("handshake") or payload.get("backend"))
        if errors:
            payload["errors"] = errors
            error_text = " | ".join(f"{key}: {value}" for key, value in errors.items())
            if not payload["online"]:
                payload["error"] = error_text
            else:
                payload["partial_error"] = error_text
        payload["snapshot"] = self._record_energy_snapshot(payload)
        return payload

    def energy_history(self, limit: int = 720, since: float | None = None) -> dict:
        with self._energy_lock:
            records = list(self._energy_history)
        records = self._coalesce_energy_history(records)
        if since is not None:
            records = [item for item in records if float(item.get("timestamp", 0.0)) >= since]
        return {"history": records[-limit:]}

    def energy_reading(self) -> dict:
        payload = {
            "enabled": bool(self.energy_server_url),
            "online": False,
            "url": self.energy_server_url,
            "checked_at": self.clock(),
        }
        if not self.energy_server_url:
            payload["error"] = "energy server disabled"
            return payload
        try:
            payload["reading"] = self._energy_get_json("/devices")
            payload["online"] = True
        except Exception as exc:
            payload["error"] = str(exc)
        return payload

    def manual_switch(self, key: int, reason: str = "manual_switch") -> dict:
        if key not in {0, 1}:
            raise ValueError(f"Unsupported switch key: {key}")
        switcher = getattr(self.thermostat, "manual_switch", None)
        if not callable(switcher):
            raise ValueError("Thermostat does not support manual switch control")
        snapshot = switcher(key, reason=reason)
        with self._lock:
            active_ids = sorted(self._active_session_ids)
        snapshot["active_session_ids"] = active_ids
        snapshot["service"] = self._service_snapshot()
        return snapshot

    def status(self, decision=None) -> dict:
        with self._lock:
            active_ids = sorted(self._active_session_ids)
        snapshot = self.thermostat.snapshot(decision)
        snapshot["active_session_ids"] = active_ids
        snapshot["service"] = self._service_snapshot()
        return snapshot

    def _service_snapshot(self) -> dict:
        return {
            "started_at": self.started_at,
            "uptime_sec": max(0.0, self.clock() - self.started_at),
        }

    def _record_service_event(self, event_type: str, message: str, **extra) -> None:
        recorder = getattr(self.thermostat, "record_event", None)
        if callable(recorder):
            recorder(event_type, message, **extra)

    def _energy_get_json(self, path: str) -> dict:
        url = f"{self.energy_server_url}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=self.energy_timeout_sec) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text) if text else {}

    def _record_energy_snapshot(self, payload: dict) -> dict:
        with self._energy_lock:
            previous = self._last_reliable_energy_snapshot_locked()
            snapshot = self._energy_snapshot_from_status(payload, previous=previous)
            if self._energy_history and self._energy_snapshots_equal(
                self._energy_history[-1],
                snapshot,
            ):
                return snapshot
            self._energy_history.append(snapshot)
            if len(self._energy_history) > self.energy_history_limit:
                self._energy_history = self._energy_history[-self.energy_history_limit:]
        if self.energy_history_path:
            self.energy_history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.energy_history_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(snapshot, ensure_ascii=True) + "\n")
        return snapshot

    def _energy_snapshot_from_status(self, payload: dict, previous: dict | None = None) -> dict:
        handshake = payload.get("handshake") if isinstance(payload.get("handshake"), dict) else {}
        backend = payload.get("backend") or handshake.get("backend") or {}
        backend = backend if isinstance(backend, dict) else {}
        thermal = payload.get("thermal_control") if isinstance(payload.get("thermal_control"), dict) else {}
        candidates = backend.get("hid_candidates") if isinstance(backend, dict) else []
        has_device_list = isinstance(handshake.get("devices"), list)
        devices = handshake.get("devices") if has_device_list else []
        state_uncertain = not has_device_list and bool(payload.get("online") or payload.get("errors"))
        if state_uncertain and previous:
            previous_devices = previous.get("devices") if isinstance(previous.get("devices"), list) else []
            devices = list(previous_devices)
        if isinstance(candidates, list):
            hid_candidate_count = len(candidates)
        elif state_uncertain and previous:
            hid_candidate_count = int(previous.get("hid_candidate_count") or 0)
        else:
            hid_candidate_count = 0
        active_sessions = thermal.get("energy_server_active_session_ids") if isinstance(thermal, dict) else []
        if not isinstance(active_sessions, list):
            active_sessions = previous.get("active_session_ids", []) if state_uncertain and previous else []
        session_id = handshake.get("session_id")
        if session_id is None and state_uncertain and previous:
            session_id = previous.get("session_id")

        def backend_value(key: str, default=None):
            if key in backend:
                return backend.get(key)
            if state_uncertain and previous:
                return previous.get(key, default)
            return default

        return {
            "timestamp": float(payload.get("checked_at") or self.clock()),
            "online": bool(payload.get("online")),
            "url": payload.get("url") or "",
            "backend_name": backend_value("backend_name"),
            "session_operations_mode": backend_value("session_operations_mode"),
            "hid_waiting": bool(backend_value("hid_waiting")),
            "hid_discovery_only": bool(backend_value("hid_discovery_only")),
            "hid_candidate_count": hid_candidate_count,
            "devices": list(devices) if isinstance(devices, list) else [],
            "device_count": len(devices) if isinstance(devices, list) else 0,
            "session_id": session_id,
            "active_session_ids": list(active_sessions) if isinstance(active_sessions, list) else [],
            "state_uncertain": bool(state_uncertain),
            "error": payload.get("error") or payload.get("partial_error") or "",
        }

    def _last_reliable_energy_snapshot_locked(self) -> dict | None:
        for item in reversed(self._energy_history):
            if self._energy_snapshot_has_reliable_devices(item):
                return item
        return None

    @classmethod
    def _coalesce_energy_history(cls, records: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        last_reliable: dict | None = None
        for item in records:
            current = dict(item)
            if cls._energy_snapshot_device_state_uncertain(current):
                current["state_uncertain"] = True
                if last_reliable:
                    current = cls._preserve_energy_device_state(current, last_reliable)
            if cls._energy_snapshot_has_reliable_devices(current):
                last_reliable = current
            normalized.append(current)
        return normalized

    @staticmethod
    def _preserve_energy_device_state(current: dict, previous: dict) -> dict:
        preserved = dict(current)
        devices = previous.get("devices") if isinstance(previous.get("devices"), list) else []
        preserved["devices"] = list(devices)
        preserved["device_count"] = len(devices)
        if not preserved.get("session_id"):
            preserved["session_id"] = previous.get("session_id")
        if not preserved.get("backend_name"):
            preserved["backend_name"] = previous.get("backend_name")
        if not preserved.get("session_operations_mode"):
            preserved["session_operations_mode"] = previous.get("session_operations_mode")
        if not preserved.get("hid_candidate_count"):
            preserved["hid_candidate_count"] = previous.get("hid_candidate_count", 0)
        preserved["state_uncertain"] = True
        if not preserved.get("error"):
            preserved["error"] = "device state unknown; preserving last known devices"
        return preserved

    @classmethod
    def _energy_snapshot_has_reliable_devices(cls, snapshot: dict) -> bool:
        return isinstance(snapshot.get("devices"), list) and not cls._energy_snapshot_device_state_uncertain(snapshot)

    @staticmethod
    def _energy_snapshot_device_state_uncertain(snapshot: dict) -> bool:
        if snapshot.get("state_uncertain"):
            return True
        error = str(snapshot.get("error") or "")
        if not snapshot.get("online") and error:
            return True
        try:
            device_count = int(snapshot.get("device_count") or 0)
            candidate_count = int(snapshot.get("hid_candidate_count") or 0)
        except (TypeError, ValueError):
            return True
        return (
            bool(snapshot.get("online"))
            and device_count == 0
            and candidate_count > 0
            and not snapshot.get("hid_waiting")
            and not snapshot.get("hid_discovery_only")
            and not error
        )

    @classmethod
    def _energy_snapshots_equal(cls, left: dict, right: dict) -> bool:
        return cls._energy_snapshot_signature(left) == cls._energy_snapshot_signature(right)

    @staticmethod
    def _energy_snapshot_signature(snapshot: dict) -> str:
        semantic_keys = (
            "online",
            "url",
            "backend_name",
            "session_operations_mode",
            "hid_waiting",
            "hid_discovery_only",
            "hid_candidate_count",
            "devices",
            "device_count",
            "session_id",
            "active_session_ids",
            "state_uncertain",
            "error",
        )
        semantic = {key: snapshot.get(key) for key in semantic_keys}
        return json.dumps(
            semantic,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _load_energy_history(self) -> list[dict]:
        if not self.energy_history_path or not self.energy_history_path.exists():
            return []
        records = []
        try:
            with self.energy_history_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        if records and self._energy_snapshots_equal(records[-1], item):
                            continue
                        records.append(item)
        except OSError as exc:
            self.logger.warning("failed to load energy history %s: %s", self.energy_history_path, exc)
        return records[-self.energy_history_limit:]


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_handler(service: ThermostatSessionService):
    class SmartBirdThermostatHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path in {"/", "/ui"}:
                self._write_html(200, DASHBOARD_HTML)
                return
            if path in {"/status", "/api/status"}:
                self._write_json(200, service.status())
                return
            if path == "/api/history":
                self._write_json(
                    200,
                    service.history(
                        _limit_from_query(query, 720, 7200),
                        since=_float_from_query(query, "since"),
                    ),
                )
                return
            if path == "/api/events":
                self._write_json(200, service.events(_limit_from_query(query, 200, 1000)))
                return
            if path == "/api/energy/status":
                self._write_json(200, service.energy_status())
                return
            if path == "/api/energy/history":
                self._write_json(
                    200,
                    service.energy_history(
                        _limit_from_query(query, 720, 7200),
                        since=_float_from_query(query, "since"),
                    ),
                )
                return
            self._write_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            try:
                payload = self._read_json()
                if self.path == "/session/start":
                    response = service.session_event(
                        "start",
                        _session_ids_from_payload(payload),
                        reason=str(payload.get("reason") or payload.get("energy_ctrl_msg") or ""),
                        source=str(payload.get("source") or "http"),
                    )
                    self._write_json(200, response)
                    return
                if self.path in {"/session/stop", "/session/pause", "/session/end"}:
                    action = str(payload.get("energy_ctrl_msg") or "").strip()
                    if action not in {"pause", "stop", "end"}:
                        action = {
                            "/session/pause": "pause",
                            "/session/end": "end",
                        }.get(self.path, "stop")
                    response = service.session_event(
                        action,
                        _session_ids_from_payload(payload),
                        reason=str(payload.get("reason") or payload.get("energy_ctrl_msg") or ""),
                        source=str(payload.get("source") or "http"),
                    )
                    self._write_json(200, response)
                    return
                if self.path == "/mode/experiment":
                    response = service.set_mode(
                        MODE_EXPERIMENT,
                        reason=str(payload.get("reason") or "manual_experiment"),
                    )
                    self._write_json(200, response)
                    return
                if self.path == "/mode/protection":
                    response = service.set_mode(
                        MODE_PROTECTION,
                        reason=str(payload.get("reason") or "manual_protection"),
                    )
                    self._write_json(200, response)
                    return
                if self.path == "/evaluate":
                    response = service.evaluate(
                        reason=str(payload.get("reason") or "manual_evaluate")
                    )
                    self._write_json(200, response)
                    return
                if self.path == "/api/energy/read":
                    self._write_json(200, service.energy_reading())
                    return
                if self.path == "/api/switch":
                    try:
                        key = int(payload.get("key"))
                    except (TypeError, ValueError):
                        raise ValueError("key must be 0 or 1")
                    response = service.manual_switch(
                        key,
                        reason=str(payload.get("reason") or "manual_switch"),
                    )
                    self._write_json(200, response)
                    return
                self._write_json(404, {"error": "not_found"})
            except ValueError as exc:
                self._write_json(400, {"error": str(exc)})
            except Exception as exc:
                service.logger.exception("Thermostat service request failed")
                self._write_json(500, {"error": str(exc)})

        def log_message(self, fmt: str, *args) -> None:
            service.logger.info("%s - %s", self.client_address[0], fmt % args)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

        def _write_json(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, status: int, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SmartBirdThermostatHandler


def _limit_from_query(query: dict, default: int, maximum: int) -> int:
    try:
        raw = query.get("limit", [default])[0]
        return max(1, min(int(raw), maximum))
    except (TypeError, ValueError):
        return default


def _float_from_query(query: dict, name: str) -> float | None:
    try:
        values = query.get(name)
        if not values:
            return None
        return float(values[0])
    except (TypeError, ValueError):
        return None


def _normalize_session_ids(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    result = []
    for item in values:
        text = str(item).strip()
        if text and text != "not_ready":
            result.append(text)
    return sorted(set(result))


def _session_ids_from_payload(payload: dict) -> list[str]:
    if "session_ids" in payload:
        return _normalize_session_ids(payload.get("session_ids"))
    if "session_id" in payload:
        return _normalize_session_ids(payload.get("session_id"))
    return []


def build_config(args) -> SmartBirdThermalConfig:
    env_config = SmartBirdThermalConfig.from_env()

    def pick(name: str, default):
        value = getattr(args, name, None)
        if value is None or value == "":
            return default
        return value

    return SmartBirdThermalConfig(
        enabled=True,
        smartbird_host=pick("smartbird_host", env_config.smartbird_host),
        smartbird_port=pick("smartbird_port", env_config.smartbird_port),
        adb_serial=pick("adb_serial", env_config.adb_serial),
        loop_interval_sec=pick("loop_sec", env_config.loop_interval_sec),
        min_on_sec=pick("min_on_sec", env_config.min_on_sec),
        min_off_sec=pick("min_off_sec", env_config.min_off_sec),
        plum_rain_margin_c=pick("margin_c", env_config.plum_rain_margin_c),
        condensation_guard_c=env_config.condensation_guard_c,
        protection_min_surface_c=pick("min_surface_c", env_config.protection_min_surface_c),
        protection_on_surface_c=pick("on_surface_c", env_config.protection_on_surface_c),
        protection_hysteresis_c=pick("hysteresis_c", env_config.protection_hysteresis_c),
        default_weather_temperature_c=pick(
            "default_ambient_c",
            env_config.default_weather_temperature_c,
        ),
        default_weather_humidity_pct=pick("default_rh", env_config.default_weather_humidity_pct),
        amap_key=pick("amap_key", env_config.amap_key),
        amap_city=pick("amap_city", env_config.amap_city),
        amap_timeout_sec=pick("amap_timeout_sec", env_config.amap_timeout_sec),
        weather_refresh_sec=pick("weather_refresh_sec", env_config.weather_refresh_sec),
    )


def resolve_persistence_paths(args) -> tuple[Path, Path, Path]:
    if args.history_file:
        history_file = Path(args.history_file)
    else:
        data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir(args)
        history_file = data_dir / "history.jsonl"

    if args.events_file:
        events_file = Path(args.events_file)
    else:
        data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir(args)
        events_file = data_dir / "events.jsonl"

    if args.energy_history_file:
        energy_history_file = Path(args.energy_history_file)
    else:
        data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir(args)
        energy_history_file = data_dir / "energy_history.jsonl"

    return history_file, events_file, energy_history_file


def resolve_notification_config_path(args) -> Path:
    if args.notification_config_file:
        return Path(args.notification_config_file)
    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir(args)
    return data_dir / "notification_config.json"


def _default_data_dir(args) -> Path:
    if args.log_file:
        return Path(args.log_file).resolve().parent / "smartbird_thermostat_data"
    return Path.cwd() / "logs" / "smartbird_thermostat_data"


def configure_logging(log_file: str | None) -> logging.Logger:
    logger = logging.getLogger("smartbird_thermostat_service")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19002)
    parser.add_argument("--smartbird-host", default=None)
    parser.add_argument("--smartbird-port", type=int, default=None)
    parser.add_argument("--adb-serial", default=None)
    parser.add_argument("--loop-sec", type=float, default=None)
    parser.add_argument("--min-on-sec", type=float, default=None)
    parser.add_argument("--min-off-sec", type=float, default=None)
    parser.add_argument("--margin-c", type=float, default=None)
    parser.add_argument("--min-surface-c", type=float, default=None)
    parser.add_argument("--on-surface-c", type=float, default=None)
    parser.add_argument("--hysteresis-c", type=float, default=None)
    parser.add_argument("--default-ambient-c", type=float, default=None)
    parser.add_argument("--default-rh", type=float, default=None)
    parser.add_argument("--amap-key", default=None)
    parser.add_argument("--amap-city", default=None)
    parser.add_argument("--amap-timeout-sec", type=float, default=None)
    parser.add_argument("--weather-refresh-sec", type=float, default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--history-file", default=None)
    parser.add_argument("--events-file", default=None)
    parser.add_argument("--energy-history-file", default=None)
    parser.add_argument("--energy-server-url", default="http://127.0.0.1:18988")
    parser.add_argument("--energy-timeout-sec", type=float, default=1.0)
    parser.add_argument("--notification-config-file", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logger = configure_logging(args.log_file)
    config = build_config(args)
    history_file, events_file, energy_history_file = resolve_persistence_paths(args)
    notification_config_file = resolve_notification_config_path(args)
    notification_config = load_notification_config(notification_config_file, logger=logger)
    thermostat = build_controller(
        config,
        logger=logger,
        history_path=history_file,
        events_path=events_file,
    )
    if thermostat is None:
        raise RuntimeError("Smart-Bird thermostat is disabled")

    service = ThermostatSessionService(
        thermostat,
        logger=logger,
        energy_server_url=args.energy_server_url,
        energy_timeout_sec=args.energy_timeout_sec,
        energy_history_path=energy_history_file,
        notification_config=notification_config,
    )
    service.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service))
    logger.info(
        "smartbird thermostat service listening on http://%s:%s, tcp=%s:%s, adb=%s",
        args.host,
        args.port,
        config.smartbird_host,
        config.smartbird_port,
        config.adb_serial,
    )
    logger.info("smartbird thermostat persistence history=%s events=%s", history_file, events_file)
    logger.info("smartbird thermostat energy history=%s", energy_history_file)
    logger.info(
        "smartbird thermostat notification config=%s enabled=%s recipients=%s",
        notification_config_file,
        notification_config.enabled,
        notification_config.recipients,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("smartbird thermostat service interrupted")
    finally:
        server.shutdown()
        server.server_close()
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
