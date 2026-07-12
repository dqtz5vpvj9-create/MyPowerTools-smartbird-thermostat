from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from test_tools.energy_server_task import build_server_args
from test_tools.smartbird_thermostat_task import task_args


class MyPowerToolsRuntimeSettingsTests(unittest.TestCase):
    def test_thermostat_settings_map_to_real_service_arguments(self):
        settings = {
            "serviceHost": "127.0.0.1",
            "servicePort": 29002,
            "smartBirdHost": "0.0.0.0",
            "smartBirdPort": 29001,
            "adbSerials": "device-a,device-b",
            "loopSec": 2.5,
            "minOnSec": 11,
            "minOffSec": 12,
            "marginC": 6,
            "minSurfaceC": 31,
            "onSurfaceC": 36,
            "hysteresisC": 3,
            "defaultAmbientC": 27,
            "defaultRh": 88,
            "amapCity": "110000",
            "amapTimeoutSec": 4,
            "weatherRefreshSec": 120,
            "energyServerUrl": "http://127.0.0.1:28988",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            arguments = task_args(settings, root, root / "service.log")
        pairs = dict(zip(arguments[0::2], arguments[1::2]))
        self.assertEqual("29002", pairs["--port"])
        self.assertEqual("29001", pairs["--smartbird-port"])
        self.assertEqual("device-a,device-b", pairs["--adb-serial"])
        self.assertEqual("2.5", pairs["--loop-sec"])
        self.assertEqual("6", pairs["--margin-c"])
        self.assertEqual("110000", pairs["--amap-city"])
        self.assertEqual("http://127.0.0.1:28988", pairs["--energy-server-url"])

    def test_energy_backend_and_usb_selectors_map_to_energy_server(self):
        settings = {
            "servicePort": 19002,
            "energyServerUrl": "http://127.0.0.1:28988",
            "energyBackend": "hid",
            "usbMeterSelectorMode": "serial",
            "usbMeterSelector": "meter-a,meter-b",
            "energyAllowUnsafeControl": False,
        }
        arguments = build_server_args(settings)
        self.assertIn("28988", arguments)
        self.assertIn("hid", arguments)
        self.assertEqual(2, arguments.count("--hid-serial"))
        self.assertIn("meter-a", arguments)
        self.assertIn("meter-b", arguments)
        self.assertIn("safe_readonly", arguments)

    def test_release_source_contains_both_tasks_and_runtime_dependencies(self):
        source_root = Path(__file__).resolve().parents[1]
        required = [
            "test_tools/smartbird_thermostat_service.py",
            "test_tools/smartbird_thermostat_task.py",
            "test_tools/energy_server.py",
            "test_tools/energy_server_task.py",
            "test_tools/energy_control_impl.py",
            "test_tools/usbmeter_hid_backend.py",
            "scripts/install-smartbird-thermostat-task.ps1",
            "scripts/install-energy-server-task.ps1",
            "requirements-energy-runtime.txt",
        ]
        for relative_path in required:
            self.assertTrue((source_root / relative_path).is_file(), relative_path)


if __name__ == "__main__":
    unittest.main()
