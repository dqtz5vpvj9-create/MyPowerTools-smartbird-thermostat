"""
Unit tests for energy_server.py Flask routes.
使用 mock 替代 EnergyControlImpl，无需 GUI/硬件。

由于 energy_server.py 在模块级别执行 app.run() 和启动线程，
必须在导入之前 patch 所有关键依赖。
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import threading

from test_tools.energy_control_impl import (
    BACKEND_HID,
    BACKEND_UIA,
    SAFETY_MODE_SAFE_READONLY,
    UnsafeOperationBlockedError,
)


def _make_mock_impl(device_names=None):
    """构造 mock EnergyControlImpl。"""
    if device_names is None:
        device_names = ["FNIRSI_C1-6297"]
    impl = MagicMock()
    impl.device_names = device_names
    impl._default_device_name = device_names[0] if device_names else None
    impl._gui_lock = threading.RLock()
    impl.get_all_values.return_value = {
        name: {"vbus": "5.0", "ibus": "0.1", "nrg": "0.05"}
        for name in device_names
    }
    impl.get_last_value_item.return_value = "0.0500 Wh"
    impl.get_nrg_wh_value.side_effect = (
        lambda device_name=None: float(
            impl.get_last_value_item(device_name).replace("Wh", "").strip()
        )
    )
    impl.get_vbus_value.return_value = "5.0"
    impl.get_ibus_value.return_value = "0.1"
    impl.supports_control_operations.return_value = True
    impl.get_backend_status.return_value = {
        "backend_name": BACKEND_UIA,
        "safety_mode": SAFETY_MODE_SAFE_READONLY,
        "allow_unsafe_control": False,
        "control_operations_allowed": True,
        "capabilities": {
            "read_vbus": True,
            "read_ibus": True,
            "read_nrg": True,
            "list_devices": True,
            "create_session": True,
            "start_session": True,
            "pause_session": True,
            "stop_session": True,
        },
    }
    return impl


# ---- 在导入 energy_server 之前，注入所有必要的 mock ----

_original_win32gui = sys.modules.get('win32gui')
_original_win32con = sys.modules.get('win32con')
_original_pywinauto = sys.modules.get('pywinauto')
_original_pywinauto_mouse = sys.modules.get('pywinauto.mouse')
_original_psutil = sys.modules.get('psutil')
_original_py_modules = sys.modules.get('py_modules')
_original_logging_lib = sys.modules.get('py_modules.logging_lib')
_original_ecimpl_mod = sys.modules.get('test_tools.energy_control_impl')

# Mock Windows GUI 依赖
sys.modules['win32gui'] = MagicMock()
sys.modules['win32con'] = MagicMock()
sys.modules['pywinauto'] = MagicMock()
sys.modules['pywinauto.mouse'] = MagicMock()
sys.modules['psutil'] = MagicMock()

# Mock py_modules.logging_lib
_mock_logging_lib = MagicMock()
_mock_logging_lib.setup_logging = MagicMock(return_value=MagicMock())
sys.modules['py_modules'] = MagicMock()
sys.modules['py_modules.logging_lib'] = _mock_logging_lib

# Mock EnergyControlImpl class — 返回我们的 mock 实例
_mock_impl_class = MagicMock(side_effect=lambda **kwargs: _make_mock_impl(["DEV-A", "DEV-B"]))
_mock_ecimpl_mod = MagicMock()
_mock_ecimpl_mod.BACKEND_HID = BACKEND_HID
_mock_ecimpl_mod.BACKEND_UIA = BACKEND_UIA
_mock_ecimpl_mod.SAFETY_MODE_SAFE_READONLY = SAFETY_MODE_SAFE_READONLY
_mock_ecimpl_mod.SAFETY_MODE_FULL_COMPAT_RESEARCH = "full_compat_research"
_mock_ecimpl_mod.CONTROL_OPERATIONS = frozenset({"create", "start", "pause", "stop"})
_mock_ecimpl_mod.EnergyControlImpl = _mock_impl_class
_mock_ecimpl_mod.UnsafeOperationBlockedError = UnsafeOperationBlockedError
sys.modules['test_tools.energy_control_impl'] = _mock_ecimpl_mod

# Mock sys.argv 以通过 argparse
_original_argv = sys.argv
sys.argv = ['energy_server.py']

# 导入时 patch Flask.run 和 threading.Thread 使其不阻塞
energy_server = None
with patch('flask.Flask.run'):
    _orig_Thread = threading.Thread
    threading.Thread = MagicMock()
    try:
        from test_tools import energy_server
    except Exception as _exc:
        print(f"WARNING: Failed to import energy_server: {_exc}")
        import traceback
        traceback.print_exc()
    finally:
        threading.Thread = _orig_Thread
        sys.argv = _original_argv
        if _original_win32gui is not None:
            sys.modules['win32gui'] = _original_win32gui
        else:
            sys.modules.pop('win32gui', None)
        if _original_win32con is not None:
            sys.modules['win32con'] = _original_win32con
        else:
            sys.modules.pop('win32con', None)
        if _original_pywinauto is not None:
            sys.modules['pywinauto'] = _original_pywinauto
        else:
            sys.modules.pop('pywinauto', None)
        if _original_pywinauto_mouse is not None:
            sys.modules['pywinauto.mouse'] = _original_pywinauto_mouse
        else:
            sys.modules.pop('pywinauto.mouse', None)
        if _original_psutil is not None:
            sys.modules['psutil'] = _original_psutil
        else:
            sys.modules.pop('psutil', None)
        if _original_py_modules is not None:
            sys.modules['py_modules'] = _original_py_modules
        else:
            sys.modules.pop('py_modules', None)
        if _original_logging_lib is not None:
            sys.modules['py_modules.logging_lib'] = _original_logging_lib
        else:
            sys.modules.pop('py_modules.logging_lib', None)
        if _original_ecimpl_mod is not None:
            sys.modules['test_tools.energy_control_impl'] = _original_ecimpl_mod
        else:
            sys.modules.pop('test_tools.energy_control_impl', None)


class TestEnergyServerRoutes(unittest.TestCase):
    """Test Flask routes with mocked EnergyControlImpl."""

    @classmethod
    def setUpClass(cls):
        if energy_server is None:
            raise unittest.SkipTest("energy_server could not be imported")
        # Replace the module-level impl with a fresh mock
        cls.mock_impl = _make_mock_impl(["DEV-A", "DEV-B"])
        energy_server.energy_control_impl = cls.mock_impl
        energy_server.session_id = "test_session"
        energy_server.device_sessions = {
            "DEV-A": "session_a",
            "DEV-B": "session_b",
        }
        energy_server.app.config['TESTING'] = True
        cls.client = energy_server.app.test_client()

    def setUp(self):
        # Recreate mock each test to avoid stale side_effect leakage
        self.mock_impl = _make_mock_impl(["DEV-A", "DEV-B"])
        energy_server.args.backend = BACKEND_UIA
        energy_server.args.hid_discovery_only = False
        energy_server.session_id = "test_session"
        energy_server.device_sessions = {
            "DEV-A": "session_a",
            "DEV-B": "session_b",
        }
        energy_server.logical_energy_sessions = {}
        energy_server.thermal_service_notifier = None
        energy_server.thermal_active_session_ids = set()
        self.mock_impl.get_all_values.return_value = {
            "DEV-A": {"vbus": "5.0", "ibus": "0.1", "nrg": "0.05"},
            "DEV-B": {"vbus": "4.9", "ibus": "0.2", "nrg": "0.03"},
        }
        self.__class__.mock_impl = self.mock_impl
        energy_server.energy_control_impl = self.mock_impl

    def _enable_hid_readonly_sessions(self):
        energy_server.args.backend = BACKEND_HID
        self.mock_impl.supports_control_operations.return_value = False
        self.mock_impl.get_backend_status.return_value.update({
            "backend_name": BACKEND_HID,
            "control_operations_allowed": False,
            "device_health": {
                "DEV-A": {
                    "connected": True,
                    "read_errors": 0,
                    "write_errors": 0,
                    "crc_errors": 0,
                    "last_error": None,
                },
                "DEV-B": {
                    "connected": True,
                    "read_errors": 0,
                    "write_errors": 0,
                    "crc_errors": 0,
                    "last_error": None,
                },
            },
        })
        self.mock_impl.get_device_health.side_effect = (
            lambda name: self.mock_impl.get_backend_status.return_value["device_health"][name]
        )

    # ---- /handshake ----

    def test_handshake(self):
        resp = self.client.get('/handshake')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "online")
        self.assertIn("session_id", data)
        self.assertIn("devices", data)
        self.assertEqual(data["devices"], ["DEV-A", "DEV-B"])
        self.assertIn("device_sessions", data)
        self.assertIn("backend", data)
        self.assertEqual(data["backend"]["safety_mode"], SAFETY_MODE_SAFE_READONLY)

    # ---- /energy_control (single device) ----

    def test_energy_control_no_data(self):
        resp = self.client.post('/energy_control',
                                data='',
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_energy_control_missing_ctrl_msg(self):
        resp = self.client.post('/energy_control',
                                json={"device_name": "DEV-A"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("Missing", data["message"])

    def test_energy_control_create_default_device(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "create"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.click_create_button.assert_called_once_with(None)
        data = resp.get_json()
        self.assertEqual(data["message"], "OK")
        self.assertEqual(data["device_name"], "DEV-A")

    def test_energy_control_start_specific_device(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start", "device_name": "DEV-B"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.click_start_button.assert_called_once_with("DEV-B")
        data = resp.get_json()
        self.assertEqual(data["device_name"], "DEV-B")
        self.assertEqual(data["session_id"], "session_b")

    def test_energy_control_pause(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "pause", "device_name": "DEV-A"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.click_pause_button.assert_called_once_with("DEV-A")

    def test_energy_control_stop(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "stop"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.click_stop_button.assert_called_once_with(None)

    def test_energy_control_read_nrg(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "read_nrg"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["message"], "0.0500")

    def test_energy_control_read_nrg_specific_device(self):
        self.mock_impl.get_last_value_item.return_value = "1.2345 Wh"
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "read_nrg", "device_name": "DEV-B"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.get_last_value_item.assert_called_once_with("DEV-B")
        data = resp.get_json()
        self.assertEqual(data["message"], "1.2345")

    def test_energy_control_read_vbus_specific_device(self):
        self.mock_impl.get_vbus_value.return_value = "5.12345"
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "read_vbus", "device_name": "DEV-B"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.get_vbus_value.assert_called_once_with("DEV-B")
        data = resp.get_json()
        self.assertEqual(data["message"], "5.12345")

    def test_energy_control_read_ibus_specific_device(self):
        self.mock_impl.get_ibus_value.return_value = "0.12345"
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "read_ibus", "device_name": "DEV-B"})
        self.assertEqual(resp.status_code, 200)
        self.mock_impl.get_ibus_value.assert_called_once_with("DEV-B")
        data = resp.get_json()
        self.assertEqual(data["message"], "0.12345")

    def test_energy_control_unknown_command(self):
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "invalid_cmd"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("Unknown command", data["message"])

    def test_energy_control_device_not_found(self):
        self.mock_impl.click_start_button.side_effect = KeyError("Device 'NO-EXIST' not found")
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start", "device_name": "NO-EXIST"})
        self.assertEqual(resp.status_code, 404)

    def test_energy_control_internal_error(self):
        self.mock_impl.click_start_button.side_effect = RuntimeError("GUI crashed")
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start"})
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertIn("GUI crashed", data["message"])

    # ---- /devices ----

    def test_list_devices(self):
        resp = self.client.get('/devices')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["devices"], ["DEV-A", "DEV-B"])
        self.assertIn("values", data)
        self.assertEqual(data["values"]["DEV-A"]["vbus"], "5.0")
        self.assertEqual(data["values"]["DEV-B"]["ibus"], "0.2")
        self.assertIn("device_sessions", data)
        self.assertIn("backend", data)

    def test_backend_status(self):
        resp = self.client.get('/backend_status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["backend_name"], BACKEND_UIA)
        self.assertEqual(data["safety_mode"], SAFETY_MODE_SAFE_READONLY)

    def test_thermal_control_status_disabled_by_default(self):
        resp = self.client.get('/thermal_control/status')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"enabled": False})

    def test_thermal_control_status_proxies_service(self):
        notifier = MagicMock()
        notifier.status.return_value = {"enabled": True, "mode": "dewpoint_protection"}
        energy_server.thermal_service_notifier = notifier

        resp = self.client.get('/thermal_control/status')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["mode"], "dewpoint_protection")
        self.assertEqual(data["energy_server_active_session_ids"], [])

    def test_energy_control_start_notifies_thermal_service(self):
        notifier = MagicMock()
        notifier.notify_session_event.return_value = {"mode": "experiment_unconditional"}
        energy_server.thermal_service_notifier = notifier

        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start", "device_name": "DEV-A"})

        self.assertEqual(resp.status_code, 200)
        notifier.notify_session_event.assert_called_once_with(
            ctrl_msg="start",
            session_ids=["session_a"],
            active_session_ids=["session_a"],
        )
        data = resp.get_json()
        self.assertEqual(data["thermal_control"]["mode"], "experiment_unconditional")
        self.assertEqual(data["thermal_control"]["energy_server_active_session_ids"], ["session_a"])

    def test_energy_control_stop_notifies_thermal_service_after_last_session(self):
        notifier = MagicMock()
        notifier.notify_session_event.side_effect = [
            {"mode": "experiment_unconditional"},
            {"mode": "dewpoint_protection"},
        ]
        energy_server.thermal_service_notifier = notifier

        self.client.post('/energy_control',
                         json={"energy_ctrl_msg": "start", "device_name": "DEV-A"})
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "stop", "device_name": "DEV-A"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(notifier.notify_session_event.call_count, 2)
        notifier.notify_session_event.assert_called_with(
            ctrl_msg="stop",
            session_ids=["session_a"],
            active_session_ids=[],
        )
        data = resp.get_json()
        self.assertEqual(data["thermal_control"]["mode"], "dewpoint_protection")
        self.assertEqual(data["thermal_control"]["energy_server_active_session_ids"], [])

    def test_hid_waiting_start_notifies_thermal_service_without_device(self):
        self.mock_impl = _make_mock_impl([])
        self.mock_impl.supports_control_operations.return_value = False
        self.mock_impl.get_backend_status.return_value.update({
            "backend_name": BACKEND_HID,
            "control_operations_allowed": False,
            "device_health": {},
        })
        energy_server.energy_control_impl = self.mock_impl
        energy_server.args.backend = BACKEND_HID
        energy_server.device_sessions = {}
        notifier = MagicMock()
        notifier.notify_session_event.return_value = {"mode": "experiment_unconditional"}
        energy_server.thermal_service_notifier = notifier

        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start"})

        self.assertEqual(resp.status_code, 200)
        notifier.notify_session_event.assert_called_once_with(
            ctrl_msg="start",
            session_ids=["test_session"],
            active_session_ids=["test_session"],
        )
        data = resp.get_json()
        self.assertEqual(data["message"], "OK")
        self.assertIsNone(data["device_name"])
        self.assertTrue(data["device_waiting"])
        self.assertEqual(data["thermal_control"]["energy_server_active_session_ids"], ["test_session"])

    # ---- /energy_control_all (batch) ----

    def test_energy_control_all_no_data(self):
        resp = self.client.post('/energy_control_all',
                                data='',
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_energy_control_all_missing_msg(self):
        resp = self.client.post('/energy_control_all', json={})
        self.assertEqual(resp.status_code, 400)

    def test_energy_control_all_start(self):
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "start"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("results", data)
        self.assertEqual(data["results"]["DEV-A"]["message"], "OK")
        self.assertEqual(data["results"]["DEV-B"]["message"], "OK")
        # Verify both devices got called
        calls = self.mock_impl.click_start_button.call_args_list
        called_devices = [c[0][0] for c in calls]
        self.assertIn("DEV-A", called_devices)
        self.assertIn("DEV-B", called_devices)

    def test_hid_waiting_energy_control_all_start_notifies_thermal_service(self):
        self.mock_impl = _make_mock_impl([])
        self.mock_impl.supports_control_operations.return_value = False
        self.mock_impl.get_backend_status.return_value.update({
            "backend_name": BACKEND_HID,
            "control_operations_allowed": False,
            "device_health": {},
        })
        energy_server.energy_control_impl = self.mock_impl
        energy_server.args.backend = BACKEND_HID
        energy_server.device_sessions = {}
        notifier = MagicMock()
        notifier.notify_session_event.return_value = {"mode": "experiment_unconditional"}
        energy_server.thermal_service_notifier = notifier

        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "start"})

        self.assertEqual(resp.status_code, 200)
        notifier.notify_session_event.assert_called_once_with(
            ctrl_msg="start",
            session_ids=["test_session"],
            active_session_ids=["test_session"],
        )
        data = resp.get_json()
        self.assertEqual(data["results"]["_server"]["message"], "OK")
        self.assertTrue(data["results"]["_server"]["device_waiting"])
        self.assertEqual(data["thermal_control"]["energy_server_active_session_ids"], ["test_session"])

    def test_energy_control_all_create(self):
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "create"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mock_impl.click_create_button.call_count, 2)

    def test_energy_control_all_pause(self):
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "pause"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mock_impl.click_pause_button.call_count, 2)

    def test_energy_control_all_stop(self):
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "stop"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mock_impl.click_stop_button.call_count, 2)

    def test_energy_control_all_read_nrg(self):
        self.mock_impl.get_last_value_item.side_effect = [
            "0.0500 Wh", "0.0300 Wh"
        ]
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "read_nrg"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["message"], "0.0500")
        self.assertEqual(data["results"]["DEV-B"]["message"], "0.0300")

    def test_energy_control_all_read_vbus(self):
        self.mock_impl.get_vbus_value.side_effect = ["5.10000", "5.20000"]
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "read_vbus"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["message"], "5.10000")
        self.assertEqual(data["results"]["DEV-B"]["message"], "5.20000")

    def test_energy_control_all_read_ibus(self):
        self.mock_impl.get_ibus_value.side_effect = ["0.10000", "0.20000"]
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "read_ibus"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["message"], "0.10000")
        self.assertEqual(data["results"]["DEV-B"]["message"], "0.20000")

    def test_energy_control_all_partial_failure(self):
        self.mock_impl.click_start_button.side_effect = [
            None,  # DEV-A succeeds
            RuntimeError("DEV-B disconnected"),  # DEV-B fails
        ]
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "start"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["message"], "OK")
        self.assertTrue(data["results"]["DEV-B"].get("error"))
        self.assertIn("disconnected", data["results"]["DEV-B"]["message"])

    def test_energy_control_all_includes_session_ids(self):
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "stop"})
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["session_id"], "session_a")
        self.assertEqual(data["results"]["DEV-B"]["session_id"], "session_b")

    def test_energy_control_control_command_blocked_by_safe_mode(self):
        self.mock_impl.supports_control_operations.return_value = False
        self.mock_impl.get_backend_status.return_value["control_operations_allowed"] = False
        self.mock_impl.click_start_button.side_effect = UnsafeOperationBlockedError("blocked")
        resp = self.client.post('/energy_control',
                                json={"energy_ctrl_msg": "start", "device_name": "DEV-A"})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("blocked", data["message"])
        self.assertIn("backend", data)

    def test_energy_control_all_control_command_blocked_by_safe_mode(self):
        self.mock_impl.supports_control_operations.return_value = False
        self.mock_impl.get_backend_status.return_value["control_operations_allowed"] = False
        self.mock_impl.click_stop_button.side_effect = UnsafeOperationBlockedError("blocked")
        resp = self.client.post('/energy_control_all',
                                json={"energy_ctrl_msg": "stop"})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["status_code"], 403)
        self.assertEqual(data["results"]["DEV-B"]["status_code"], 403)

    def test_hid_backend_status_exposes_readonly_delta_sessions(self):
        self._enable_hid_readonly_sessions()

        resp = self.client.get('/backend_status')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["backend_name"], BACKEND_HID)
        self.assertFalse(data["control_operations_allowed"])
        self.assertEqual(data["session_operations_mode"], "readonly_delta")
        self.assertTrue(data["logical_session_operations_allowed"])
        self.assertTrue(data["capabilities"]["create_session"])
        self.assertTrue(data["capabilities"]["start_session"])
        self.assertTrue(data["capabilities"]["stop_session"])

    def test_hid_readonly_logical_session_supports_legacy_start_stop_read(self):
        self._enable_hid_readonly_sessions()
        self.mock_impl.get_last_value_item.side_effect = [
            "0.1000 Wh",  # start begin
            "0.123456 Wh",  # stop end
        ]

        create_resp = self.client.post('/energy_control',
                                       json={"energy_ctrl_msg": "create", "device_name": "DEV-A"})
        start_resp = self.client.post('/energy_control',
                                      json={"energy_ctrl_msg": "start", "device_name": "DEV-A"})
        stop_resp = self.client.post('/energy_control',
                                     json={"energy_ctrl_msg": "stop", "device_name": "DEV-A"})
        read_resp = self.client.post('/energy_control',
                                     json={"energy_ctrl_msg": "read_nrg", "device_name": "DEV-A"})

        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual(stop_resp.status_code, 200)
        self.assertEqual(read_resp.status_code, 200)
        self.assertEqual(
            create_resp.get_json()["session_id"],
            start_resp.get_json()["session_id"],
        )
        self.mock_impl.click_create_button.assert_not_called()
        self.mock_impl.click_start_button.assert_not_called()
        self.mock_impl.click_stop_button.assert_not_called()
        read_data = read_resp.get_json()
        self.assertEqual(read_data["message"], "0.0235")
        self.assertAlmostEqual(read_data["energy_wh"], 0.023456)
        self.assertAlmostEqual(read_data["begin_nrg_wh"], 0.1)
        self.assertAlmostEqual(read_data["end_nrg_wh"], 0.123456)
        self.assertEqual(read_data["session_operations_mode"], "readonly_delta")
        self.assertEqual(read_data["server_session"], "test_session")
        self.assertEqual(read_data["device_session"], "session_a")

    def test_hid_readonly_logical_session_rejects_session_change(self):
        self._enable_hid_readonly_sessions()
        self.mock_impl.get_last_value_item.side_effect = [
            "0.1000 Wh",  # start begin
        ]

        start_resp = self.client.post('/energy_control',
                                      json={"energy_ctrl_msg": "start", "device_name": "DEV-A"})
        self.assertEqual(start_resp.status_code, 200)
        energy_server.device_sessions["DEV-A"] = "new_device_session"

        stop_resp = self.client.post('/energy_control',
                                     json={"energy_ctrl_msg": "stop", "device_name": "DEV-A"})

        self.assertEqual(stop_resp.status_code, 500)
        self.assertIn("device_session_changed", stop_resp.get_json()["message"])

    def test_hid_energy_control_all_logical_sessions(self):
        self._enable_hid_readonly_sessions()
        self.mock_impl.get_last_value_item.side_effect = [
            "0.1000 Wh",  # DEV-A start begin
            "0.2000 Wh",  # DEV-B start begin
            "0.1250 Wh",  # DEV-A stop end
            "0.2500 Wh",  # DEV-B stop end
        ]

        start_resp = self.client.post('/energy_control_all',
                                      json={"energy_ctrl_msg": "start"})
        stop_resp = self.client.post('/energy_control_all',
                                     json={"energy_ctrl_msg": "stop"})
        read_resp = self.client.post('/energy_control_all',
                                     json={"energy_ctrl_msg": "read_nrg"})

        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual(stop_resp.status_code, 200)
        self.assertEqual(read_resp.status_code, 200)
        data = read_resp.get_json()
        self.assertEqual(data["results"]["DEV-A"]["message"], "0.0250")
        self.assertEqual(data["results"]["DEV-B"]["message"], "0.0500")
        self.assertAlmostEqual(data["results"]["DEV-A"]["energy_wh"], 0.025)
        self.assertAlmostEqual(data["results"]["DEV-B"]["energy_wh"], 0.05)
        self.assertAlmostEqual(data["results"]["DEV-A"]["begin_nrg_wh"], 0.1)
        self.assertAlmostEqual(data["results"]["DEV-B"]["end_nrg_wh"], 0.25)
        self.mock_impl.click_start_button.assert_not_called()
        self.mock_impl.click_stop_button.assert_not_called()


class TestEnergyServerRecovery(unittest.TestCase):

    def setUp(self):
        energy_server.energy_control_impl = _make_mock_impl(["DEV-A"])
        energy_server.session_id = "old_session"
        energy_server.device_sessions = {"DEV-A": "old_dev_session"}
        energy_server.args.backend = BACKEND_UIA

    def test_rebuild_energy_control_impl_retries_until_success(self):
        new_impl = _make_mock_impl(["DEV-A", "DEV-B"])
        with patch.object(energy_server, 'EnergyControlImpl', side_effect=[RuntimeError("boot fail"), new_impl]):
            with patch.object(energy_server, 'generate_session_id', side_effect=["new_session", "dev_a", "dev_b"]):
                result = energy_server.rebuild_energy_control_impl()

        self.assertTrue(result)
        self.assertIs(energy_server.energy_control_impl, new_impl)
        self.assertEqual(energy_server.session_id, "new_session")
        self.assertEqual(energy_server.device_sessions, {"DEV-A": "dev_a", "DEV-B": "dev_b"})

    def test_rebuild_energy_control_impl_accepts_empty_hid_waiting_backend(self):
        new_impl = _make_mock_impl([])
        energy_server.args.backend = BACKEND_HID
        energy_server.args.hid_allow_all = True
        energy_server.args.hid_discovery_only = False
        energy_server.hid_paths_arg = None
        energy_server.hid_serials_arg = None

        with patch.object(energy_server, 'EnergyControlImpl', return_value=new_impl):
            with patch.object(energy_server, 'generate_session_id', return_value="new_session"):
                result = energy_server.rebuild_energy_control_impl()

        self.assertTrue(result)
        self.assertIs(energy_server.energy_control_impl, new_impl)
        self.assertEqual(energy_server.session_id, "new_session")
        self.assertEqual(energy_server.device_sessions, {})

    def test_monitor_device_connection_does_not_mark_stale_when_nrg_changes(self):
        impl = _make_mock_impl(["DEV-A"])
        impl.get_ibus_value.side_effect = ["0.1", "0.1"]
        impl.get_vbus_value.side_effect = ["5.0", "5.0"]
        impl.get_last_value_item.side_effect = ["0.0001 Wh", "0.0002 Wh"]
        energy_server.energy_control_impl = impl

        with patch.object(energy_server, 'STALE_CYCLES_THRESHOLD', 1):
            with patch.object(energy_server, 'shutdown_event') as mock_shutdown:
                mock_shutdown.is_set.side_effect = [False, False, True]
                with patch.object(energy_server, 'rebuild_energy_control_impl') as mock_rebuild:
                    with patch.object(energy_server.time, 'sleep'):
                        energy_server.monitor_device_connection()

        mock_rebuild.assert_not_called()

    def test_monitor_device_connection_marks_stale_when_all_values_unchanged(self):
        impl = _make_mock_impl(["DEV-A"])
        impl.get_ibus_value.side_effect = ["0.1", "0.1"]
        impl.get_vbus_value.side_effect = ["5.0", "5.0"]
        impl.get_last_value_item.side_effect = ["0.0001 Wh", "0.0001 Wh"]
        energy_server.energy_control_impl = impl

        with patch.object(energy_server, 'STALE_CYCLES_THRESHOLD', 1):
            with patch.object(energy_server, 'shutdown_event') as mock_shutdown:
                mock_shutdown.is_set.side_effect = [False, False, False, True]
                with patch.object(energy_server, 'rebuild_energy_control_impl', return_value=True) as mock_rebuild:
                    with patch.object(energy_server.time, 'sleep'):
                        energy_server.monitor_device_connection()

        mock_rebuild.assert_called_once()

    def test_monitor_device_connection_hid_does_not_rebuild_when_fresh(self):
        impl = _make_mock_impl(["DEV-A"])
        impl.get_device_health.return_value = {
            "connected": True,
            "last_sample_monotonic": 100.0,
            "title": "DEV-A",
        }
        impl.refresh_hid_devices.return_value = {
            "added": [],
            "removed": [],
            "restarted": [],
            "devices": ["DEV-A"],
        }
        energy_server.energy_control_impl = impl
        energy_server.args.backend = BACKEND_HID

        with patch.object(energy_server.time, 'monotonic', return_value=101.0):
            with patch.object(energy_server, 'shutdown_event') as mock_shutdown:
                mock_shutdown.is_set.side_effect = [False, True]
                with patch.object(energy_server, 'rebuild_energy_control_impl') as mock_rebuild:
                    with patch.object(energy_server.time, 'sleep'):
                        energy_server.monitor_device_connection_hid()

        mock_rebuild.assert_not_called()
        impl.refresh_hid_devices.assert_called_once_with(restart_device_names=[])

    def test_monitor_device_connection_hid_refreshes_when_stale(self):
        impl = _make_mock_impl(["DEV-A"])
        impl.get_device_health.return_value = {
            "connected": True,
            "last_sample_monotonic": 100.0,
            "title": "DEV-A",
        }
        impl.refresh_hid_devices.return_value = {
            "added": [],
            "removed": [],
            "restarted": ["DEV-A"],
            "devices": ["DEV-A"],
        }
        energy_server.energy_control_impl = impl
        energy_server.args.backend = BACKEND_HID

        with patch.object(energy_server.time, 'monotonic', return_value=110.0):
            with patch.object(energy_server, 'shutdown_event') as mock_shutdown:
                mock_shutdown.is_set.side_effect = [False, True]
                with patch.object(energy_server, 'rebuild_energy_control_impl') as mock_rebuild:
                    with patch.object(energy_server.time, 'sleep'):
                        energy_server.monitor_device_connection_hid()

        mock_rebuild.assert_not_called()
        impl.refresh_hid_devices.assert_called_once_with(restart_device_names=["DEV-A"])


class TestEnergyServerBootSelection(unittest.TestCase):
    def test_monitor_target_selection_expression(self):
        monitor_target = (
            energy_server.monitor_device_connection_hid
            if BACKEND_HID == BACKEND_HID
            else energy_server.monitor_device_connection
        )
        self.assertIs(monitor_target, energy_server.monitor_device_connection_hid)

    def test_source_uses_backend_specific_monitor_target(self):
        source_path = Path(__file__).with_name('energy_server.py')
        with source_path.open('r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('monitor_target = (', source)
        self.assertIn('if args.backend == BACKEND_HID', source)
        self.assertIn('threading.Thread(target=monitor_target, daemon=True).start()', source)


if __name__ == '__main__':
    unittest.main()
