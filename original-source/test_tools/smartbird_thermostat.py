#!/usr/bin/env python3
"""Dew-point aware cooling control for Smart-Bird TCP switches."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Protocol


KEY_OFF = 0
KEY_ON = 1
MODE_PROTECTION = "dewpoint_protection"
MODE_EXPERIMENT = "experiment_unconditional"


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


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def dew_point_celsius(temperature_c: float, relative_humidity_pct: float) -> float:
    """Return dew point in Celsius using the Magnus approximation."""
    rh = max(1.0, min(100.0, float(relative_humidity_pct)))
    temp = float(temperature_c)
    a = 17.625
    b = 243.04
    gamma = math.log(rh / 100.0) + (a * temp) / (b + temp)
    return (b * gamma) / (a - gamma)


@dataclass
class WeatherSample:
    temperature_c: float
    relative_humidity_pct: float
    source: str = "configured"


@dataclass
class ThermalSample:
    surface_c: float
    sensor_name: str
    temperatures: dict[str, float]


@dataclass
class ThermalDecision:
    mode: str
    desired_key: int | None
    applied_key: int | None
    reason: str
    surface_c: float | None
    dew_point_c: float | None
    off_threshold_c: float | None
    on_threshold_c: float | None
    blocked_by_min_runtime: bool = False


@dataclass
class SmartBirdThermalConfig:
    enabled: bool = False
    smartbird_host: str = "0.0.0.0"
    smartbird_port: int = 19001
    adb_serial: str = "10.33.0.243:5555"
    loop_interval_sec: float = 10.0
    min_on_sec: float = 60.0
    min_off_sec: float = 60.0
    plum_rain_margin_c: float = 5.0
    condensation_guard_c: float = 3.0
    protection_min_surface_c: float = 30.0
    protection_on_surface_c: float = 35.0
    protection_hysteresis_c: float = 4.0
    default_weather_temperature_c: float = 28.0
    default_weather_humidity_pct: float = 95.0
    amap_key: str = ""
    amap_city: str = "310112"
    amap_timeout_sec: float = 3.0
    weather_refresh_sec: float = 300.0

    @classmethod
    def from_env(cls) -> "SmartBirdThermalConfig":
        return cls(
            enabled=_env_bool("SMARTBIRD_THERMAL_ENABLE", False),
            smartbird_host=os.environ.get("SMARTBIRD_TCP_HOST", "0.0.0.0"),
            smartbird_port=_env_int("SMARTBIRD_TCP_PORT", 19001),
            adb_serial=os.environ.get("SMARTBIRD_THERMAL_ADB_SERIAL", "10.33.0.243:5555"),
            loop_interval_sec=_env_float("SMARTBIRD_THERMAL_LOOP_SEC", 10.0),
            min_on_sec=_env_float("SMARTBIRD_THERMAL_MIN_ON_SEC", 60.0),
            min_off_sec=_env_float("SMARTBIRD_THERMAL_MIN_OFF_SEC", 60.0),
            plum_rain_margin_c=_env_float("SMARTBIRD_THERMAL_MARGIN_C", 5.0),
            condensation_guard_c=_env_float("SMARTBIRD_THERMAL_GUARD_C", 3.0),
            protection_min_surface_c=_env_float("SMARTBIRD_THERMAL_MIN_SURFACE_C", 30.0),
            protection_on_surface_c=_env_float("SMARTBIRD_THERMAL_ON_SURFACE_C", 35.0),
            protection_hysteresis_c=_env_float("SMARTBIRD_THERMAL_HYSTERESIS_C", 4.0),
            default_weather_temperature_c=_env_float("SMARTBIRD_THERMAL_DEFAULT_AMBIENT_C", 28.0),
            default_weather_humidity_pct=_env_float("SMARTBIRD_THERMAL_DEFAULT_RH", 95.0),
            amap_key=os.environ.get("AMAP_API_KEY", os.environ.get("GAODE_API_KEY", "")),
            amap_city=os.environ.get("SMARTBIRD_THERMAL_AMAP_CITY", "310112"),
            amap_timeout_sec=_env_float("SMARTBIRD_THERMAL_AMAP_TIMEOUT_SEC", 3.0),
            weather_refresh_sec=_env_float("SMARTBIRD_THERMAL_WEATHER_REFRESH_SEC", 300.0),
        )


class SwitchSink(Protocol):
    def set_key(self, key: int, reason: str = "") -> bool:
        ...

    def get_key(self) -> int | None:
        ...

    def snapshot(self) -> dict:
        ...


class WeatherProvider:
    def __init__(self, config: SmartBirdThermalConfig):
        self.config = config
        self._lock = threading.Lock()
        self._last_fetch_monotonic = 0.0
        self._last_sample: WeatherSample | None = None

    def current(self) -> WeatherSample:
        now = time.monotonic()
        with self._lock:
            if (
                self._last_sample is not None
                and now - self._last_fetch_monotonic < self.config.weather_refresh_sec
            ):
                return self._last_sample
            sample = self._fetch_amap() or self._fallback()
            self._last_sample = sample
            self._last_fetch_monotonic = now
            return sample

    def _fallback(self) -> WeatherSample:
        return WeatherSample(
            temperature_c=self.config.default_weather_temperature_c,
            relative_humidity_pct=self.config.default_weather_humidity_pct,
            source="fallback",
        )

    def _fetch_amap(self) -> WeatherSample | None:
        if not self.config.amap_key:
            return None
        query = urllib.parse.urlencode(
            {
                "key": self.config.amap_key,
                "city": self.config.amap_city,
                "extensions": "base",
            }
        )
        url = f"https://restapi.amap.com/v3/weather/weatherInfo?{query}"
        try:
            with urllib.request.urlopen(url, timeout=self.config.amap_timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        lives = payload.get("lives") or []
        if not lives:
            return None
        live = lives[0]
        try:
            return WeatherSample(
                temperature_c=float(live["temperature"]),
                relative_humidity_pct=float(live["humidity"]),
                source="amap",
            )
        except (KeyError, TypeError, ValueError):
            return None


THERMAL_RE = re.compile(r"Temperature\{mValue=([^,]+).*?mName=([^,}]+)")


def normalize_adb_serials(serials: str | Iterable[str] | None) -> list[str]:
    if serials is None:
        return []
    if isinstance(serials, str):
        raw_items = [serials]
    else:
        raw_items = [str(item) for item in serials]
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        for item in re.split(r"[,;\s]+", raw):
            text = item.strip()
            if not text or text in seen:
                continue
            result.append(text)
            seen.add(text)
    return result


def parse_thermalservice_temperatures(output: str) -> dict[str, float]:
    temperatures: dict[str, float] = {}
    capture = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "Current temperatures from HAL:":
            capture = True
            continue
        if line == "Current cooling devices from HAL:":
            capture = False
            continue
        if not capture:
            continue
        match = THERMAL_RE.search(line)
        if match is None:
            continue
        value, name = match.groups()
        try:
            temperatures[name] = float(value)
        except ValueError:
            continue
    return temperatures


class AndroidThermalReader:
    SENSOR_PRIORITY = (
        "VIRTUAL-SKIN",
        "VIRTUAL-SKIN-CHARGE-WIRED",
        "VIRTUAL-SKIN-MODEL",
        "VIRTUAL-SKIN-LEGACY",
        "VIRTUAL-USB-UI",
    )

    def __init__(self, serial: str | Iterable[str], timeout_sec: float = 5.0):
        self.serials = normalize_adb_serials(serial)
        if not self.serials:
            raise ValueError("at least one adb serial is required")
        self.serial = self.serials[0]
        self.timeout_sec = timeout_sec
        self.adb_executable = self._resolve_adb_executable()

    @staticmethod
    def _resolve_adb_executable() -> str:
        configured = os.environ.get("ADB_PATH", "").strip()
        if configured:
            return configured

        script_path = Path(__file__).resolve()
        if len(script_path.parents) > 3:
            bundled = script_path.parents[3] / "Tools" / "AndroidPlatformTools" / "adb.exe"
            if bundled.is_file():
                return str(bundled)

        discovered = shutil.which("adb")
        if discovered:
            return discovered

        return "adb"

    def current(self) -> ThermalSample:
        errors: list[str] = []
        for serial in self.serials:
            try:
                proc = subprocess.run(
                    [self.adb_executable, "-s", serial, "shell", "dumpsys", "thermalservice"],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    check=True,
                )
                temperatures = parse_thermalservice_temperatures(proc.stdout)
                for name in self.SENSOR_PRIORITY:
                    value = temperatures.get(name)
                    if value is not None:
                        self.serial = serial
                        return ThermalSample(value, f"{name}@{serial}", temperatures)
                errors.append(f"{serial}: no usable Android surface temperature sensor found")
            except Exception as exc:
                errors.append(f"{serial}: {exc}")
        raise RuntimeError("all adb thermal targets failed: " + " | ".join(errors))


class SmartBirdTcpSwitch:
    def __init__(
        self,
        host: str,
        port: int,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.host = host
        self.port = port
        self.logger = logger
        self.clock = clock
        self._lock = threading.RLock()
        self._clients: list[socket.socket] = []
        self._desired_key: int | None = None
        self._reported_key: int | None = None
        self._last_command: dict | None = None
        self._last_telemetry: dict | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            server = self._server_socket
            clients = list(self._clients)
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        for client in clients:
            try:
                client.close()
            except OSError:
                pass

    def set_key(self, key: int, reason: str = "") -> bool:
        key = int(key)
        if key not in (KEY_OFF, KEY_ON):
            raise ValueError(f"invalid Smart-Bird key: {key}")
        message = {
            "type": "event",
            "key": key,
            "messageId": f"thermal-{int(time.time() * 1000)}",
        }
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self._desired_key = key
            self._last_command = {
                "key": key,
                "reason": reason,
                "sent_monotonic": self.clock(),
                "clients": len(self._clients),
            }
            clients = list(self._clients)
        sent = False
        for client in clients:
            try:
                client.sendall(payload)
                sent = True
            except OSError:
                self._drop_client(client)
        self._log(f"smartbird command key={key} reason={reason} clients={len(clients)}")
        return sent

    def get_key(self) -> int | None:
        with self._lock:
            return self._reported_key if self._reported_key is not None else self._desired_key

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "host": self.host,
                "port": self.port,
                "desired_key": self._desired_key,
                "reported_key": self._reported_key,
                "client_count": len(self._clients),
                "last_command": self._last_command,
                "last_telemetry": self._last_telemetry,
            }

    def _serve(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(5)
            server.settimeout(1.0)
            with self._lock:
                self._server_socket = server
            self._log(f"smartbird tcp listening on {self.host}:{self.port}")
            while not self._stop_event.is_set():
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._register_client(client, addr)

    def _register_client(self, client: socket.socket, addr) -> None:
        client.settimeout(1.0)
        with self._lock:
            self._clients.append(client)
            desired = self._desired_key
        self._log(f"smartbird client connected {addr}")
        if desired is not None:
            self.set_key(desired, "sync_new_tcp_client")
        threading.Thread(target=self._read_client, args=(client, addr), daemon=True).start()

    def _read_client(self, client: socket.socket, addr) -> None:
        buffer = ""
        while not self._stop_event.is_set():
            try:
                data = client.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buffer += data.decode("utf-8", "replace")
            objects, buffer = _extract_json_objects(buffer)
            for obj in objects:
                self._handle_telemetry(obj)
        self._drop_client(client)
        self._log(f"smartbird client disconnected {addr}")

    def _handle_telemetry(self, obj: dict) -> None:
        with self._lock:
            self._last_telemetry = obj
            if "key" in obj:
                try:
                    self._reported_key = int(obj["key"])
                except (TypeError, ValueError):
                    pass

    def _drop_client(self, client: socket.socket) -> None:
        with self._lock:
            self._clients = [item for item in self._clients if item is not client]
        try:
            client.close()
        except OSError:
            pass

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except Exception:
                pass


def _extract_json_objects(text: str) -> tuple[list[dict], str]:
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            if idx > 0:
                return objects, text[idx:]
            return objects, text
        if isinstance(obj, dict):
            objects.append(obj)
        idx = end
    return objects, ""


class SmartBirdThermostat:
    def __init__(
        self,
        config: SmartBirdThermalConfig,
        switch: SwitchSink,
        thermal_reader: AndroidThermalReader | None = None,
        weather_provider: WeatherProvider | None = None,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
        history_path: str | os.PathLike | None = None,
        events_path: str | os.PathLike | None = None,
    ):
        self.config = config
        self.switch = switch
        self.thermal_reader = thermal_reader or AndroidThermalReader(config.adb_serial)
        self.weather_provider = weather_provider or WeatherProvider(config)
        self.logger = logger
        self.clock = clock
        self.mode = MODE_PROTECTION
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_key: int | None = switch.get_key()
        self._last_change_monotonic: float | None = None
        self._last_decision: ThermalDecision | None = None
        self._history = deque(maxlen=7200)
        self._events = deque(maxlen=1000)
        self._event_seq = 0
        self._history_path = Path(history_path) if history_path else None
        self._events_path = Path(events_path) if events_path else None
        self._load_persistent_records()

    def start(self) -> None:
        start_switch = getattr(self.switch, "start", None)
        if callable(start_switch):
            start_switch()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        stop_switch = getattr(self.switch, "stop", None)
        if callable(stop_switch):
            stop_switch()

    def enter_experiment(self, reason: str = "energy_session_start") -> dict:
        with self._lock:
            self.mode = MODE_EXPERIMENT
            self._record_event_locked(
                "mode",
                f"mode={MODE_EXPERIMENT}",
                reason=reason,
            )
        decision = self.evaluate_once(reason=reason, force=True)
        return self.snapshot(decision)

    def enter_protection(self, reason: str = "energy_session_end") -> dict:
        with self._lock:
            self.mode = MODE_PROTECTION
            self._record_event_locked(
                "mode",
                f"mode={MODE_PROTECTION}",
                reason=reason,
            )
        decision = self.evaluate_once(reason=reason)
        return self.snapshot(decision)

    def manual_switch(self, key: int, reason: str = "manual_switch") -> dict:
        if key not in {KEY_OFF, KEY_ON}:
            raise ValueError(f"unsupported switch key: {key}")
        now = self.clock()
        detail = f"{reason}: manual_switch {'on' if key == KEY_ON else 'off'}"
        applied = self._apply_key(
            key,
            now,
            detail,
            force=True,
            safety_override=key == KEY_OFF,
        )
        decision = ThermalDecision(
            mode=self.mode,
            desired_key=key,
            applied_key=applied,
            reason=detail,
            surface_c=None,
            dew_point_c=None,
            off_threshold_c=None,
            on_threshold_c=None,
        )
        self._remember_decision(decision)
        return self.snapshot(decision)

    def evaluate_once(self, reason: str = "periodic", force: bool = False) -> ThermalDecision:
        now = self.clock()
        with self._lock:
            mode = self.mode
        if mode == MODE_EXPERIMENT:
            current_key = self.switch.get_key()
            if current_key is None:
                current_key = self._last_key
            decision = ThermalDecision(
                mode=mode,
                desired_key=KEY_ON,
                applied_key=self._apply_key(
                    KEY_ON,
                    now,
                    reason,
                    force=force or current_key != KEY_ON,
                ),
                reason=reason,
                surface_c=None,
                dew_point_c=None,
                off_threshold_c=None,
                on_threshold_c=None,
            )
            self._remember_decision(decision)
            return decision

        try:
            thermal = self.thermal_reader.current()
            weather = self.weather_provider.current()
            dew_point = dew_point_celsius(
                weather.temperature_c,
                weather.relative_humidity_pct,
            )
        except Exception as exc:
            detail = f"{reason}: sensor_error: {exc} protect_off_no_adb"
            applied = self._apply_key(
                KEY_OFF,
                now,
                detail,
                force=force,
                safety_override=True,
            )
            decision = ThermalDecision(
                mode=mode,
                desired_key=KEY_OFF,
                applied_key=applied,
                reason=detail,
                surface_c=None,
                dew_point_c=None,
                off_threshold_c=None,
                on_threshold_c=None,
            )
            self._remember_decision(decision)
            self._log(decision.reason)
            return decision

        off_threshold = max(
            dew_point + self.config.plum_rain_margin_c,
            self.config.protection_min_surface_c,
        )
        on_threshold = max(
            off_threshold + self.config.protection_hysteresis_c,
            self.config.protection_on_surface_c,
        )
        current_key = self.switch.get_key()
        if current_key is None:
            current_key = self._last_key
        elif self._last_key is None:
            self._last_key = current_key

        desired: int | None = None
        safety_override = False
        detail = (
            f"{reason}: surface={thermal.surface_c:.2f}C sensor={thermal.sensor_name} "
            f"dew={dew_point:.2f}C off<={off_threshold:.2f}C on>={on_threshold:.2f}C "
            f"weather={weather.temperature_c:.1f}C/{weather.relative_humidity_pct:.0f}% {weather.source}"
        )
        if thermal.surface_c <= off_threshold:
            desired = KEY_OFF
            safety_override = True
            detail += " protect_off"
        elif current_key == KEY_ON:
            desired = KEY_ON
            detail += " hold_on"
        elif thermal.surface_c >= on_threshold:
            desired = KEY_ON
            detail += " protect_on"
        else:
            detail += " hold_off"

        applied = self._apply_key(
            desired,
            now,
            detail,
            force=force,
            safety_override=safety_override,
        )
        blocked = desired is not None and applied is None and desired != self._last_key
        decision = ThermalDecision(
            mode=mode,
            desired_key=desired,
            applied_key=applied,
            reason=detail,
            surface_c=thermal.surface_c,
            dew_point_c=dew_point,
            off_threshold_c=off_threshold,
            on_threshold_c=on_threshold,
            blocked_by_min_runtime=blocked,
        )
        self._remember_decision(decision)
        return decision

    def snapshot(self, decision: ThermalDecision | None = None) -> dict:
        with self._lock:
            last_decision = decision or self._last_decision
            return {
                "enabled": self.config.enabled,
                "mode": self.mode,
                "last_key": self._last_key,
                "last_change_monotonic": self._last_change_monotonic,
                "last_decision": last_decision.__dict__ if last_decision else None,
                "history_points": len(self._history),
                "event_count": len(self._events),
                "persistence": {
                    "history_path": str(self._history_path) if self._history_path else None,
                    "events_path": str(self._events_path) if self._events_path else None,
                },
                "switch": self.switch.snapshot(),
                "config": {
                    "smartbird_port": self.config.smartbird_port,
                    "adb_serial": self.config.adb_serial,
                    "min_on_sec": self.config.min_on_sec,
                    "min_off_sec": self.config.min_off_sec,
                    "plum_rain_margin_c": self.config.plum_rain_margin_c,
                    "protection_min_surface_c": self.config.protection_min_surface_c,
                    "protection_on_surface_c": self.config.protection_on_surface_c,
                },
            }

    def history(self, limit: int = 720, since: float | None = None) -> list[dict]:
        limit = max(1, min(int(limit), self._history.maxlen or 7200))
        with self._lock:
            records = list(self._history)
        if since is not None:
            records = [
                item for item in records
                if float(item.get("timestamp") or 0.0) >= since
            ]
        return records[-limit:]

    def events(self, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), self._events.maxlen or 1000))
        with self._lock:
            return list(self._events)[-limit:]

    def record_event(
        self,
        event_type: str,
        message: str,
        key: int | None = None,
        reason: str = "",
        **extra,
    ) -> dict:
        with self._lock:
            return self._record_event_locked(
                event_type,
                message,
                key=key,
                reason=reason,
                **extra,
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.evaluate_once()
            self._stop_event.wait(self.config.loop_interval_sec)

    def _apply_key(
        self,
        key: int | None,
        now: float,
        reason: str,
        force: bool = False,
        safety_override: bool = False,
    ) -> int | None:
        if key is None:
            return None
        current = self.switch.get_key()
        if current is None:
            current = self._last_key
        if current == key and not force:
            self._last_key = key
            return key
        if not force and not safety_override and self._last_change_monotonic is not None:
            elapsed = now - self._last_change_monotonic
            if current == KEY_ON and key == KEY_OFF and elapsed < self.config.min_on_sec:
                message = f"thermal switch blocked by min_on_sec: {reason}"
                self._log(message)
                with self._lock:
                    self._record_event_locked(
                        "blocked",
                        "min_on_sec",
                        key=key,
                        reason=reason,
                        elapsed_sec=elapsed,
                    )
                return None
            if current == KEY_OFF and key == KEY_ON and elapsed < self.config.min_off_sec:
                message = f"thermal switch blocked by min_off_sec: {reason}"
                self._log(message)
                with self._lock:
                    self._record_event_locked(
                        "blocked",
                        "min_off_sec",
                        key=key,
                        reason=reason,
                        elapsed_sec=elapsed,
                    )
                return None
        self.switch.set_key(key, reason)
        self._last_key = key
        self._last_change_monotonic = now
        with self._lock:
            self._record_event_locked(
                "switch",
                "key_changed",
                key=key,
                reason=reason,
            )
        return key

    def _remember_decision(self, decision: ThermalDecision) -> None:
        with self._lock:
            self._last_decision = decision
            record = {
                "timestamp": time.time(),
                "mode": decision.mode,
                "desired_key": decision.desired_key,
                "applied_key": decision.applied_key,
                "last_key": self._last_key,
                "surface_c": decision.surface_c,
                "dew_point_c": decision.dew_point_c,
                "off_threshold_c": decision.off_threshold_c,
                "on_threshold_c": decision.on_threshold_c,
                "blocked_by_min_runtime": decision.blocked_by_min_runtime,
                "reason": decision.reason,
            }
            self._history.append(record)
            self._append_jsonl(self._history_path, record)

    def _record_event_locked(
        self,
        event_type: str,
        message: str,
        key: int | None = None,
        reason: str = "",
        **extra,
    ) -> None:
        self._event_seq += 1
        event = {
            "seq": self._event_seq,
            "timestamp": time.time(),
            "type": event_type,
            "message": message,
            "mode": self.mode,
            "key": key,
            "last_key": self._last_key,
            "reason": reason,
        }
        event.update(extra)
        self._events.append(event)
        self._append_jsonl(self._events_path, event)
        return event

    def _load_persistent_records(self) -> None:
        history = self._read_jsonl_tail(self._history_path, self._history.maxlen or 7200)
        events = self._read_jsonl_tail(self._events_path, self._events.maxlen or 1000)
        self._history.extend(history)
        self._events.extend(events)
        for event in events:
            try:
                self._event_seq = max(self._event_seq, int(event.get("seq", 0)))
            except (TypeError, ValueError):
                continue
        if history:
            latest = history[-1]
            self._last_key = latest.get("last_key", self._last_key)

    def _read_jsonl_tail(self, path: Path | None, limit: int) -> list[dict]:
        if path is None or not path.exists():
            return []
        records: deque[dict] = deque(maxlen=limit)
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        records.append(obj)
        except OSError as exc:
            self._log(f"failed to load persistence file {path}: {exc}")
        return list(records)

    def _append_jsonl(self, path: Path | None, record: dict) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
                fh.write("\n")
        except OSError as exc:
            self._log(f"failed to append persistence file {path}: {exc}")

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except Exception:
                pass


def build_controller(
    config: SmartBirdThermalConfig,
    logger=None,
    history_path: str | os.PathLike | None = None,
    events_path: str | os.PathLike | None = None,
) -> SmartBirdThermostat | None:
    if not config.enabled:
        return None
    switch = SmartBirdTcpSwitch(config.smartbird_host, config.smartbird_port, logger=logger)
    return SmartBirdThermostat(
        config,
        switch,
        logger=logger,
        history_path=history_path,
        events_path=events_path,
    )


def config_from_args(args) -> SmartBirdThermalConfig:
    env_config = SmartBirdThermalConfig.from_env()
    enabled = bool(getattr(args, "thermal_control", False) or env_config.enabled)

    def pick(name: str, default):
        value = getattr(args, name, None)
        if value is None or value == "":
            return default
        return value

    return SmartBirdThermalConfig(
        enabled=enabled,
        smartbird_host=pick("thermal_smartbird_host", env_config.smartbird_host),
        smartbird_port=pick("thermal_smartbird_port", env_config.smartbird_port),
        adb_serial=pick("thermal_adb_serial", env_config.adb_serial),
        loop_interval_sec=pick("thermal_loop_sec", env_config.loop_interval_sec),
        min_on_sec=pick("thermal_min_on_sec", env_config.min_on_sec),
        min_off_sec=pick("thermal_min_off_sec", env_config.min_off_sec),
        plum_rain_margin_c=pick("thermal_margin_c", env_config.plum_rain_margin_c),
        condensation_guard_c=env_config.condensation_guard_c,
        protection_min_surface_c=pick(
            "thermal_min_surface_c",
            env_config.protection_min_surface_c,
        ),
        protection_on_surface_c=pick(
            "thermal_on_surface_c",
            env_config.protection_on_surface_c,
        ),
        protection_hysteresis_c=pick(
            "thermal_hysteresis_c",
            env_config.protection_hysteresis_c,
        ),
        default_weather_temperature_c=pick(
            "thermal_default_ambient_c",
            env_config.default_weather_temperature_c,
        ),
        default_weather_humidity_pct=pick(
            "thermal_default_rh",
            env_config.default_weather_humidity_pct,
        ),
        amap_key=pick("thermal_amap_key", env_config.amap_key),
        amap_city=pick("thermal_amap_city", env_config.amap_city),
        amap_timeout_sec=env_config.amap_timeout_sec,
        weather_refresh_sec=env_config.weather_refresh_sec,
    )
