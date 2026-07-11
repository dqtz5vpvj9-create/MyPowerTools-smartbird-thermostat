#!/usr/bin/env python3
import random
import importlib, sys, os
from os.path import dirname, pardir
from pathlib import Path


def import_parents(level: int = 1) -> None:
    global __package__
    file = Path(__file__).resolve()
    parent, top = file.parent, file.parents[level]
    
    sys.path.append(str(top))
#    try:
#        sys.path.remove(str(parent))
#    except ValueError: # already removed
#        pass

    __package__ = '.'.join(parent.parts[len(top.parts):])
    importlib.import_module(__package__) # won't be needed after that

if __name__ == '__main__' and (__package__ is None or len(__package__) == 0):
    import_parents()
sys.path.append(dirname(__file__) + os.sep + pardir)
from py_modules.logging_lib import setup_logging, LogLevel
logger = setup_logging()
# from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
# if __name__ == '__main__':
    # check_conda_interpreter(CONDA_ENV_NAME)
# from py_modules.lib_aosp_base import *
# from py_modules.lib_aosp_testing import *
# from py_modules.lib_sh import shell_run, ssh_command
# assert LIB_AOSP_BASE_INITED

from flask import Flask, Response, jsonify, request
# import jsonschema
import logging
# import redis
from flask import Flask, Response, jsonify, request
from typing import Tuple, Any
import socket
import threading
import time
import os
import argparse
import json
import urllib.request
from test_tools.energy_control_impl import (
    BACKEND_HID,
    BACKEND_UIA,
    SAFETY_MODE_FULL_COMPAT_RESEARCH,
    SAFETY_MODE_SAFE_READONLY,
    CONTROL_OPERATIONS,
    EnergyControlImpl,
    UnsafeOperationBlockedError,
)
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=0, help='Server port')
parser.add_argument('--devices', type=str, default='', help='Comma-separated device titles, e.g. "FNIRSI_C1-6297,FNB-58-69717"')
parser.add_argument('--serial', type=int, default=0, help='Single device product serial (legacy mode)')
parser.add_argument(
    '--backend',
    choices=[BACKEND_UIA, BACKEND_HID],
    default=BACKEND_UIA,
    help='Energy reader backend. uia uses UsbMeter.exe UI automation, hid uses direct readonly HID.',
)
parser.add_argument(
    '--safety-mode',
    choices=[SAFETY_MODE_SAFE_READONLY, SAFETY_MODE_FULL_COMPAT_RESEARCH],
    default=SAFETY_MODE_SAFE_READONLY,
    help='Operation safety policy. Defaults to safe_readonly.',
)
parser.add_argument(
    '--allow-unsafe-control',
    action='store_true',
    help='Allow potentially stateful UI operations such as create/start/pause/stop.',
)
parser.add_argument(
    '--hid-discovery-only',
    action='store_true',
    help='For --backend hid, only enumerate HID candidates. Do not open devices or send HID commands.',
)
parser.add_argument(
    '--hid-path',
    type=str,
    action='append',
    default=[],
    help='For --backend hid, safely probe explicit HID path(s). Can be repeated; comma-separated values are also accepted.',
)
parser.add_argument(
    '--hid-serial',
    type=str,
    action='append',
    default=[],
    help='For --backend hid, safely probe explicit USB serial(s). Can be repeated; comma-separated values are also accepted.',
)
parser.add_argument(
    '--hid-allow-all',
    action='store_true',
    help='Unsafe lab-only HID mode: probe every enumerated candidate. Blocked unless USBMETER_HID_ALLOW_ALL_UNSAFE=1.',
)
parser.add_argument(
    '--thermal-control',
    action='store_true',
    help='Notify the independent Smart-Bird thermostat service on energy session start/stop.',
)
parser.add_argument(
    '--thermal-service-url',
    type=str,
    default=None,
    help='Smart-Bird thermostat service URL, e.g. http://127.0.0.1:19002.',
)
parser.add_argument(
    '--thermal-service-timeout-sec',
    type=float,
    default=None,
    help='HTTP timeout for Smart-Bird thermostat service notifications.',
)
parser.add_argument('--thermal-smartbird-host', type=str, default=None)
parser.add_argument('--thermal-smartbird-port', type=int, default=None)
parser.add_argument('--thermal-adb-serial', type=str, default=None)
parser.add_argument('--thermal-loop-sec', type=float, default=None)
parser.add_argument('--thermal-min-on-sec', type=float, default=None)
parser.add_argument('--thermal-min-off-sec', type=float, default=None)
parser.add_argument('--thermal-margin-c', type=float, default=None)
parser.add_argument('--thermal-min-surface-c', type=float, default=None)
parser.add_argument('--thermal-on-surface-c', type=float, default=None)
parser.add_argument('--thermal-hysteresis-c', type=float, default=None)
parser.add_argument('--thermal-default-ambient-c', type=float, default=None)
parser.add_argument('--thermal-default-rh', type=float, default=None)
parser.add_argument('--thermal-amap-key', type=str, default=None)
parser.add_argument('--thermal-amap-city', type=str, default=None)
from datetime import datetime as datetime_class


def generate_session_id():
    time_str = datetime_class.now().strftime('%y_%m_%d_%H_%M_%S')
    return f"{time_str}_{random.randint(1000, 9999)}"


# ---- 配置 ----
args = parser.parse_args()


def normalize_multi_arg(values):
    result = []
    for value in values or []:
        for item in str(value).split(','):
            item = item.strip()
            if item:
                result.append(item)
    return result or None


hid_paths_arg = normalize_multi_arg(args.hid_path)
hid_serials_arg = normalize_multi_arg(args.hid_serial)

host_name = None
cloud_server_port = 18988
try:
    host_name = socket.gethostname()
    print(f"Host name: {host_name}")
except Exception as e:
    print(f"Failed to get host name: {e}")
if host_name and host_name == "Lis-iMac":
    cloud_server_port = 18999

if args.port:
    cloud_server_port = args.port

# 构建设备配置
devices_config = None
product_serial = None
if args.devices:
    devices_config = [{"title": t.strip()} for t in args.devices.split(',') if t.strip()]
elif args.serial:
    product_serial = args.serial
# else: 两者均为 None，EnergyControlImpl 将自动从 GUI 发现所有设备

energy_control_impl = EnergyControlImpl(
    product_serial=product_serial,
    devices=devices_config,
    backend_name=args.backend,
    safety_mode=args.safety_mode,
    allow_unsafe_control=args.allow_unsafe_control,
    hid_path=hid_paths_arg,
    hid_serial=hid_serials_arg,
    hid_allow_all=args.hid_allow_all,
    hid_discovery_only=args.hid_discovery_only,
)
session_id = generate_session_id()

# 每设备独立的 session_id，用于多设备场景
device_sessions = {name: generate_session_id() for name in energy_control_impl.device_names}
logical_energy_sessions = {}
logical_session_lock = threading.RLock()

app = Flask(__name__)
logger = app.logger
app.logger.addHandler(logging.StreamHandler(sys.stdout))
app.logger.setLevel(logging.INFO)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


class ThermalServiceNotifier:
    def __init__(self, base_url: str, timeout_sec: float = 2.0, logger=None):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.logger = logger

    def notify_session_event(
        self,
        ctrl_msg: str,
        session_ids: list[str],
        active_session_ids: list[str],
    ) -> dict:
        if ctrl_msg == "start":
            path = "/session/start"
        elif ctrl_msg in {"pause", "stop"}:
            path = "/session/stop"
        else:
            return {}
        payload = {
            "source": "energy_server",
            "energy_ctrl_msg": ctrl_msg,
            "session_ids": session_ids,
            "active_session_ids": active_session_ids,
        }
        return self._request_json("POST", path, payload)

    def status(self) -> dict:
        return self._request_json("GET", "/status")

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                text = resp.read().decode("utf-8")
                result = json.loads(text) if text else {}
                result.setdefault("enabled", True)
                result.setdefault("service_url", self.base_url)
                return result
        except Exception as exc:
            if self.logger is not None:
                self.logger.error(f"Thermal service request failed: {method} {url}: {exc}")
            return {
                "enabled": True,
                "service_url": self.base_url,
                "error": str(exc),
            }


thermal_service_enabled = bool(
    args.thermal_control or _env_bool("SMARTBIRD_THERMAL_NOTIFY_ENABLE", False)
)
thermal_service_url = (
    args.thermal_service_url
    or os.environ.get("SMARTBIRD_THERMAL_SERVICE_URL")
    or "http://127.0.0.1:19002"
)
thermal_service_timeout_sec = (
    args.thermal_service_timeout_sec
    if args.thermal_service_timeout_sec is not None
    else _env_float("SMARTBIRD_THERMAL_SERVICE_TIMEOUT_SEC", 2.0)
)
thermal_service_notifier = (
    ThermalServiceNotifier(
        thermal_service_url,
        timeout_sec=thermal_service_timeout_sec,
        logger=logger,
    )
    if thermal_service_enabled
    else None
)
thermal_active_session_ids = set()
thermal_control_lock = threading.RLock()


import signal
from threading import Event

shutdown_event = Event()

def signal_handler(sig, frame):
    logger.info("Received SIGINT, shutting down...")
    shutdown_event.set()
    def shutdown_server():
        func = request.environ.get('werkzeug.server.shutdown')
        if func is None:
            raise RuntimeError('Not running with the Werkzeug Server')
        func()
    shutdown_server()

signal.signal(signal.SIGINT, signal_handler)


STALE_CYCLES_THRESHOLD = 10
REBUILD_RETRY_DELAY_SEC = 2
REBUILD_MAX_ATTEMPTS = 3
HID_STALE_TIMEOUT_SEC = 3.0
HID_DISCOVERY_REFRESH_SEC = 3.0


def rebuild_energy_control_impl() -> bool:
    """Recreate EnergyControlImpl with retries and refresh device sessions."""
    global session_id, energy_control_impl, device_sessions, logical_energy_sessions
    session_id = "not_ready"
    for name in device_sessions:
        device_sessions[name] = "not_ready"
    with logical_session_lock:
        logical_energy_sessions = {}

    try:
        energy_control_impl.kill()
    except Exception as e:
        logger.error(f"Failed to kill UsbMeter during rebuild: {e}")

    for attempt in range(1, REBUILD_MAX_ATTEMPTS + 1):
        try:
            new_impl = EnergyControlImpl(
                product_serial=product_serial,
                devices=devices_config,
                backend_name=args.backend,
                safety_mode=args.safety_mode,
                allow_unsafe_control=args.allow_unsafe_control,
                hid_path=hid_paths_arg,
                hid_serial=hid_serials_arg,
                hid_allow_all=args.hid_allow_all,
                hid_discovery_only=args.hid_discovery_only,
            )
            if not new_impl.device_names and args.backend != BACKEND_HID:
                raise RuntimeError("No devices initialized during rebuild")
            energy_control_impl = new_impl
            session_id = generate_session_id()
            device_sessions = {name: generate_session_id() for name in energy_control_impl.device_names}
            with logical_session_lock:
                logical_energy_sessions = {}
            logger.info(
                f"EnergyControlImpl rebuilt successfully on attempt {attempt}: "
                f"{energy_control_impl.device_names}"
            )
            return True
        except Exception as e:
            logger.error(f"Rebuild attempt {attempt}/{REBUILD_MAX_ATTEMPTS} failed: {e}")
            if attempt < REBUILD_MAX_ATTEMPTS:
                time.sleep(REBUILD_RETRY_DELAY_SEC)

    logger.error("EnergyControlImpl rebuild failed after all retries")
    return False


def sync_hid_device_sessions(refresh_result: dict) -> None:
    for dev_name in refresh_result.get("added", []):
        device_sessions[dev_name] = generate_session_id()
    for dev_name in refresh_result.get("restarted", []):
        device_sessions[dev_name] = generate_session_id()
        with logical_session_lock:
            logical_energy_sessions.pop(dev_name, None)
    for dev_name in refresh_result.get("removed", []):
        device_sessions[dev_name] = "not_ready"
        with logical_session_lock:
            logical_energy_sessions.pop(dev_name, None)
    for dev_name in refresh_result.get("devices", []):
        device_sessions.setdefault(dev_name, generate_session_id())


def monitor_device_connection():
    """监控所有设备的连接状态，任何设备异常时重建整个实例。"""
    global session_id, energy_control_impl, device_sessions
    if args.backend == BACKEND_HID:
        monitor_device_connection_hid()
        return

    # 每设备跟踪上次读数
    previous_values = {name: {"ibus": None, "vbus": None, "nrg": None, "unchanged": 0}
                       for name in energy_control_impl.device_names}

    while not shutdown_event.is_set():
        try:
            any_stale = False
            for dev_name in energy_control_impl.device_names:
                try:
                    current_ibus = energy_control_impl.get_ibus_value(dev_name)
                    current_vbus = energy_control_impl.get_vbus_value(dev_name)
                    current_nrg = energy_control_impl.get_last_value_item(dev_name)
                except Exception as e:
                    logger.error(f"[{dev_name}] Read error: {e}")
                    continue

                prev = previous_values.get(dev_name)
                if prev is None:
                    previous_values[dev_name] = {
                        "ibus": current_ibus,
                        "vbus": current_vbus,
                        "nrg": current_nrg,
                        "unchanged": 0,
                    }
                    continue

                if (
                    current_ibus == prev["ibus"]
                    and current_vbus == prev["vbus"]
                    and current_nrg == prev["nrg"]
                ):
                    prev["unchanged"] += 1
                else:
                    prev["unchanged"] = 0
                prev["ibus"] = current_ibus
                prev["vbus"] = current_vbus
                prev["nrg"] = current_nrg

                if prev["unchanged"] >= STALE_CYCLES_THRESHOLD:
                    logger.warning(
                        f"[{dev_name}] Values unchanged for {STALE_CYCLES_THRESHOLD} cycles, marking stale"
                    )
                    any_stale = True

            if any_stale:
                if rebuild_energy_control_impl():
                    previous_values = {
                        name: {"ibus": None, "vbus": None, "nrg": None, "unchanged": 0}
                        for name in energy_control_impl.device_names
                    }

            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in monitor_device_connection: {e}")
            time.sleep(1)

    logger.info("Exiting monitor_device_connection thread")


def monitor_device_connection_hid():
    """Direct-HID backend health loop based on frame freshness rather than value deltas."""
    global session_id, energy_control_impl, device_sessions
    next_discovery_refresh = 0.0

    while not shutdown_event.is_set():
        try:
            now = time.monotonic()
            stale_devices = []
            for dev_name in energy_control_impl.device_names:
                try:
                    health = energy_control_impl.get_device_health(dev_name)
                except Exception as e:
                    logger.error(f"[{dev_name}] HID health read error: {e}")
                    stale_devices.append(dev_name)
                    continue

                last_sample_monotonic = health.get("last_sample_monotonic")
                connected = bool(health.get("connected", False))
                if not connected:
                    logger.warning(f"[{dev_name}] HID backend marked device disconnected")
                    stale_devices.append(dev_name)
                    continue
                if last_sample_monotonic is None:
                    logger.warning(f"[{dev_name}] No HID sample received yet")
                    stale_devices.append(dev_name)
                    continue
                age = now - float(last_sample_monotonic)
                if age > HID_STALE_TIMEOUT_SEC:
                    logger.warning(
                        f"[{dev_name}] HID samples stale for {age:.2f}s, marking stale"
                    )
                    stale_devices.append(dev_name)

            if stale_devices or now >= next_discovery_refresh:
                refresher = getattr(energy_control_impl, "refresh_hid_devices", None)
                if callable(refresher):
                    refresh_result = refresher(restart_device_names=stale_devices)
                    sync_hid_device_sessions(refresh_result)
                    if refresh_result.get("added") or refresh_result.get("removed") or refresh_result.get("restarted"):
                        logger.info(f"HID device refresh result: {refresh_result}")
                else:
                    rebuild_energy_control_impl()
                next_discovery_refresh = now + HID_DISCOVERY_REFRESH_SEC

            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in monitor_device_connection_hid: {e}")
            time.sleep(1)

    logger.info("Exiting monitor_device_connection_hid thread")


def uses_hid_logical_sessions() -> bool:
    """HID stays device-readonly; the HTTP layer can still emulate task sessions."""
    return (
        args.backend == BACKEND_HID
        and not energy_control_impl.supports_control_operations()
        and not args.hid_discovery_only
    )


def get_server_backend_status():
    status = dict(energy_control_impl.get_backend_status())
    if uses_hid_logical_sessions():
        capabilities = dict(status.get("capabilities", {}))
        capabilities.update({
            "create_session": True,
            "start_session": True,
            "pause_session": True,
            "stop_session": True,
        })
        status["capabilities"] = capabilities
        status["session_operations_mode"] = "readonly_delta"
        status["logical_session_operations_allowed"] = True
        status["control_operations_allowed"] = False
    return status


def resolve_device_name(device_name):
    actual_device = device_name or energy_control_impl._default_device_name
    if actual_device not in energy_control_impl.device_names:
        raise KeyError(
            f"Device '{actual_device}' not found. Available: {energy_control_impl.device_names}"
        )
    return actual_device


def can_use_server_level_hid_session(ctrl_msg: str, device_name) -> bool:
    """Allow session lifecycle events while direct-HID is waiting for hardware."""
    return (
        uses_hid_logical_sessions()
        and ctrl_msg in CONTROL_OPERATIONS
        and device_name is None
        and not energy_control_impl.device_names
    )


def hid_server_level_session_extra(state: str) -> dict:
    return {
        "session_operations_mode": "readonly_delta",
        "logical_session_state": state,
        "server_session": session_id,
        "device_session": None,
        "device_waiting": True,
    }


def parse_wh_value(value) -> float:
    return float(str(value).replace("Wh", "").strip())


def format_wh_value(value: float) -> str:
    return f"{value:.4f}"


def read_cumulative_nrg_wh(device_name: str) -> float:
    reader = getattr(energy_control_impl, "get_nrg_wh_value", None)
    if callable(reader):
        try:
            return float(reader(device_name))
        except (AttributeError, TypeError, ValueError):
            pass
    return parse_wh_value(energy_control_impl.get_last_value_item(device_name))


def validate_hid_logical_session(device_name: str, expected_server_session=None, expected_device_session=None):
    if expected_server_session is not None and session_id != expected_server_session:
        raise RuntimeError("server_session_changed")
    current_device_session = device_sessions.get(device_name)
    if (
        expected_device_session is not None
        and current_device_session != expected_device_session
    ):
        raise RuntimeError("device_session_changed")

    health = energy_control_impl.get_device_health(device_name)
    if not bool(health.get("connected", False)):
        raise RuntimeError("device_disconnected")
    for key in ("read_errors", "write_errors", "crc_errors"):
        if int(health.get(key, 0) or 0) != 0:
            raise RuntimeError(f"{key}_present")
    if health.get("last_error"):
        raise RuntimeError(f"device_error: {health.get('last_error')}")


def compute_delta_wh(begin_wh: float, end_wh: float) -> float:
    delta = end_wh - begin_wh
    if delta < -0.0001:
        raise RuntimeError("negative_delta")
    return max(delta, 0.0)


def hid_session_extra(device_name: str, sess=None, energy_wh=None, end_nrg_wh=None):
    if sess is None:
        sess = logical_energy_sessions.get(device_name, {})
    extra = {
        "session_operations_mode": "readonly_delta",
        "server_session": session_id,
        "device_session": device_sessions.get(device_name),
        "logical_session_state": sess.get("state"),
    }
    if sess.get("logical_session_id") is not None:
        extra["logical_session_id"] = sess.get("logical_session_id")
    if sess.get("begin_nrg_wh") is not None:
        extra["begin_nrg_wh"] = float(sess["begin_nrg_wh"])
    final_end = end_nrg_wh
    if final_end is None:
        final_end = sess.get("end_nrg_wh")
    if final_end is not None:
        extra["end_nrg_wh"] = float(final_end)
    final_energy = energy_wh
    if final_energy is None:
        final_energy = sess.get("delta_wh")
    if final_energy is not None:
        extra["energy_wh"] = float(final_energy)
    return extra


def hid_logical_create(device_name: str):
    validate_hid_logical_session(device_name)
    logical_id = generate_session_id()
    logical_energy_sessions[device_name] = {
        "state": "created",
        "logical_session_id": logical_id,
        "begin_nrg_wh": None,
        "end_nrg_wh": None,
        "delta_wh": None,
        "server_session_id": session_id,
        "device_session_id": device_sessions.get(device_name),
        "created_monotonic": time.monotonic(),
    }
    return logical_id, hid_session_extra(device_name, logical_energy_sessions[device_name])


def hid_logical_start(device_name: str):
    validate_hid_logical_session(device_name)
    existing = logical_energy_sessions.get(device_name)
    logical_id = (
        existing.get("logical_session_id")
        if existing is not None and existing.get("state") == "created"
        else generate_session_id()
    )
    begin_wh = read_cumulative_nrg_wh(device_name)
    logical_energy_sessions[device_name] = {
        "state": "active",
        "logical_session_id": logical_id,
        "begin_nrg_wh": begin_wh,
        "end_nrg_wh": None,
        "delta_wh": None,
        "server_session_id": session_id,
        "device_session_id": device_sessions.get(device_name),
        "started_monotonic": time.monotonic(),
    }
    return logical_id, hid_session_extra(device_name, logical_energy_sessions[device_name])


def hid_logical_pause(device_name: str):
    sess = logical_energy_sessions.get(device_name)
    if sess is None or sess.get("begin_nrg_wh") is None:
        return hid_logical_start(device_name)
    validate_hid_logical_session(
        device_name,
        sess.get("server_session_id"),
        sess.get("device_session_id"),
    )
    end_wh = read_cumulative_nrg_wh(device_name)
    delta_wh = compute_delta_wh(float(sess["begin_nrg_wh"]), end_wh)
    sess.update({
        "state": "paused",
        "end_nrg_wh": end_wh,
        "delta_wh": delta_wh,
        "paused_monotonic": time.monotonic(),
    })
    return sess["logical_session_id"], hid_session_extra(device_name, sess)


def hid_logical_stop(device_name: str):
    sess = logical_energy_sessions.get(device_name)
    if sess is None or sess.get("begin_nrg_wh") is None:
        logical_id, _extra = hid_logical_start(device_name)
        sess = logical_energy_sessions[device_name]
        sess["end_nrg_wh"] = sess["begin_nrg_wh"]
        sess["delta_wh"] = 0.0
        sess["state"] = "stopped"
        sess["stopped_monotonic"] = time.monotonic()
        return logical_id, hid_session_extra(device_name, sess)

    validate_hid_logical_session(
        device_name,
        sess.get("server_session_id"),
        sess.get("device_session_id"),
    )
    if sess.get("state") == "paused" and sess.get("delta_wh") is not None:
        sess["state"] = "stopped"
        sess["stopped_monotonic"] = time.monotonic()
        return sess["logical_session_id"], hid_session_extra(device_name, sess)

    end_wh = read_cumulative_nrg_wh(device_name)
    delta_wh = compute_delta_wh(float(sess["begin_nrg_wh"]), end_wh)
    sess.update({
        "state": "stopped",
        "end_nrg_wh": end_wh,
        "delta_wh": delta_wh,
        "stopped_monotonic": time.monotonic(),
    })
    return sess["logical_session_id"], hid_session_extra(device_name, sess)


def hid_logical_read_nrg(device_name: str):
    sess = logical_energy_sessions.get(device_name)
    if sess is None:
        cumulative_wh = read_cumulative_nrg_wh(device_name)
        extra = hid_session_extra(device_name, {}, energy_wh=cumulative_wh, end_nrg_wh=cumulative_wh)
        extra["cumulative_nrg_wh"] = cumulative_wh
        return format_wh_value(cumulative_wh), extra
    if sess.get("begin_nrg_wh") is None:
        return format_wh_value(0.0), hid_session_extra(device_name, sess, energy_wh=0.0)
    validate_hid_logical_session(
        device_name,
        sess.get("server_session_id"),
        sess.get("device_session_id"),
    )
    if sess.get("delta_wh") is not None and sess.get("state") in {"paused", "stopped"}:
        delta_wh = float(sess["delta_wh"])
        return format_wh_value(delta_wh), hid_session_extra(device_name, sess, energy_wh=delta_wh)
    current_wh = read_cumulative_nrg_wh(device_name)
    delta_wh = compute_delta_wh(float(sess["begin_nrg_wh"]), current_wh)
    return (
        format_wh_value(delta_wh),
        hid_session_extra(device_name, sess, energy_wh=delta_wh, end_nrg_wh=current_wh),
    )


def handle_hid_logical_command(ctrl_msg: str, device_name: str):
    with logical_session_lock:
        if ctrl_msg == "create":
            logical_id, extra = hid_logical_create(device_name)
            return "OK", logical_id, extra
        if ctrl_msg == "start":
            logical_id, extra = hid_logical_start(device_name)
            return "OK", logical_id, extra
        if ctrl_msg == "pause":
            logical_id, extra = hid_logical_pause(device_name)
            return "OK", logical_id, extra
        if ctrl_msg == "stop":
            logical_id, extra = hid_logical_stop(device_name)
            return "OK", logical_id, extra
        if ctrl_msg == "read_nrg":
            logical_id = logical_energy_sessions.get(device_name, {}).get(
                "logical_session_id",
                device_sessions.get(device_name, session_id),
            )
            message, extra = hid_logical_read_nrg(device_name)
            return message, logical_id, extra
    raise ValueError(f"Unsupported HID logical command: {ctrl_msg}")


def apply_thermal_session_event(ctrl_msg: str, session_ids) -> dict:
    if thermal_service_notifier is None or ctrl_msg not in CONTROL_OPERATIONS:
        return {}

    normalized_ids = sorted({
        str(item)
        for item in session_ids
        if item is not None and str(item) and str(item) != "not_ready"
    })
    if not normalized_ids:
        return {}

    with thermal_control_lock:
        if ctrl_msg == "start":
            thermal_active_session_ids.update(normalized_ids)
        elif ctrl_msg in {"pause", "stop"}:
            thermal_active_session_ids.difference_update(normalized_ids)
        else:
            return {}
        active_sessions = sorted(thermal_active_session_ids)

    snapshot = thermal_service_notifier.notify_session_event(
        ctrl_msg=ctrl_msg,
        session_ids=normalized_ids,
        active_session_ids=active_sessions,
    )
    snapshot["energy_server_active_session_ids"] = active_sessions
    return {"thermal_control": snapshot}


@app.route('/handshake', methods=['GET'])
def handshake() -> Tuple[Response, int]:
    """Handshake endpoint to check if server is online"""
    logger.info("Received handshake request")
    return jsonify({
        "status": "online",
        "session_id": session_id,
        "devices": energy_control_impl.device_names,
        "device_sessions": device_sessions,
        "backend": get_server_backend_status(),
    }), 200


@app.route('/energy_control', methods=['POST'])
def energy_control() -> Tuple[Response, int]:
    """
    能量控制端点，支持多设备。

    请求 JSON:
        {
            "energy_ctrl_msg": "create" | "start" | "pause" | "stop" | "read_nrg" | "read_vbus" | "read_ibus",
            "device_name": "FNIRSI_C1-6297"  // 可选，不传则操作默认设备
        }

    响应 JSON:
        {
            "message": "OK" | "<energy_value>" | "<error>",
            "session_id": "...",
            "device_name": "FNIRSI_C1-6297"
        }
    """
    global session_id, energy_control_impl
    data: Any | None = request.get_json(silent=True)
    error_code = 200
    if data is None:
        return jsonify({"message": "No data received", "session_id": session_id}), 400

    ctrl_msg = data.get("energy_ctrl_msg")
    device_name = data.get("device_name")  # 可选，None 表示默认设备
    if not ctrl_msg:
        return jsonify({"message": "Missing energy_ctrl_msg", "session_id": session_id}), 400

    logger.info(f"Received: ctrl_msg={ctrl_msg}, device={device_name or 'default'}")
    message = "OK"
    actual_device = device_name or energy_control_impl._default_device_name
    response_session_id = device_sessions.get(actual_device, session_id)
    response_extra = {}
    handled = False
    try:
        if can_use_server_level_hid_session(ctrl_msg, device_name):
            actual_device = None
            response_session_id = session_id
            response_extra = hid_server_level_session_extra(
                "active" if ctrl_msg == "start" else ctrl_msg
            )
            handled = True
        if not handled:
            actual_device = resolve_device_name(device_name)
            if (
                actual_device is not None
                and uses_hid_logical_sessions()
                and ctrl_msg in CONTROL_OPERATIONS.union({"read_nrg"})
            ):
                message, response_session_id, response_extra = handle_hid_logical_command(
                    ctrl_msg,
                    actual_device,
                )
            elif ctrl_msg in CONTROL_OPERATIONS and not energy_control_impl.supports_control_operations():
                raise UnsafeOperationBlockedError(
                    "Control operations are blocked by the current backend safety policy."
                )
            elif ctrl_msg == "create":
                energy_control_impl.click_create_button(device_name)
            elif ctrl_msg == "start":
                energy_control_impl.click_start_button(device_name)
            elif ctrl_msg == "pause":
                energy_control_impl.click_pause_button(device_name)
            elif ctrl_msg == "stop":
                energy_control_impl.click_stop_button(device_name)
            elif ctrl_msg == "read_nrg":
                energy = energy_control_impl.get_last_value_item(device_name)
                energy = energy.replace("Wh", "").strip()
                message = energy
            elif ctrl_msg == "read_vbus":
                message = energy_control_impl.get_vbus_value(device_name)
            elif ctrl_msg == "read_ibus":
                message = energy_control_impl.get_ibus_value(device_name)
            else:
                message = f"Unknown command: {ctrl_msg}"
                error_code = 400
    except UnsafeOperationBlockedError as e:
        message = str(e)
        error_code = 403
    except KeyError as e:
        message = str(e)
        error_code = 404
    except Exception as e:
        message = str(e)
        error_code = 500

    if error_code == 200 and message == "OK":
        response_extra.update(apply_thermal_session_event(ctrl_msg, [response_session_id]))

    response_payload = {
        "message": message,
        "session_id": response_session_id,
        "device_name": actual_device,
        "backend": get_server_backend_status(),
    }
    response_payload.update(response_extra)
    retval = jsonify(response_payload), error_code
    logger.info(f"Response: {retval}")
    return retval


@app.route('/devices', methods=['GET'])
def list_devices() -> Tuple[Response, int]:
    """列出所有可用设备及其当前值。"""
    values = energy_control_impl.get_all_values()
    return jsonify({
        "devices": energy_control_impl.device_names,
        "values": values,
        "device_sessions": device_sessions,
        "backend": get_server_backend_status(),
    }), 200


@app.route('/backend_status', methods=['GET'])
def backend_status() -> Tuple[Response, int]:
    """Expose backend mode and safety capabilities for operators."""
    return jsonify(get_server_backend_status()), 200


@app.route('/thermal_control/status', methods=['GET'])
def thermal_control_status() -> Tuple[Response, int]:
    if thermal_service_notifier is None:
        return jsonify({"enabled": False}), 200
    with thermal_control_lock:
        active_sessions = sorted(thermal_active_session_ids)
    snapshot = thermal_service_notifier.status()
    snapshot["energy_server_active_session_ids"] = active_sessions
    return jsonify(snapshot), 200


@app.route('/energy_control_all', methods=['POST'])
def energy_control_all() -> Tuple[Response, int]:
    """
    批量操作所有设备。

    请求 JSON:
        {"energy_ctrl_msg": "create" | "start" | "pause" | "stop" | "read_nrg" | "read_vbus" | "read_ibus"}

    响应 JSON:
        {"results": {device_name: {message, session_id}}}
    """
    data: Any | None = request.get_json(silent=True)
    if data is None:
        return jsonify({"message": "No data received"}), 400

    ctrl_msg = data.get("energy_ctrl_msg")
    if not ctrl_msg:
        return jsonify({"message": "Missing energy_ctrl_msg"}), 400

    logger.info(f"Received batch command: {ctrl_msg}")
    if (
        ctrl_msg in CONTROL_OPERATIONS
        and uses_hid_logical_sessions()
        and not energy_control_impl.device_names
    ):
        results = {
            "_server": {
                "message": "OK",
                "session_id": session_id,
            }
        }
        results["_server"].update(
            hid_server_level_session_extra("active" if ctrl_msg == "start" else ctrl_msg)
        )
        response_payload = {"results": results, "backend": get_server_backend_status()}
        response_payload.update(apply_thermal_session_event(ctrl_msg, [session_id]))
        return jsonify(response_payload), 200

    if (
        ctrl_msg in CONTROL_OPERATIONS
        and not energy_control_impl.supports_control_operations()
        and not uses_hid_logical_sessions()
    ):
        message = (
            "Control operations are blocked by the current backend safety policy. "
            "Restart with --safety-mode full_compat_research --allow-unsafe-control "
            "only after protocol validation."
        )
        results = {
            dev_name: {
                "message": message,
                "error": True,
                "status_code": 403,
                "session_id": device_sessions.get(dev_name, session_id),
            }
            for dev_name in energy_control_impl.device_names
        }
        return jsonify({"results": results, "backend": get_server_backend_status()}), 403

    results = {}
    for dev_name in energy_control_impl.device_names:
        try:
            if uses_hid_logical_sessions() and ctrl_msg in CONTROL_OPERATIONS.union({"read_nrg"}):
                message, logical_session_id, extra = handle_hid_logical_command(ctrl_msg, dev_name)
                results[dev_name] = {
                    "message": message,
                    "session_id": logical_session_id,
                }
                results[dev_name].update(extra)
            elif ctrl_msg == "create":
                energy_control_impl.click_create_button(dev_name)
                results[dev_name] = {"message": "OK"}
            elif ctrl_msg == "start":
                energy_control_impl.click_start_button(dev_name)
                results[dev_name] = {"message": "OK"}
            elif ctrl_msg == "pause":
                energy_control_impl.click_pause_button(dev_name)
                results[dev_name] = {"message": "OK"}
            elif ctrl_msg == "stop":
                energy_control_impl.click_stop_button(dev_name)
                results[dev_name] = {"message": "OK"}
            elif ctrl_msg == "read_nrg":
                energy = energy_control_impl.get_last_value_item(dev_name)
                energy = energy.replace("Wh", "").strip()
                results[dev_name] = {"message": energy}
            elif ctrl_msg == "read_vbus":
                results[dev_name] = {"message": energy_control_impl.get_vbus_value(dev_name)}
            elif ctrl_msg == "read_ibus":
                results[dev_name] = {"message": energy_control_impl.get_ibus_value(dev_name)}
            else:
                results[dev_name] = {"message": f"Unknown command: {ctrl_msg}"}
        except UnsafeOperationBlockedError as e:
            results[dev_name] = {"message": str(e), "error": True, "status_code": 403}
        except Exception as e:
            results[dev_name] = {"message": str(e), "error": True}

    # 附加每设备 session_id
    for dev_name in results:
        results[dev_name].setdefault("session_id", device_sessions.get(dev_name, session_id))

    status_code = 200
    if (
        ctrl_msg in CONTROL_OPERATIONS
        and not energy_control_impl.supports_control_operations()
        and not uses_hid_logical_sessions()
    ):
        status_code = 403
    successful_sessions = [
        item.get("session_id")
        for item in results.values()
        if not item.get("error") and item.get("message") == "OK"
    ]
    response_payload = {"results": results, "backend": get_server_backend_status()}
    if status_code == 200:
        response_payload.update(apply_thermal_session_event(ctrl_msg, successful_sessions))
    return jsonify(response_payload), status_code


# check_conda_interpreter("android_automatic")
monitor_target = (
    monitor_device_connection_hid
    if args.backend == BACKEND_HID
    else monitor_device_connection
)
threading.Thread(target=monitor_target, daemon=True).start()
app.run(host='0.0.0.0', port=cloud_server_port)
