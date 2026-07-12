from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from test_tools.smartbird_thermostat import (
    KEY_OFF,
    KEY_ON,
    MODE_EXPERIMENT,
    MODE_PROTECTION,
    AndroidThermalReader,
    SmartBirdThermalConfig,
    SmartBirdThermostat,
    ThermalSample,
    WeatherSample,
    dew_point_celsius,
    normalize_adb_serials,
    parse_thermalservice_temperatures,
)


class FakeSwitch:
    def __init__(self, key=None):
        self.key = key
        self.commands = []

    def set_key(self, key: int, reason: str = "") -> bool:
        self.key = key
        self.commands.append((key, reason))
        return True

    def get_key(self):
        return self.key

    def snapshot(self):
        return {"key": self.key, "commands": list(self.commands)}


class FakeThermalReader:
    def __init__(self, surface_c: float):
        self.surface_c = surface_c

    def current(self):
        return ThermalSample(
            surface_c=self.surface_c,
            sensor_name="VIRTUAL-SKIN",
            temperatures={"VIRTUAL-SKIN": self.surface_c},
        )


class FailingThermalReader:
    def current(self):
        raise RuntimeError("adb offline")


class FakeWeatherProvider:
    def __init__(self, temperature_c=28.0, humidity_pct=83.0):
        self.sample = WeatherSample(temperature_c, humidity_pct, "test")

    def current(self):
        return self.sample


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


def make_thermostat(surface_c, switch_key=None, clock=None):
    cfg = SmartBirdThermalConfig(
        enabled=True,
        min_on_sec=180.0,
        min_off_sec=180.0,
        plum_rain_margin_c=5.0,
        protection_min_surface_c=30.0,
        protection_on_surface_c=35.0,
        protection_hysteresis_c=4.0,
    )
    switch = FakeSwitch(switch_key)
    clock = clock or FakeClock()
    thermostat = SmartBirdThermostat(
        cfg,
        switch,
        thermal_reader=FakeThermalReader(surface_c),
        weather_provider=FakeWeatherProvider(),
        clock=clock,
    )
    return thermostat, switch, clock


class TestSmartBirdThermostat(unittest.TestCase):
    def test_dew_point_for_plum_rain_conditions(self):
        self.assertAlmostEqual(dew_point_celsius(28.0, 83.0), 24.84, places=1)

    def test_parse_current_hal_temperatures(self):
        output = """
Cached temperatures:
    Temperature{mValue=65.0, mType=0, mName=MID, mStatus=0}
Current temperatures from HAL:
    Temperature{mValue=23.793081, mType=3, mName=VIRTUAL-SKIN, mStatus=0}
    Temperature{mValue=22.373001, mType=4, mName=VIRTUAL-USB-UI, mStatus=0}
Current cooling devices from HAL:
    CoolingDevice{mValue=0, mType=5, mName=tpu_cooling}
"""
        temps = parse_thermalservice_temperatures(output)
        self.assertEqual(temps["VIRTUAL-SKIN"], 23.793081)
        self.assertEqual(temps["VIRTUAL-USB-UI"], 22.373001)
        self.assertNotIn("MID", temps)

    def test_normalize_adb_serials_accepts_fallback_list(self):
        serials = normalize_adb_serials("10.33.0.243:5555, 192.168.29.79:35559")

        self.assertEqual(serials, ["10.33.0.243:5555", "192.168.29.79:35559"])

    def test_android_thermal_reader_falls_back_to_second_serial(self):
        output = """
Current temperatures from HAL:
    Temperature{mValue=31.25, mType=3, mName=VIRTUAL-SKIN, mStatus=0}
Current cooling devices from HAL:
"""
        calls = []

        class Proc:
            stdout = output

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd[2] == "10.33.0.243:5555":
                raise RuntimeError("offline")
            return Proc()

        reader = AndroidThermalReader("10.33.0.243:5555,192.168.29.79:35559")

        with patch("test_tools.smartbird_thermostat.subprocess.run", side_effect=fake_run):
            sample = reader.current()

        self.assertEqual([cmd[2] for cmd in calls], ["10.33.0.243:5555", "192.168.29.79:35559"])
        self.assertEqual(sample.surface_c, 31.25)
        self.assertEqual(sample.sensor_name, "VIRTUAL-SKIN@192.168.29.79:35559")

    def test_android_thermal_reader_prefers_bundled_adb_over_path(self):
        with (
            patch.dict(os.environ, {"ADB_PATH": ""}),
            patch("test_tools.smartbird_thermostat.Path.is_file", return_value=True),
            patch("test_tools.smartbird_thermostat.shutil.which", return_value=r"D:\\AndroidSDK\\adb.exe"),
        ):
            resolved = AndroidThermalReader._resolve_adb_executable()

        self.assertTrue(resolved.endswith(r"Tools\AndroidPlatformTools\adb.exe"))

    def test_experiment_start_forces_cooling_on(self):
        thermostat, switch, _clock = make_thermostat(surface_c=22.0, switch_key=KEY_OFF)

        snapshot = thermostat.enter_experiment("energy_start")

        self.assertEqual(snapshot["mode"], MODE_EXPERIMENT)
        self.assertEqual(switch.commands[-1][0], KEY_ON)

    def test_protection_keeps_cooling_on_when_still_hot(self):
        thermostat, switch, _clock = make_thermostat(surface_c=36.0, switch_key=KEY_OFF)
        thermostat.enter_experiment("energy_start")
        switch.commands.clear()

        snapshot = thermostat.enter_protection("energy_stop")

        self.assertEqual(snapshot["mode"], MODE_PROTECTION)
        self.assertEqual(switch.key, KEY_ON)
        self.assertEqual(switch.commands, [])

    def test_protection_turns_cooling_off_when_surface_is_below_safe_floor(self):
        thermostat, switch, _clock = make_thermostat(surface_c=22.0, switch_key=KEY_OFF)
        thermostat.enter_experiment("energy_start")
        switch.commands.clear()

        snapshot = thermostat.enter_protection("energy_stop")

        self.assertEqual(snapshot["mode"], MODE_PROTECTION)
        self.assertEqual(switch.commands[-1][0], KEY_OFF)
        self.assertEqual(switch.key, KEY_OFF)

    def test_protection_turns_cooling_off_when_adb_is_unavailable(self):
        cfg = SmartBirdThermalConfig(enabled=True)
        switch = FakeSwitch(KEY_ON)
        thermostat = SmartBirdThermostat(
            cfg,
            switch,
            thermal_reader=FailingThermalReader(),
            weather_provider=FakeWeatherProvider(),
        )

        decision = thermostat.evaluate_once("adb_missing")

        self.assertEqual(decision.desired_key, KEY_OFF)
        self.assertEqual(decision.applied_key, KEY_OFF)
        self.assertEqual(switch.key, KEY_OFF)
        self.assertIn("protect_off_no_adb", decision.reason)

    def test_energy_stop_turns_cooling_off_when_adb_is_unavailable(self):
        cfg = SmartBirdThermalConfig(enabled=True)
        switch = FakeSwitch(KEY_OFF)
        thermostat = SmartBirdThermostat(
            cfg,
            switch,
            thermal_reader=FailingThermalReader(),
            weather_provider=FakeWeatherProvider(),
        )

        start_snapshot = thermostat.enter_experiment("energy_start")
        stop_snapshot = thermostat.enter_protection("energy_stop")

        self.assertEqual(start_snapshot["last_key"], KEY_ON)
        self.assertEqual(stop_snapshot["mode"], MODE_PROTECTION)
        self.assertEqual(stop_snapshot["last_key"], KEY_OFF)
        self.assertEqual([cmd[0] for cmd in switch.commands], [KEY_ON, KEY_OFF])
        self.assertIn("protect_off_no_adb", stop_snapshot["last_decision"]["reason"])

    def test_min_off_time_blocks_hot_restart_until_elapsed(self):
        clock = FakeClock(10.0)
        thermostat, switch, _clock = make_thermostat(
            surface_c=40.0,
            switch_key=KEY_OFF,
            clock=clock,
        )
        thermostat._last_key = KEY_OFF
        thermostat._last_change_monotonic = 0.0

        first = thermostat.evaluate_once("hot_soon_after_off")

        self.assertTrue(first.blocked_by_min_runtime)
        self.assertEqual(switch.commands, [])

        clock.advance(180.0)
        second = thermostat.evaluate_once("hot_after_min_off")

        self.assertFalse(second.blocked_by_min_runtime)
        self.assertEqual(switch.commands[-1][0], KEY_ON)

    def test_history_records_decisions(self):
        thermostat, _switch, _clock = make_thermostat(surface_c=36.0, switch_key=KEY_OFF)

        thermostat.evaluate_once("sample")

        history = thermostat.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["mode"], MODE_PROTECTION)
        self.assertEqual(history[0]["surface_c"], 36.0)
        self.assertIsNotNone(history[0]["dew_point_c"])

    def test_events_record_mode_and_switch_changes(self):
        thermostat, switch, _clock = make_thermostat(surface_c=22.0, switch_key=KEY_OFF)

        thermostat.enter_experiment("energy_start")

        events = thermostat.events()
        self.assertEqual(events[0]["type"], "mode")
        self.assertEqual(events[-1]["type"], "switch")
        self.assertEqual(events[-1]["key"], KEY_ON)
        self.assertEqual(switch.commands[-1][0], KEY_ON)

    def test_history_and_events_persist_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.jsonl"
            events_path = Path(tmp) / "events.jsonl"
            cfg = SmartBirdThermalConfig(enabled=True)
            first = SmartBirdThermostat(
                cfg,
                FakeSwitch(KEY_OFF),
                thermal_reader=FakeThermalReader(38.0),
                weather_provider=FakeWeatherProvider(),
                history_path=history_path,
                events_path=events_path,
            )

            first.evaluate_once("persist_sample")
            first.enter_experiment("persist_event")

            second = SmartBirdThermostat(
                cfg,
                FakeSwitch(KEY_OFF),
                thermal_reader=FakeThermalReader(31.0),
                weather_provider=FakeWeatherProvider(),
                history_path=history_path,
                events_path=events_path,
            )

            self.assertGreaterEqual(len(second.history()), 2)
            self.assertGreaterEqual(len(second.events()), 2)
            self.assertEqual(second.events()[-1]["type"], "switch")


if __name__ == "__main__":
    unittest.main()
