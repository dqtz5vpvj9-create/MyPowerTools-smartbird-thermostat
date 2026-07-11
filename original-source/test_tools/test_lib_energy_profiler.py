import os
import unittest
from unittest.mock import patch

from py_modules.lib_energy_profiler import EnergyProfiler


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(("info", msg))

    def warning(self, msg):
        self.messages.append(("warning", msg))

    def error(self, msg):
        self.messages.append(("error", msg))

    def notice(self, msg):
        self.messages.append(("notice", msg))


class _Sender:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def control(self, ctrl_msg, device_name=None):
        self.calls.append((ctrl_msg, device_name))
        if not self.responses:
            raise AssertionError(f"unexpected control call: {ctrl_msg}")
        return self.responses.pop(0)

    def handshake(self, timeout=1.0):
        return True


class TestEnergyProfiler(unittest.TestCase):
    def test_passes_device_name_and_prefers_high_precision_energy_wh(self):
        profiler = EnergyProfiler(_Logger(), device_name="DEV-A")
        profiler.http_sender_ = _Sender([
            {"message": "OK", "session_id": "logical-1"},
            {"message": "OK", "session_id": "logical-1"},
            {"message": "OK", "session_id": "logical-1"},
            {"message": "0.0001", "energy_wh": 0.000123456, "session_id": "logical-1"},
        ])

        with patch("py_modules.lib_energy_profiler.time.sleep"):
            session_id = profiler.start()
            energy_wh, finish_session = profiler.finish()

        self.assertEqual(session_id, "logical-1")
        self.assertEqual(finish_session, "logical-1")
        self.assertAlmostEqual(energy_wh, 0.000123456)
        self.assertEqual(
            profiler.http_sender_.calls,
            [
                ("create", "DEV-A"),
                ("start", "DEV-A"),
                ("stop", "DEV-A"),
                ("read_nrg", "DEV-A"),
            ],
        )

    def test_uses_environment_device_binding(self):
        with patch.dict(os.environ, {"ENERGY_DEVICE_NAME": "DEV-B"}):
            profiler = EnergyProfiler(_Logger())

        self.assertEqual(profiler.device_name, "DEV-B")

    def test_rejects_mismatched_create_start_session(self):
        profiler = EnergyProfiler(_Logger(), device_name="DEV-A")
        profiler.http_sender_ = _Sender([
            {"message": "OK", "session_id": "logical-create"},
            {"message": "OK", "session_id": "logical-start"},
        ])

        with patch("py_modules.lib_energy_profiler.time.sleep"):
            session_id = profiler.start()

        self.assertEqual(session_id, "[invalid_session_id_1]")


if __name__ == "__main__":
    unittest.main()
