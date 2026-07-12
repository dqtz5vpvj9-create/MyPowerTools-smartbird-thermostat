#!/usr/bin/env python3
"""Task Scheduler entrypoint driven by the MyPowerTools per-user settings file."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys


if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from test_tools.smartbird_thermostat_service import main  # noqa: E402


CREDENTIAL_TYPE_GENERIC = 1


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_credential(name: str) -> str:
    if os.name != "nt":
        return ""
    advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    credential = ctypes.POINTER(CREDENTIALW)()
    target = f"MyPowerTools/smartbird-thermostat/{name}"
    if not advapi32.CredReadW(target, CREDENTIAL_TYPE_GENERIC, 0, ctypes.byref(credential)):
        return ""
    try:
        value = credential.contents
        if not value.CredentialBlob or value.CredentialBlobSize == 0:
            return ""
        raw = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
        return raw.decode("utf-8")
    finally:
        advapi32.CredFree(credential)


def load_settings(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("SmartBird settings must be a JSON object")
    return value


def setting(settings: dict, name: str, default):
    value = settings.get(name, default)
    return default if value is None or value == "" else value


def task_args(settings: dict, data_root: Path, log_file: Path) -> list[str]:
    notification_file = data_root / "notification_config.json"
    return [
        "--host", str(setting(settings, "serviceHost", "127.0.0.1")),
        "--port", str(setting(settings, "servicePort", 19002)),
        "--smartbird-host", str(setting(settings, "smartBirdHost", "0.0.0.0")),
        "--smartbird-port", str(setting(settings, "smartBirdPort", 19001)),
        "--adb-serial", str(setting(settings, "adbSerials", "")),
        "--loop-sec", str(setting(settings, "loopSec", 10)),
        "--min-on-sec", str(setting(settings, "minOnSec", 60)),
        "--min-off-sec", str(setting(settings, "minOffSec", 60)),
        "--margin-c", str(setting(settings, "marginC", 5)),
        "--min-surface-c", str(setting(settings, "minSurfaceC", 30)),
        "--on-surface-c", str(setting(settings, "onSurfaceC", 35)),
        "--hysteresis-c", str(setting(settings, "hysteresisC", 4)),
        "--default-ambient-c", str(setting(settings, "defaultAmbientC", 28)),
        "--default-rh", str(setting(settings, "defaultRh", 95)),
        "--amap-city", str(setting(settings, "amapCity", "310112")),
        "--amap-timeout-sec", str(setting(settings, "amapTimeoutSec", 3)),
        "--weather-refresh-sec", str(setting(settings, "weatherRefreshSec", 300)),
        "--energy-server-url", str(setting(settings, "energyServerUrl", "http://127.0.0.1:18988")),
        "--notification-config-file", str(notification_file),
        "--log-file", str(log_file),
        "--data-dir", str(data_root / "data"),
    ]


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings-file", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--log-file", default="")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings_file = Path(args.settings_file).resolve()
    data_root = Path(args.data_root).resolve()
    settings = load_settings(settings_file)
    data_root.mkdir(parents=True, exist_ok=True)
    log_file = Path(args.log_file).resolve() if args.log_file else data_root / "logs" / "smartbird_thermostat_service.log"

    amap_key = read_credential("amap-key")
    smtp_password = read_credential("smtp-password")
    if amap_key:
        os.environ["AMAP_API_KEY"] = amap_key
    if smtp_password:
        os.environ["SMARTBIRD_NOTIFY_SMTP_PASSWORD"] = smtp_password
    return main(task_args(settings, data_root, log_file))


if __name__ == "__main__":
    raise SystemExit(run())
