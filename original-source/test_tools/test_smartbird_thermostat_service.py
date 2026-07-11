import http.client
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest

from test_tools.smartbird_thermostat import MODE_EXPERIMENT, MODE_PROTECTION
from test_tools.smartbird_thermostat_service import (
    EmailNotificationConfig,
    ThermostatSessionService,
    ThreadingHTTPServer,
    make_handler,
)


class FakeThermostat:
    def __init__(self):
        self.mode = MODE_PROTECTION
        self.started = False
        self.stopped = False
        self.calls = []
        self.client_count = 1
        self.thermal_reader = None
        self._events = [
            {
                "seq": 1,
                "timestamp": 100.0,
                "type": "mode",
                "message": "mode=dewpoint_protection",
                "key": None,
                "reason": "test",
            }
        ]

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def enter_experiment(self, reason=""):
        self.mode = MODE_EXPERIMENT
        self.calls.append(("experiment", reason))
        return self.snapshot()

    def enter_protection(self, reason=""):
        self.mode = MODE_PROTECTION
        self.calls.append(("protection", reason))
        return self.snapshot()

    def evaluate_once(self, reason=""):
        self.calls.append(("evaluate", reason))
        return None

    def manual_switch(self, key, reason=""):
        self.calls.append(("manual_switch", key, reason))
        self.record_event("manual_switch", "key_changed", key=key, reason=reason)
        return self.snapshot()

    def snapshot(self, decision=None):
        return {
            "enabled": True,
            "mode": self.mode,
            "last_key": self.calls[-1][1] if self.calls and self.calls[-1][0] == "manual_switch" else 0,
            "last_decision": decision,
            "history_points": len(self.history()),
            "event_count": len(self.events()),
            "switch": {"client_count": self.client_count},
            "config": {"adb_serial": "adb-a,adb-b"},
        }

    def history(self, limit=720, since=None):
        records = [
            {
                "timestamp": 100.0,
                "mode": self.mode,
                "last_key": 0,
                "surface_c": 31.0,
                "dew_point_c": 24.0,
                "off_threshold_c": 30.0,
                "on_threshold_c": 35.0,
            }
        ]
        if since is not None:
            records = [item for item in records if item["timestamp"] >= since]
        return records[-limit:]

    def events(self, limit=200):
        return self._events[-limit:]

    def record_event(self, event_type, message, key=None, reason="", **extra):
        event = {
            "seq": len(self._events) + 1,
            "timestamp": 100.0 + len(self._events),
            "type": event_type,
            "message": message,
            "key": key,
            "reason": reason,
        }
        event.update(extra)
        self._events.append(event)
        return event


class TestSmartBirdThermostatService(unittest.TestCase):
    def setUp(self):
        self.fake = FakeThermostat()
        logger = logging.getLogger("test_smartbird_thermostat_service")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.service = ThermostatSessionService(
            self.fake,
            logger=logger,
            clock=lambda: 100.0,
            energy_server_url="",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request_json(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def request_text(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request(method, path)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        conn.close()
        return resp.status, text

    def test_status_returns_active_sessions(self):
        status, data = self.request_json("GET", "/status")

        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["mode"], MODE_PROTECTION)
        self.assertEqual(data["active_session_ids"], [])

    def test_dashboard_html_is_served(self):
        status, text = self.request_text("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn("Smart-Bird Thermostat", text)
        self.assertIn("tempChart", text)
        self.assertIn("Energy Server", text)
        self.assertIn("energyBackend", text)
        self.assertIn("energyDeviceChart", text)
        self.assertIn("energyHistoryRows", text)
        self.assertIn("readMeterButton", text)
        self.assertIn("manualSwitchToggle", text)

    def test_history_and_event_apis(self):
        status, data = self.request_json("GET", "/api/history?limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["surface_c"], 31.0)

        status, data = self.request_json("GET", "/api/history?limit=1&since=101")

        self.assertEqual(status, 200)
        self.assertEqual(data["history"], [])

        status, data = self.request_json("GET", "/api/events?limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["type"], "mode")

    def test_energy_status_api_can_be_disabled(self):
        status, data = self.request_json("GET", "/api/energy/status")

        self.assertEqual(status, 200)
        self.assertFalse(data["enabled"])
        self.assertFalse(data["online"])
        self.assertEqual(data["url"], "")

    def test_energy_history_api_records_device_snapshots(self):
        self.service.energy_server_url = "http://energy.local"
        devices = ["FNB-58-1"]

        def fake_energy_get_json(path):
            if path == "/handshake":
                return {
                    "status": "online",
                    "session_id": "sess-1",
                    "devices": list(devices),
                }
            if path == "/backend_status":
                return {
                    "backend_name": "hid",
                    "session_operations_mode": "readonly_delta",
                    "hid_waiting": False,
                    "hid_discovery_only": False,
                    "hid_candidates": [{"path": "hid-a"}],
                }
            if path == "/thermal_control/status":
                return {"enabled": True, "energy_server_active_session_ids": ["sess-1"]}
            return {}

        self.service._energy_get_json = fake_energy_get_json

        status, data = self.request_json("GET", "/api/energy/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["online"])

        status, data = self.request_json("GET", "/api/energy/history?limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(len(data["history"]), 1)
        sample = data["history"][0]
        self.assertTrue(sample["online"])
        self.assertEqual(sample["devices"], ["FNB-58-1"])
        self.assertEqual(sample["device_count"], 1)
        self.assertEqual(sample["hid_candidate_count"], 1)
        self.assertEqual(sample["session_id"], "sess-1")

        status, data = self.request_json("GET", "/api/energy/status")
        self.assertEqual(status, 200)
        status, data = self.request_json("GET", "/api/energy/history?limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["history"]), 1)

        devices.append("FNB-58-2")
        status, data = self.request_json("GET", "/api/energy/status")
        self.assertEqual(status, 200)
        status, data = self.request_json("GET", "/api/energy/history?limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][-1]["devices"], ["FNB-58-1", "FNB-58-2"])

    def test_energy_history_preserves_last_known_devices_on_partial_status(self):
        self.service.energy_server_url = "http://energy.local"
        handshake_timeout = False

        def fake_energy_get_json(path):
            if path == "/handshake":
                if handshake_timeout:
                    raise TimeoutError("timed out")
                return {
                    "status": "online",
                    "session_id": "sess-1",
                    "devices": ["FNB-58-1"],
                }
            if path == "/backend_status":
                return {
                    "backend_name": "hid",
                    "session_operations_mode": "readonly_delta",
                    "hid_waiting": False,
                    "hid_discovery_only": False,
                    "hid_candidates": [{"path": "hid-a"}],
                }
            if path == "/thermal_control/status":
                return {"enabled": True}
            return {}

        self.service._energy_get_json = fake_energy_get_json

        status, data = self.request_json("GET", "/api/energy/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["snapshot"]["state_uncertain"])

        handshake_timeout = True
        status, data = self.request_json("GET", "/api/energy/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["online"])
        self.assertTrue(data["snapshot"]["state_uncertain"])
        self.assertEqual(data["snapshot"]["devices"], ["FNB-58-1"])
        self.assertIn("handshake", data["snapshot"]["error"])

        status, data = self.request_json("GET", "/api/energy/history?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(data["history"][-1]["device_count"], 1)
        self.assertTrue(data["history"][-1]["state_uncertain"])

    def test_energy_history_coalesces_old_unknown_device_rows(self):
        self.service._energy_history = [
            {
                "timestamp": 1.0,
                "online": True,
                "backend_name": "hid",
                "session_operations_mode": "readonly_delta",
                "hid_waiting": False,
                "hid_discovery_only": False,
                "hid_candidate_count": 1,
                "devices": ["FNB-58-1"],
                "device_count": 1,
                "session_id": "sess-1",
                "active_session_ids": [],
                "error": "",
            },
            {
                "timestamp": 2.0,
                "online": True,
                "backend_name": "hid",
                "session_operations_mode": "readonly_delta",
                "hid_waiting": False,
                "hid_discovery_only": False,
                "hid_candidate_count": 1,
                "devices": [],
                "device_count": 0,
                "session_id": None,
                "active_session_ids": [],
                "error": "",
            },
            {
                "timestamp": 3.0,
                "online": False,
                "backend_name": None,
                "session_operations_mode": None,
                "hid_waiting": False,
                "hid_discovery_only": False,
                "hid_candidate_count": 0,
                "devices": [],
                "device_count": 0,
                "session_id": None,
                "active_session_ids": [],
                "error": "handshake: timed out | backend: timed out",
            },
        ]

        status, data = self.request_json("GET", "/api/energy/history?limit=3")

        self.assertEqual(status, 200)
        self.assertEqual(data["history"][1]["devices"], ["FNB-58-1"])
        self.assertEqual(data["history"][1]["device_count"], 1)
        self.assertTrue(data["history"][1]["state_uncertain"])
        self.assertEqual(data["history"][2]["devices"], ["FNB-58-1"])
        self.assertEqual(data["history"][2]["device_count"], 1)
        self.assertTrue(data["history"][2]["state_uncertain"])

    def test_energy_history_persists_to_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "energy_history.jsonl"
            logger = logging.getLogger("test_energy_history_persists")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            service = ThermostatSessionService(
                FakeThermostat(),
                logger=logger,
                clock=lambda: 123.0,
                energy_server_url="http://energy.local",
                energy_history_path=path,
            )
            service._energy_get_json = lambda request_path: {
                "/handshake": {"session_id": "sess-2", "devices": []},
                "/backend_status": {
                    "backend_name": "hid",
                    "hid_waiting": True,
                    "hid_candidates": [],
                },
                "/thermal_control/status": {"enabled": True},
            }[request_path]

            service.energy_status()
            service.energy_status()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            reloaded = ThermostatSessionService(
                FakeThermostat(),
                logger=logger,
                clock=lambda: 124.0,
                energy_server_url="",
                energy_history_path=path,
            )

            history = reloaded.energy_history()["history"]
            self.assertEqual(len(history), 1)
            self.assertTrue(history[0]["hid_waiting"])
            self.assertEqual(history[0]["session_id"], "sess-2")

    def test_energy_read_api_returns_current_meter_values(self):
        self.service.energy_server_url = "http://energy.local"
        self.service._energy_get_json = lambda path: {
            "devices": ["FNB-58-1"],
            "values": {
                "FNB-58-1": {
                    "vbus": "5.10000",
                    "ibus": "0.20000",
                    "nrg": "0.1234 Wh",
                }
            },
        }

        status, data = self.request_json("POST", "/api/energy/read", {})

        self.assertEqual(status, 200)
        self.assertTrue(data["online"])
        self.assertEqual(data["reading"]["devices"], ["FNB-58-1"])
        self.assertEqual(data["reading"]["values"]["FNB-58-1"]["nrg"], "0.1234 Wh")

    def test_manual_switch_api_forces_key_and_records_event(self):
        status, data = self.request_json(
            "POST",
            "/api/switch",
            {"key": 1, "reason": "operator_test"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(data["last_key"], 1)
        self.assertIn(("manual_switch", 1, "operator_test"), self.fake.calls)
        events = self.fake.events()
        self.assertEqual(events[-1]["type"], "manual_switch")
        self.assertEqual(events[-1]["key"], 1)

    def test_notification_monitor_reports_configured_anomalies(self):
        sent = []
        cfg = EmailNotificationConfig(
            enabled=True,
            username="sender@example.com",
            password="secret",
            sender="sender@example.com",
            recipients=["operator@example.com"],
            cooldown_sec=60,
        )
        service = ThermostatSessionService(
            self.fake,
            logger=logging.getLogger("test_notification_monitor"),
            clock=lambda: 1000.0,
            energy_server_url="http://energy.local",
            notification_config=cfg,
            email_sender=lambda subject, body: sent.append((subject, body)),
        )
        devices = ["FNB-A", "FNB-B"]

        def fake_energy_get_json(path):
            if path == "/handshake":
                return {"session_id": "sess", "devices": list(devices)}
            if path == "/backend_status":
                return {
                    "backend_name": "hid",
                    "hid_waiting": False,
                    "hid_candidates": [{"path": "a"}, {"path": "b"}],
                }
            if path == "/thermal_control/status":
                return {"enabled": True}
            return {}

        service._energy_get_json = fake_energy_get_json
        service._check_notifications_once()
        self.assertEqual(sent, [])

        class BrokenReader:
            def current(self):
                raise RuntimeError("all adb failed")

        self.fake.thermal_reader = BrokenReader()
        self.fake.client_count = 0
        devices.pop()

        service._check_notifications_once()

        self.assertEqual(len(sent), 3)
        subjects = " ".join(subject for subject, _body in sent)
        self.assertIn("功耗计数量减少", subjects)
        self.assertIn("ADB 全部不可用", subjects)
        self.assertIn("Smart-Bird TCP 设备断开", subjects)

    def test_notification_monitor_ignores_uncertain_energy_device_count(self):
        sent = []
        cfg = EmailNotificationConfig(
            enabled=True,
            username="sender@example.com",
            password="secret",
            sender="sender@example.com",
            recipients=["operator@example.com"],
            cooldown_sec=60,
        )
        service = ThermostatSessionService(
            self.fake,
            logger=logging.getLogger("test_notification_uncertain_device_count"),
            clock=lambda: 1000.0,
            energy_server_url="http://energy.local",
            notification_config=cfg,
            email_sender=lambda subject, body: sent.append((subject, body)),
        )
        handshake_timeout = False

        def fake_energy_get_json(path):
            if path == "/handshake":
                if handshake_timeout:
                    raise TimeoutError("timed out")
                return {"session_id": "sess", "devices": ["FNB-A"]}
            if path == "/backend_status":
                return {
                    "backend_name": "hid",
                    "hid_waiting": False,
                    "hid_candidates": [{"path": "a"}],
                }
            if path == "/thermal_control/status":
                return {"enabled": True}
            return {}

        service._energy_get_json = fake_energy_get_json
        service._check_notifications_once()
        self.assertEqual(sent, [])

        handshake_timeout = True
        service._check_notifications_once()

        self.assertFalse(any("功耗计数量减少" in subject for subject, _body in sent))

    def test_notification_monitor_sends_same_anomaly_once_until_recovery(self):
        sent = []
        cfg = EmailNotificationConfig(
            enabled=True,
            username="sender@example.com",
            password="secret",
            sender="sender@example.com",
            recipients=["operator@example.com"],
            cooldown_sec=60,
        )
        service = ThermostatSessionService(
            self.fake,
            logger=logging.getLogger("test_notification_dedupe"),
            clock=lambda: 1000.0,
            energy_server_url="http://energy.local",
            notification_config=cfg,
            email_sender=lambda subject, body: sent.append((subject, body)),
        )
        offline = True

        def fake_energy_get_json(path):
            if offline:
                raise RuntimeError("offline")
            if path == "/handshake":
                return {"session_id": "sess", "devices": ["FNB-A"]}
            if path == "/backend_status":
                return {
                    "backend_name": "hid",
                    "hid_waiting": False,
                    "hid_candidates": [{"path": "a"}],
                }
            if path == "/thermal_control/status":
                return {"enabled": True}
            return {}

        service._energy_get_json = fake_energy_get_json

        service._check_notifications_once()
        service._check_notifications_once()
        service._check_notifications_once()

        self.assertEqual(len(sent), 1)
        self.assertIn("Energy Server 离线", sent[0][0])

        offline = False
        service._check_notifications_once()
        self.assertEqual(len(sent), 2)
        self.assertIn("recovered", sent[1][0])

        offline = True
        service._check_notifications_once()
        self.assertEqual(len(sent), 3)
        self.assertIn("Energy Server 离线", sent[2][0])

    def test_notification_monitor_reports_energy_server_offline(self):
        sent = []
        cfg = EmailNotificationConfig(
            enabled=True,
            username="sender@example.com",
            password="secret",
            sender="sender@example.com",
            recipients=["operator@example.com"],
        )
        service = ThermostatSessionService(
            self.fake,
            logger=logging.getLogger("test_notification_offline"),
            clock=lambda: 1000.0,
            energy_server_url="http://energy.local",
            notification_config=cfg,
            email_sender=lambda subject, body: sent.append((subject, body)),
        )
        service._energy_get_json = lambda path: (_ for _ in ()).throw(RuntimeError("offline"))

        service._check_notifications_once()

        self.assertTrue(any("Energy Server 离线" in subject for subject, _body in sent))

    def test_session_start_and_stop_lifecycle(self):
        status, data = self.request_json(
            "POST",
            "/session/start",
            {"session_ids": ["a"], "source": "energy_server"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], MODE_EXPERIMENT)
        self.assertEqual(data["active_session_ids"], ["a"])

        status, data = self.request_json(
            "POST",
            "/session/start",
            {"session_ids": ["b"], "source": "energy_server"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["active_session_ids"], ["a", "b"])

        status, data = self.request_json(
            "POST",
            "/session/stop",
            {"session_ids": ["a"], "source": "energy_server"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], MODE_EXPERIMENT)
        self.assertEqual(data["active_session_ids"], ["b"])

        status, data = self.request_json(
            "POST",
            "/session/stop",
            {"session_ids": ["b"], "source": "energy_server"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], MODE_PROTECTION)
        self.assertEqual(data["active_session_ids"], [])

    def test_manual_protection_clears_sessions(self):
        self.request_json("POST", "/session/start", {"session_id": "manual-a"})

        status, data = self.request_json(
            "POST",
            "/mode/protection",
            {"reason": "operator"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], MODE_PROTECTION)
        self.assertEqual(data["active_session_ids"], [])

    def test_session_events_are_recorded_for_ui(self):
        self.request_json(
            "POST",
            "/session/start",
            {
                "session_ids": ["energy-a"],
                "source": "energy_server",
                "energy_ctrl_msg": "start",
            },
        )
        self.request_json(
            "POST",
            "/session/stop",
            {
                "session_ids": ["energy-a"],
                "source": "energy_server",
                "energy_ctrl_msg": "stop",
            },
        )

        status, data = self.request_json("GET", "/api/events?limit=5")

        self.assertEqual(status, 200)
        session_events = [item for item in data["events"] if item["type"] == "energy_session"]
        self.assertEqual([item["action"] for item in session_events], ["start", "stop"])
        self.assertEqual(session_events[0]["source"], "energy_server")
        self.assertEqual(session_events[0]["session_ids"], ["energy-a"])
        self.assertEqual(session_events[-1]["active_session_ids"], [])


if __name__ == "__main__":
    unittest.main()
