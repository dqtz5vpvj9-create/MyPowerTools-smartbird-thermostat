#!/usr/bin/env python3
"""Short Task Scheduler entrypoint for the energy server."""

from __future__ import annotations

from pathlib import Path
import sys


if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "energy_server.log"
    stream = log_file.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    sys.argv = [
        "energy_server.py",
        "--port",
        "18988",
        "--backend",
        "hid",
        "--thermal-control",
        "--thermal-service-url",
        "http://127.0.0.1:19002",
    ]
    from test_tools import energy_server  # noqa: F401
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
