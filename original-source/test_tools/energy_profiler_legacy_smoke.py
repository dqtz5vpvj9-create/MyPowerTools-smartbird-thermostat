import argparse
import json
import math
import queue
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from py_modules.lib_energy_profiler import EnergyProfiler
from py_modules.simple_http_notification_sender import SimpleHttpNotificationSender


AGENT_BINDINGS = {
    "agent1": "FNB-58-88872",
    "agent2": "FNB-58-86360",
    "agent3": "FNB-58-69343",
    "agent4": "FNB-58-69717",
}
EXPECTED_DEVICES = list(AGENT_BINDINGS.values())
DELTA_EPSILON_WH = 1e-9


class SmokeLogger:
    def __init__(self, name):
        self.name = name

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

    def notice(self, msg):
        pass


def request_json(base_url, method, path, payload=None, expected_status=200, timeout=10):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        status = exc.code
    if status != expected_status:
        raise RuntimeError(f"{method} {path} expected {expected_status}, got {status}: {body}")
    return json.loads(body) if body else {}


def build_sender(base_url, logger):
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError(f"base_url must include scheme, host, and port: {base_url}")
    return SimpleHttpNotificationSender(parsed.scheme, parsed.hostname, parsed.port, logger)


def write_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def validate_health(backend, device_name):
    health = backend.get("device_health") or {}
    item = health.get(device_name)
    if not item:
        raise RuntimeError(f"missing health for {device_name}: {backend}")
    if not item.get("connected"):
        raise RuntimeError(f"{device_name} disconnected: {item}")
    for key in ("read_errors", "write_errors", "crc_errors"):
        if int(item.get(key, 0) or 0) != 0:
            raise RuntimeError(f"{device_name} {key}={item.get(key)}: {item}")
    if item.get("last_error") is not None:
        raise RuntimeError(f"{device_name} last_error={item.get('last_error')}: {item}")
    return item


def validate_server_state(base_url):
    payload = request_json(base_url, "GET", "/devices")
    if set(payload["devices"]) != set(EXPECTED_DEVICES):
        raise RuntimeError(f"device list changed: {payload['devices']}")
    backend = payload["backend"]
    if backend.get("backend_name") != "hid":
        raise RuntimeError(f"expected hid backend: {backend}")
    if backend.get("session_operations_mode") != "readonly_delta":
        raise RuntimeError(f"expected readonly_delta session mode: {backend}")
    for device in EXPECTED_DEVICES:
        validate_health(backend, device)
    return payload


def require_response_ok(label, response, initial_server_session, initial_device_session, device_name):
    if response.get("message") != "OK":
        raise RuntimeError(f"{label} failed: {response}")
    if response.get("server_session") != initial_server_session:
        raise RuntimeError(f"{label} server_session changed: {response}")
    if response.get("device_session") != initial_device_session:
        raise RuntimeError(f"{label} device_session changed: {response}")
    validate_health(response["backend"], device_name)


def validate_task_record(record):
    for key in (
        "agent",
        "device",
        "task_id",
        "start_response_session",
        "stop_response_session",
        "read_response_session",
        "energy_wh",
        "server_session",
        "device_session",
    ):
        if key not in record:
            raise RuntimeError(f"record missing {key}: {record}")
    energy_wh = float(record["energy_wh"])
    if math.isnan(energy_wh):
        raise RuntimeError(f"energy_wh is nan: {record}")
    if energy_wh < -DELTA_EPSILON_WH:
        raise RuntimeError(f"energy_wh is negative: {record}")
    begin = float(record["begin_nrg_wh"])
    end = float(record["end_nrg_wh"])
    if abs((end - begin) - energy_wh) > 5e-9:
        raise RuntimeError(f"delta mismatch: {record}")
    if len({
        record["start_response_session"],
        record["stop_response_session"],
        record["read_response_session"],
    }) != 1:
        raise RuntimeError(f"logical session drift: {record}")


def run_agent(agent, device_name, args, initial_server_session, initial_device_session, records_path):
    logger = SmokeLogger(agent)
    profiler = EnergyProfiler(logger, device_name=device_name)
    profiler.http_sender_ = build_sender(args.base_url, logger)

    for index in range(args.task_count):
        duration = args.task_duration_sec
        if args.long_task_sec and index == args.task_count - 1:
            duration = args.long_task_sec
        task_id = f"{agent}-legacy-{index + 1:03d}"
        task_started = time.time()
        session_id = profiler.start()
        if session_id == "[invalid_session_id_1]":
            raise RuntimeError(f"{task_id} start failed")
        create_resp = profiler.last_create_response
        start_resp = profiler.last_start_response
        require_response_ok("create", create_resp, initial_server_session, initial_device_session, device_name)
        require_response_ok("start", start_resp, initial_server_session, initial_device_session, device_name)
        if create_resp["session_id"] != start_resp["session_id"]:
            raise RuntimeError(f"{task_id} create/start session mismatch: {create_resp} {start_resp}")

        time.sleep(duration)
        energy_wh, finish_session = profiler.finish()
        if finish_session == "[invalid_session_id_2]":
            raise RuntimeError(f"{task_id} finish failed")
        stop_resp = profiler.last_stop_response
        read_resp = profiler.last_read_response
        require_response_ok("stop", stop_resp, initial_server_session, initial_device_session, device_name)
        if read_resp.get("server_session") != initial_server_session:
            raise RuntimeError(f"{task_id} read server_session changed: {read_resp}")
        if read_resp.get("device_session") != initial_device_session:
            raise RuntimeError(f"{task_id} read device_session changed: {read_resp}")
        validate_health(read_resp["backend"], device_name)
        if "energy_wh" not in read_resp:
            raise RuntimeError(f"{task_id} read_nrg missing energy_wh: {read_resp}")
        if abs(float(read_resp["energy_wh"]) - energy_wh) > 5e-12:
            raise RuntimeError(f"{task_id} EnergyProfiler did not use energy_wh: {read_resp}")
        if abs(float(read_resp["end_nrg_wh"]) - float(read_resp["begin_nrg_wh"]) - energy_wh) > 5e-9:
            raise RuntimeError(f"{task_id} read_nrg is not end-begin: {read_resp}")

        record = {
            "agent": agent,
            "device": device_name,
            "task_id": task_id,
            "task_duration_sec": duration,
            "t_start": task_started,
            "t_end": time.time(),
            "start_response_session": start_resp["session_id"],
            "stop_response_session": stop_resp["session_id"],
            "read_response_session": read_resp["session_id"],
            "energy_wh": energy_wh,
            "message_energy_wh": float(str(read_resp["message"]).replace("Wh", "").strip()),
            "begin_nrg_wh": float(read_resp["begin_nrg_wh"]),
            "end_nrg_wh": float(read_resp["end_nrg_wh"]),
            "server_session": read_resp["server_session"],
            "device_session": read_resp["device_session"],
            "health": read_resp["backend"]["device_health"][device_name],
        }
        validate_task_record(record)
        write_jsonl(records_path, record)


def run_supervisor(args):
    records_dir = Path(
        args.records_dir
        or tempfile.mkdtemp(prefix="energy_profiler_legacy_smoke_", dir=str(Path.cwd() / "build"))
    )
    records_dir.mkdir(parents=True, exist_ok=True)

    initial = validate_server_state(args.base_url)
    initial_server_session = initial["backend"].get("server_session") or request_json(
        args.base_url,
        "GET",
        "/handshake",
    )["session_id"]
    initial_device_sessions = initial["device_sessions"]
    errors = queue.Queue()
    threads = []

    def worker(agent, device_name):
        try:
            run_agent(
                agent,
                device_name,
                args,
                initial_server_session,
                initial_device_sessions[device_name],
                records_dir / f"{agent}.jsonl",
            )
        except Exception as exc:
            errors.put((agent, repr(exc)))

    for agent, device_name in AGENT_BINDINGS.items():
        thread = threading.Thread(target=worker, args=(agent, device_name), name=agent)
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()

    if not errors.empty():
        raise RuntimeError(f"legacy EnergyProfiler smoke failed: {list(errors.queue)}")

    final = validate_server_state(args.base_url)
    if final["device_sessions"] != initial_device_sessions:
        raise RuntimeError(f"device_sessions changed: {final['device_sessions']}")
    records = []
    for path in sorted(records_dir.glob("*.jsonl")):
        with open(path, "r", encoding="utf-8") as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    if len(records) != len(AGENT_BINDINGS) * args.task_count:
        raise RuntimeError(f"unexpected record count {len(records)}")
    for record in records:
        validate_task_record(record)

    summary = {
        "status": "ok",
        "base_url": args.base_url,
        "records_dir": str(records_dir),
        "server_session": request_json(args.base_url, "GET", "/handshake")["session_id"],
        "devices": EXPECTED_DEVICES,
        "task_count": len(records),
        "nan_count": sum(1 for item in records if math.isnan(float(item["energy_wh"]))),
        "min_energy_wh": min(float(item["energy_wh"]) for item in records),
        "max_energy_wh": max(float(item["energy_wh"]) for item in records),
        "scenarios": [
            "4 agents use EnergyProfiler create/start/stop/read_nrg",
            "read_nrg includes high precision energy_wh",
            "energy_wh equals end_nrg_wh - begin_nrg_wh",
            "server_session and device_session remain stable",
            "benchmark-style records contain no nan",
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18999")
    parser.add_argument("--records-dir")
    parser.add_argument("--task-count", type=int, default=4)
    parser.add_argument("--task-duration-sec", type=float, default=2.0)
    parser.add_argument("--long-task-sec", type=float, default=8.0)
    return parser


if __name__ == "__main__":
    run_supervisor(build_parser().parse_args())
