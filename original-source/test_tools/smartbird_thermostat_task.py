#!/usr/bin/env python3
"""Short Task Scheduler entrypoint for the Smart-Bird thermostat service."""

from __future__ import annotations

from pathlib import Path
import sys


if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from test_tools.smartbird_thermostat_service import main  # noqa: E402


def task_args() -> list[str]:
    repo_root = Path(__file__).resolve().parents[1]
    log_file = repo_root / "logs" / "smartbird_thermostat_service.log"
    return [
        "--host",
        "127.0.0.1",
        "--port",
        "19002",
        "--smartbird-host",
        "0.0.0.0",
        "--smartbird-port",
        "19001",
        "--adb-serial",
        "10.33.0.243:5555,192.168.29.79:35559",
        "--loop-sec",
        "10",
        "--min-on-sec",
        "60",
        "--min-off-sec",
        "60",
        "--margin-c",
        "5",
        "--min-surface-c",
        "30",
        "--on-surface-c",
        "35",
        "--hysteresis-c",
        "4",
        "--default-ambient-c",
        "28",
        "--default-rh",
        "95",
        "--amap-city",
        "310112",
        "--energy-server-url",
        "http://127.0.0.1:18988",
        "--log-file",
        str(log_file),
    ]


if __name__ == "__main__":
    raise SystemExit(main(task_args()))
