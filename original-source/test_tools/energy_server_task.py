#!/usr/bin/env python3
"""Task Scheduler entrypoint for the Energy Server using MyPowerTools settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse


if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings-file", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--log-file", default="")
    return parser.parse_args(argv)


def load_settings(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("SmartBird settings must be a JSON object")
    return value


def build_server_args(settings: dict) -> list[str]:
    endpoint = urlparse(str(settings.get("energyServerUrl") or "http://127.0.0.1:18988"))
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    backend = str(settings.get("energyBackend") or "hid")
    selector_mode = str(settings.get("usbMeterSelectorMode") or "auto")
    selectors = str(settings.get("usbMeterSelector") or "").strip()
    arguments = [
        "energy_server.py",
        "--port", str(port),
        "--backend", backend,
        "--thermal-control",
        "--thermal-service-url",
        f"http://127.0.0.1:{int(settings.get('servicePort') or 19002)}",
    ]
    if bool(settings.get("energyAllowUnsafeControl")):
        arguments.extend(["--safety-mode", "full_compat_research", "--allow-unsafe-control"])
    else:
        arguments.extend(["--safety-mode", "safe_readonly"])

    if backend == "hid":
        if selector_mode == "serial" and selectors:
            for selector in split_selectors(selectors):
                arguments.extend(["--hid-serial", selector])
        elif selector_mode == "path" and selectors:
            for selector in split_selectors(selectors):
                arguments.extend(["--hid-path", selector])
        elif selector_mode == "auto":
            arguments.append("--hid-discovery-only")
    elif selector_mode == "title" and selectors:
        arguments.extend(["--devices", ",".join(split_selectors(selectors))])
    return arguments


def split_selectors(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(Path(args.settings_file).resolve())
    data_root = Path(args.data_root).resolve()
    log_file = Path(args.log_file).resolve() if args.log_file else data_root / "logs" / "energy_server.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stream = log_file.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    sys.argv = build_server_args(settings)
    from test_tools import energy_server  # noqa: F401
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
