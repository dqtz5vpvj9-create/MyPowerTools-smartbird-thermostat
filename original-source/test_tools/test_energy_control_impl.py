"""
Unit tests for energy_control_impl.py
使用 mock 模拟 GUI/硬件依赖，测试多设备支持逻辑。
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock, call
import threading
import time
from types import SimpleNamespace


# Mock 所有 Windows GUI 依赖，使测试可以在无 GUI 环境下运行
import sys
sys.modules['win32gui'] = MagicMock()
sys.modules['win32con'] = MagicMock()
sys.modules['pywinauto'] = MagicMock()
sys.modules['pywinauto.mouse'] = MagicMock()
sys.modules['psutil'] = MagicMock()

from test_tools.energy_control_impl import (
    BACKEND_HID,
    BACKEND_UIA,
    SAFETY_MODE_FULL_COMPAT_RESEARCH,
    SAFETY_MODE_SAFE_READONLY,
    DeviceHandle,
    EnergyControlImpl,
    UnsafeOperationBlockedError,
    retry,
    MAX_SETTING_RETRIES,
)


# ===================== DeviceHandle Tests =====================

class TestDeviceHandle(unittest.TestCase):

    def _make_device(self, name="DEV-1"):
        dw = MagicMock()
        return DeviceHandle(name=name, device_window=dw)

    def test_init_stores_name_and_window(self):
        dw = MagicMock()
        dev = DeviceHandle("TestDev", dw)
        self.assertEqual(dev.name, "TestDev")
        self.assertIs(dev.device_window, dw)
        self.assertIsNone(dev.create_button)

    def test_initialize_interfaces_success(self):
        dev = self._make_device()
        mock_control = MagicMock()
        mock_parent = MagicMock()
        children = [MagicMock() for _ in range(5)]
        mock_parent.children.return_value = children
        mock_control.parent.return_value = mock_parent
        dev.device_window.child_window.side_effect = lambda **kw: {
            ("控制:",): mock_control,
        }.get((kw.get("title"),), MagicMock())
        # Override child_window to return proper mocks
        def child_window_router(**kwargs):
            title = kwargs.get("title")
            if title == "控制:":
                return mock_control
            mock_cw = MagicMock()
            mock_cw.parent.return_value = MagicMock()
            return mock_cw
        dev.device_window.child_window.side_effect = child_window_router

        dev.initialize_interfaces()
        self.assertIs(dev.create_button, children[1])
        self.assertIs(dev.start_button, children[2])
        self.assertIs(dev.pause_button, children[3])
        self.assertIs(dev.stop_button, children[4])

    def test_initialize_interfaces_too_few_children_raises(self):
        dev = self._make_device()
        mock_control = MagicMock()
        mock_parent = MagicMock()
        mock_parent.children.return_value = [MagicMock() for _ in range(3)]  # 不够 5 个
        mock_control.parent.return_value = mock_parent
        dev.device_window.child_window.return_value = mock_control

        with self.assertRaises(Exception) as ctx:
            dev.initialize_interfaces()
        self.assertIn("Could not find all control buttons", str(ctx.exception))

    def test_click_buttons(self):
        dev = self._make_device()
        dev.create_button = MagicMock()
        dev.start_button = MagicMock()
        dev.pause_button = MagicMock()
        dev.stop_button = MagicMock()

        dev.click_create()
        dev.create_button.click.assert_called_once()
        dev.click_start()
        dev.start_button.click.assert_called_once()
        dev.click_pause()
        dev.pause_button.click.assert_called_once()
        dev.click_stop()
        dev.stop_button.click.assert_called_once()

    def test_get_last_value_item(self):
        dev = self._make_device()
        dev.value_p = MagicMock()
        mock_item = MagicMock()
        mock_item.window_text.return_value = "0.0123 Wh"
        dev.value_p.descendants.return_value = [MagicMock(), mock_item]

        result = dev.get_last_value_item()
        self.assertEqual(result, "0.0123 Wh")

    def test_get_last_value_item_prefers_nrg_pair(self):
        dev = self._make_device()
        dev.value_p = MagicMock()
        labels = ["累计值", "", "TIME", "00:00:01.000", "NRG", "0.0123 Wh"]
        items = []
        for label in labels:
            item = MagicMock()
            item.window_text.return_value = label
            items.append(item)
        dev.value_p.descendants.return_value = items

        result = dev.get_last_value_item()

        self.assertEqual(result, "0.0123 Wh")

    def test_get_last_value_item_empty_raises(self):
        dev = self._make_device()
        dev.value_p = MagicMock()
        dev.value_p.descendants.return_value = []

        with self.assertRaises(Exception) as ctx:
            dev.get_last_value_item()
        self.assertIn("No items found", str(ctx.exception))

    def test_get_vbus_value(self):
        dev = self._make_device()
        dev.vbus_pane = MagicMock()
        mock_val = MagicMock()
        mock_val.window_text.return_value = "5.10330"
        dev.vbus_pane.descendants.return_value = [MagicMock(), mock_val]

        self.assertEqual(dev.get_vbus_value(), "5.10330")

    def test_get_ibus_value(self):
        dev = self._make_device()
        dev.ibus_pane = MagicMock()
        mock_val = MagicMock()
        mock_val.window_text.return_value = "0.00007"
        dev.ibus_pane.descendants.return_value = [MagicMock(), mock_val]

        self.assertEqual(dev.get_ibus_value(), "0.00007")

    def test_get_vbus_empty_raises(self):
        dev = self._make_device()
        dev.vbus_pane = MagicMock()
        dev.vbus_pane.descendants.return_value = []
        with self.assertRaises(Exception):
            dev.get_vbus_value()

    def test_get_ibus_empty_raises(self):
        dev = self._make_device()
        dev.ibus_pane = MagicMock()
        dev.ibus_pane.descendants.return_value = []
        with self.assertRaises(Exception):
            dev.get_ibus_value()


# ===================== EnergyControlImpl Tests =====================

class TestBuildDeviceConfigs(unittest.TestCase):
    """_build_device_configs 是纯静态方法，无需 mock。"""

    def test_with_devices_list(self):
        devices = [{"title": "A"}, {"title": "B"}]
        result = EnergyControlImpl._build_device_configs(None, devices)
        self.assertEqual(result, devices)

    def test_devices_takes_priority_over_serial(self):
        devices = [{"title": "X"}]
        result = EnergyControlImpl._build_device_configs(1234, devices)
        self.assertEqual(result, devices)

    def test_with_product_serial(self):
        result = EnergyControlImpl._build_device_configs(6297, None)
        self.assertEqual(result, [{"title": "FNIRSI_C1-6297"}])

    def test_returns_none_for_auto_discovery_when_both_none(self):
        result = EnergyControlImpl._build_device_configs(None, None)
        self.assertIsNone(result)

    def test_rect_interactable_when_mostly_inside_container(self):
        rect = SimpleNamespace(left=10, top=10, right=110, bottom=110)
        container = SimpleNamespace(left=0, top=0, right=200, bottom=200)

        self.assertTrue(EnergyControlImpl._is_rect_interactable(rect, container))

    def test_rect_not_interactable_when_mostly_offscreen(self):
        rect = SimpleNamespace(left=-180, top=-100, right=-20, bottom=100)
        container = SimpleNamespace(left=0, top=0, right=200, bottom=200)

        self.assertFalse(EnergyControlImpl._is_rect_interactable(rect, container))


def _create_mock_ctrl(device_names):
    """
    构造一个跳过 __init__ 的 EnergyControlImpl 实例，
    并注入 mock DeviceHandle。
    """
    ctrl = object.__new__(EnergyControlImpl)
    ctrl.logger = MagicMock()
    ctrl.window_handle = 12345
    ctrl.exe_path = r"UsbMeter_V0_0_6\UsbMeter.exe"
    ctrl._gui_lock = threading.RLock()
    ctrl.backend_name = BACKEND_UIA
    ctrl.safety_mode = SAFETY_MODE_SAFE_READONLY
    ctrl.allow_unsafe_control = False
    ctrl.hid_discovery_only = False
    ctrl.hid_allow_all = False
    ctrl.hid_path = None
    ctrl.hid_serial = None
    ctrl._hid_manager = None
    ctrl._devices = {}
    for name in device_names:
        dev = MagicMock(spec=DeviceHandle)
        dev.name = name
        dev.lock = threading.Lock()
        dev.get_health = MagicMock(return_value={"connected": True, "title": name})
        ctrl._devices[name] = dev
    ctrl._default_device_name = device_names[0] if device_names else None
    return ctrl


class TestEnergyControlImplDeviceAccess(unittest.TestCase):

    def test_device_names_property(self):
        ctrl = _create_mock_ctrl(["DEV-A", "DEV-B"])
        self.assertEqual(ctrl.device_names, ["DEV-A", "DEV-B"])

    def test_get_device_default(self):
        ctrl = _create_mock_ctrl(["DEV-A", "DEV-B"])
        dev = ctrl.get_device()
        self.assertEqual(dev.name, "DEV-A")

    def test_get_device_by_name(self):
        ctrl = _create_mock_ctrl(["DEV-A", "DEV-B"])
        dev = ctrl.get_device("DEV-B")
        self.assertEqual(dev.name, "DEV-B")

    def test_get_device_not_found_raises(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        with self.assertRaises(KeyError) as ctx:
            ctrl.get_device("NO-EXIST")
        self.assertIn("NO-EXIST", str(ctx.exception))

    def test_get_default_device(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        self.assertIsNotNone(ctrl._get_default_device())

    def test_get_default_device_empty(self):
        ctrl = _create_mock_ctrl([])
        self.assertIsNone(ctrl._get_default_device())

    def test_get_nrg_wh_value_uses_precise_device_method_when_available(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl._devices["DEV-A"].get_nrg_wh_value = MagicMock(return_value=0.123456789)

        self.assertAlmostEqual(ctrl.get_nrg_wh_value("DEV-A"), 0.123456789)

    def test_get_nrg_wh_value_falls_back_to_last_value_item(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl._devices["DEV-A"].get_last_value_item.return_value = "0.1234 Wh"

        self.assertAlmostEqual(ctrl.get_nrg_wh_value("DEV-A"), 0.1234)

    def test_default_backend_status_is_safe_readonly(self):
        ctrl = _create_mock_ctrl(["DEV-A"])

        status = ctrl.get_backend_status()

        self.assertEqual(status["backend_name"], BACKEND_UIA)
        self.assertEqual(status["safety_mode"], SAFETY_MODE_SAFE_READONLY)
        self.assertFalse(status["allow_unsafe_control"])
        self.assertFalse(status["control_operations_allowed"])
        self.assertTrue(status["capabilities"]["read_vbus"])
        self.assertFalse(status["capabilities"]["start_session"])

    def test_supports_control_operations_only_when_explicitly_enabled(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        self.assertFalse(ctrl.supports_control_operations())

        ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        ctrl.allow_unsafe_control = True

        self.assertTrue(ctrl.supports_control_operations())

    def test_hid_backend_never_supports_control_operations(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl.backend_name = BACKEND_HID
        ctrl.safety_mode = SAFETY_MODE_SAFE_READONLY
        ctrl.allow_unsafe_control = False

        self.assertFalse(ctrl.supports_control_operations())

    def test_hid_backend_status_includes_device_health(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl.backend_name = BACKEND_HID
        ctrl._devices["DEV-A"].get_health.return_value = {
            "connected": True,
            "last_sample_monotonic": 123.0,
            "title": "DEV-A",
        }

        status = ctrl.get_backend_status()

        self.assertEqual(status["backend_name"], BACKEND_HID)
        self.assertIn("device_health", status)
        self.assertTrue(status["device_health"]["DEV-A"]["connected"])

    def test_get_device_health_uses_handle_health_when_available(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl._devices["DEV-A"].get_health.return_value = {"connected": True, "title": "DEV-A"}

        health = ctrl.get_device_health("DEV-A")

        self.assertEqual(health["title"], "DEV-A")

    def test_validate_runtime_mode_accepts_hid_safe_readonly(self):
        ctrl = object.__new__(EnergyControlImpl)
        ctrl.backend_name = BACKEND_HID
        ctrl.safety_mode = SAFETY_MODE_SAFE_READONLY
        ctrl.allow_unsafe_control = False

        ctrl._validate_runtime_mode()

    def test_validate_runtime_mode_rejects_hid_unsafe_control(self):
        ctrl = object.__new__(EnergyControlImpl)
        ctrl.backend_name = BACKEND_HID
        ctrl.safety_mode = SAFETY_MODE_SAFE_READONLY
        ctrl.allow_unsafe_control = True

        with self.assertRaises(ValueError):
            ctrl._validate_runtime_mode()

    def test_validate_runtime_mode_rejects_hid_non_safe_mode(self):
        ctrl = object.__new__(EnergyControlImpl)
        ctrl.backend_name = BACKEND_HID
        ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        ctrl.allow_unsafe_control = False

        with self.assertRaises(ValueError):
            ctrl._validate_runtime_mode()

    def test_validate_runtime_mode_rejects_hid_allow_all_without_lab_env(self):
        ctrl = object.__new__(EnergyControlImpl)
        ctrl.backend_name = BACKEND_HID
        ctrl.safety_mode = SAFETY_MODE_SAFE_READONLY
        ctrl.allow_unsafe_control = False
        ctrl.hid_allow_all = True

        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, "hid_allow_all is disabled"):
                ctrl._validate_runtime_mode()

    @patch('test_tools.energy_control_impl.HidBackendManager')
    def test_hid_default_initializes_waiting_backend_without_opened_devices(self, mock_manager_cls):
        manager = mock_manager_cls.return_value
        manager.discover_devices.return_value = {}
        manager.list_candidates.return_value = [{"path": "p1", "hid_serial": "SERIAL-A"}]

        ctrl = EnergyControlImpl(backend_name=BACKEND_HID)

        self.assertEqual(ctrl.device_names, [])
        self.assertEqual(ctrl._hid_candidates, [{"path": "p1", "hid_serial": "SERIAL-A"}])
        self.assertIsNone(ctrl._default_device_name)
        status = ctrl.get_backend_status()
        self.assertFalse(status["capabilities"]["read_nrg"])
        self.assertFalse(status["hid_discovery_only"])
        self.assertTrue(status["hid_waiting"])
        mock_manager_cls.assert_called_once_with(
            requested_titles=None,
            logger=ctrl.logger,
            hid_path=None,
            hid_serial=None,
            allow_all=False,
            discovery_only=False,
        )

    @patch('test_tools.energy_control_impl.HidBackendManager')
    def test_hid_allow_all_initializes_waiting_backend_without_devices(self, mock_manager_cls):
        manager = mock_manager_cls.return_value
        manager.discover_devices.return_value = {}
        manager.list_candidates.return_value = []

        with patch.dict('os.environ', {'USBMETER_HID_ALLOW_ALL_UNSAFE': '1'}):
            ctrl = EnergyControlImpl(backend_name=BACKEND_HID, hid_allow_all=True)

        self.assertEqual(ctrl.device_names, [])
        self.assertIsNone(ctrl._default_device_name)
        status = ctrl.get_backend_status()
        self.assertFalse(status["capabilities"]["read_nrg"])
        self.assertFalse(status["hid_discovery_only"])
        self.assertTrue(status["hid_waiting"])
        mock_manager_cls.assert_called_once_with(
            requested_titles=None,
            logger=ctrl.logger,
            hid_path=None,
            hid_serial=None,
            allow_all=True,
            discovery_only=False,
        )

    @patch('test_tools.energy_control_impl.HidBackendManager')
    def test_hid_explicit_path_passes_single_device_selector(self, mock_manager_cls):
        manager = mock_manager_cls.return_value
        dev = MagicMock()
        dev.name = "DEV-A"
        manager.discover_devices.return_value = {"DEV-A": dev}
        manager.list_candidates.return_value = [{"path": "path-a"}]

        ctrl = EnergyControlImpl(backend_name=BACKEND_HID, hid_path="path-a")

        self.assertEqual(ctrl.device_names, ["DEV-A"])
        self.assertEqual(ctrl._default_device_name, "DEV-A")
        mock_manager_cls.assert_called_once_with(
            requested_titles=None,
            logger=ctrl.logger,
            hid_path="path-a",
            hid_serial=None,
            allow_all=False,
            discovery_only=False,
        )


class TestEnergyControlImplOperations(unittest.TestCase):
    """测试设备操作方法的委托逻辑。"""

    def setUp(self):
        self.ctrl = _create_mock_ctrl(["DEV-A", "DEV-B"])
        # Mock _ensure_window_foreground
        self.ctrl._ensure_window_foreground = MagicMock()

    def test_click_create_button_default(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_create_button()
        self.ctrl._devices["DEV-A"].click_create.assert_called_once()

    def test_click_create_button_specific(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_create_button("DEV-B")
        self.ctrl._devices["DEV-B"].click_create.assert_called_once()
        self.ctrl._devices["DEV-A"].click_create.assert_not_called()

    def test_click_start_button(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_start_button("DEV-A")
        self.ctrl._devices["DEV-A"].click_start.assert_called_once()

    def test_click_pause_button(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_pause_button("DEV-B")
        self.ctrl._devices["DEV-B"].click_pause.assert_called_once()

    def test_click_stop_button(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_stop_button()
        self.ctrl._devices["DEV-A"].click_stop.assert_called_once()

    def test_get_last_value_item(self):
        self.ctrl._devices["DEV-A"].get_last_value_item.return_value = "0.05 Wh"
        result = self.ctrl.get_last_value_item()
        self.assertEqual(result, "0.05 Wh")

    def test_get_vbus_value(self):
        self.ctrl._devices["DEV-B"].get_vbus_value.return_value = "5.1"
        result = self.ctrl.get_vbus_value("DEV-B")
        self.assertEqual(result, "5.1")

    def test_get_ibus_value(self):
        self.ctrl._devices["DEV-A"].get_ibus_value.return_value = "0.001"
        result = self.ctrl.get_ibus_value("DEV-A")
        self.assertEqual(result, "0.001")

    def test_control_operation_blocked_in_safe_mode(self):
        with self.assertRaises(UnsafeOperationBlockedError):
            self.ctrl.click_start_button("DEV-A")

        self.ctrl._devices["DEV-A"].click_start.assert_not_called()


class TestEnergyControlImplBatchOps(unittest.TestCase):
    """测试批量操作方法。"""

    def setUp(self):
        self.ctrl = _create_mock_ctrl(["DEV-A", "DEV-B", "DEV-C"])
        self.ctrl._ensure_window_foreground = MagicMock()

    def test_click_create_all(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_create_all()
        for dev in self.ctrl._devices.values():
            dev.click_create.assert_called_once()

    def test_click_start_all(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_start_all()
        for dev in self.ctrl._devices.values():
            dev.click_start.assert_called_once()

    def test_click_pause_all(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_pause_all()
        for dev in self.ctrl._devices.values():
            dev.click_pause.assert_called_once()

    def test_click_stop_all(self):
        self.ctrl.safety_mode = SAFETY_MODE_FULL_COMPAT_RESEARCH
        self.ctrl.allow_unsafe_control = True
        self.ctrl.click_stop_all()
        for dev in self.ctrl._devices.values():
            dev.click_stop.assert_called_once()

    def test_get_all_values(self):
        for name, dev in self.ctrl._devices.items():
            dev.get_vbus_value.return_value = f"vbus_{name}"
            dev.get_ibus_value.return_value = f"ibus_{name}"
            dev.get_last_value_item.return_value = f"nrg_{name}"

        result = self.ctrl.get_all_values()
        self.assertEqual(len(result), 3)
        for name in ["DEV-A", "DEV-B", "DEV-C"]:
            self.assertEqual(result[name]["vbus"], f"vbus_{name}")
            self.assertEqual(result[name]["ibus"], f"ibus_{name}")
            self.assertEqual(result[name]["nrg"], f"nrg_{name}")

    def test_get_all_values_partial_failure(self):
        self.ctrl._devices["DEV-A"].get_vbus_value.return_value = "5.0"
        self.ctrl._devices["DEV-A"].get_ibus_value.return_value = "0.1"
        self.ctrl._devices["DEV-A"].get_last_value_item.return_value = "0.5"

        self.ctrl._devices["DEV-B"].get_vbus_value.side_effect = Exception("disconnected")

        self.ctrl._devices["DEV-C"].get_vbus_value.return_value = "4.9"
        self.ctrl._devices["DEV-C"].get_ibus_value.return_value = "0.2"
        self.ctrl._devices["DEV-C"].get_last_value_item.return_value = "0.3"

        result = self.ctrl.get_all_values()
        self.assertEqual(result["DEV-A"]["vbus"], "5.0")
        self.assertIn("error", result["DEV-B"])
        self.assertIn("disconnected", result["DEV-B"]["error"])
        self.assertEqual(result["DEV-C"]["nrg"], "0.3")

    def test_click_start_all_blocked_in_safe_mode(self):
        with self.assertRaises(UnsafeOperationBlockedError):
            self.ctrl.click_start_all()


class TestEnergyControlImplKill(unittest.TestCase):

    @patch('test_tools.energy_control_impl.psutil')
    def test_kill_terminates_processes(self, mock_psutil):
        ctrl = _create_mock_ctrl(["DEV-A"])
        proc1 = MagicMock()
        proc1.name.return_value = "UsbMeter.exe"
        proc2 = MagicMock()
        proc2.name.return_value = "other.exe"
        mock_psutil.process_iter.return_value = [proc1, proc2]

        ctrl.kill()
        proc1.kill.assert_called_once()
        proc2.kill.assert_not_called()

    def test_hid_kill_closes_manager_without_touching_processes(self):
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl.backend_name = BACKEND_HID
        hid_manager = MagicMock()
        ctrl._hid_manager = hid_manager

        ctrl.kill()

        hid_manager.close.assert_called_once()
        self.assertEqual(ctrl._devices, {})


# ===================== Retry Decorator Tests =====================

class TestRetryDecorator(unittest.TestCase):

    def test_retry_succeeds_first_attempt(self):
        mock_fn = MagicMock(return_value="ok")

        @retry(max_attempts=3, delay=0.01)
        def fn():
            return mock_fn()

        result = fn()
        self.assertEqual(result, "ok")
        self.assertEqual(mock_fn.call_count, 1)

    def test_retry_succeeds_after_failures(self):
        mock_fn = MagicMock(side_effect=[Exception("fail1"), Exception("fail2"), "ok"])

        @retry(max_attempts=3, delay=0.01)
        def fn():
            return mock_fn()

        result = fn()
        self.assertEqual(result, "ok")
        self.assertEqual(mock_fn.call_count, 3)

    def test_retry_exhausted_raises_last_exception(self):
        mock_fn = MagicMock(side_effect=ValueError("always fail"))

        @retry(max_attempts=2, delay=0.01, exceptions=(ValueError,))
        def fn():
            return mock_fn()

        with self.assertRaises(ValueError) as ctx:
            fn()
        self.assertIn("always fail", str(ctx.exception))
        self.assertEqual(mock_fn.call_count, 2)

    def test_retry_does_not_catch_unspecified_exceptions(self):
        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def fn():
            raise TypeError("wrong type")

        with self.assertRaises(TypeError):
            fn()


# ===================== Toggle Checkbox Tests =====================

class TestToggleCheckbox(unittest.TestCase):

    def test_already_at_target_no_click(self):
        checkbox = MagicMock()
        checkbox.window_text.return_value = 'false'
        ensure_fn = MagicMock()

        DeviceHandle._toggle_checkbox(checkbox, 'false', 'test', 'DEV', ensure_fn)
        checkbox.invoke.assert_not_called()
        checkbox.click_input.assert_not_called()

    def test_toggle_using_invoke(self):
        checkbox = MagicMock()
        # First call returns 'true', after invoke returns 'false'
        checkbox.window_text.side_effect = ['true', 'false']
        ensure_fn = MagicMock()

        DeviceHandle._toggle_checkbox(checkbox, 'false', 'test', 'DEV', ensure_fn)
        checkbox.invoke.assert_called_once()
        checkbox.click_input.assert_not_called()

    def test_toggle_falls_back_when_invoke_does_not_change_value(self):
        checkbox = MagicMock()
        checkbox.window_text.side_effect = ['true', 'true', 'false', 'false']
        ensure_fn = MagicMock()

        DeviceHandle._toggle_checkbox(checkbox, 'false', 'test', 'DEV', ensure_fn)
        checkbox.invoke.assert_called_once()
        checkbox.click_input.assert_called_once()

    def test_toggle_falls_back_to_click_input(self):
        checkbox = MagicMock()
        checkbox.window_text.side_effect = ['true', 'false']
        checkbox.invoke.side_effect = Exception("no invoke pattern")
        ensure_fn = MagicMock()

        DeviceHandle._toggle_checkbox(checkbox, 'false', 'test', 'DEV', ensure_fn)
        checkbox.click_input.assert_called_once()

    def test_toggle_timeout_raises(self):
        checkbox = MagicMock()
        # Never reaches target
        checkbox.window_text.return_value = 'true'
        ensure_fn = MagicMock()

        with self.assertRaises(TimeoutError) as ctx:
            DeviceHandle._toggle_checkbox(checkbox, 'false', 'VBUS', 'DEV', ensure_fn)
        self.assertIn("Failed to set VBUS", str(ctx.exception))


class TestFindTableCell(unittest.TestCase):

    def _cell(self, control_type, text):
        cell = MagicMock()
        cell.element_info = SimpleNamespace(control_type=control_type)
        cell.window_text.return_value = text
        return cell

    def test_finds_cell_by_row_and_column(self):
        group = MagicMock()
        headers = [
            self._cell("Header", "项目"),
            self._cell("Header", "颜色"),
            self._cell("Header", "记录"),
            self._cell("Header", "显示"),
        ]
        rows = [
            self._cell("DataItem", "CAP"),
            self._cell("DataItem", "#ff5500"),
            self._cell("DataItem", "true"),
            self._cell("DataItem", ""),
            self._cell("DataItem", "NRG"),
            self._cell("DataItem", "#ffaa00"),
            self._cell("DataItem", "true"),
            self._cell("DataItem", ""),
        ]
        group.descendants.return_value = [MagicMock(), *headers, *rows]

        cell = DeviceHandle._find_table_cell(group, "NRG", "记录")

        self.assertEqual(cell.window_text(), "true")


# ===================== Smart Foreground Tests =====================

class TestSmartForeground(unittest.TestCase):

    @patch('test_tools.energy_control_impl.win32gui')
    @patch('test_tools.energy_control_impl.time')
    def test_skips_if_already_foreground(self, mock_time, mock_win32gui):
        ctrl = _create_mock_ctrl(["DEV-A"])
        mock_win32gui.GetForegroundWindow.return_value = ctrl.window_handle

        ctrl._ensure_window_foreground()
        mock_win32gui.SetForegroundWindow.assert_not_called()
        mock_time.sleep.assert_not_called()

    @patch('test_tools.energy_control_impl.win32gui')
    @patch('test_tools.energy_control_impl.time')
    def test_switches_if_not_foreground(self, mock_time, mock_win32gui):
        ctrl = _create_mock_ctrl(["DEV-A"])
        mock_win32gui.GetForegroundWindow.return_value = 99999  # different handle

        ctrl._ensure_window_foreground()
        mock_win32gui.SetForegroundWindow.assert_called_once_with(ctrl.window_handle)
        self.assertEqual(mock_time.sleep.call_count, 2)
        self.assertEqual(mock_win32gui.ShowWindow.call_count, 2)
        self.assertEqual(mock_win32gui.SetWindowPos.call_count, 2)


# ===================== DeviceHandle Retry Integration Tests =====================

class TestDeviceHandleRetry(unittest.TestCase):
    """验证 DeviceHandle 的方法在失败后自动重试。"""

    def test_get_vbus_retries_on_failure(self):
        dev = DeviceHandle("DEV-R", MagicMock())
        dev.vbus_pane = MagicMock()
        # First call: empty (raises), second call: has value
        mock_val = MagicMock()
        mock_val.window_text.return_value = "5.0"
        dev.vbus_pane.descendants.side_effect = [[], [MagicMock(), mock_val]]

        result = dev.get_vbus_value()
        self.assertEqual(result, "5.0")
        self.assertEqual(dev.vbus_pane.descendants.call_count, 2)

    def test_get_ibus_retries_on_failure(self):
        dev = DeviceHandle("DEV-R", MagicMock())
        dev.ibus_pane = MagicMock()
        mock_val = MagicMock()
        mock_val.window_text.return_value = "0.01"
        dev.ibus_pane.descendants.side_effect = [[], [MagicMock(), mock_val]]

        result = dev.get_ibus_value()
        self.assertEqual(result, "0.01")

    def test_get_last_value_retries_on_failure(self):
        dev = DeviceHandle("DEV-R", MagicMock())
        dev.value_p = MagicMock()
        mock_item = MagicMock()
        mock_item.window_text.return_value = "0.05 Wh"
        dev.value_p.descendants.side_effect = [[], [mock_item]]

        result = dev.get_last_value_item()
        self.assertEqual(result, "0.05 Wh")

    def test_click_create_retries_on_failure(self):
        dev = DeviceHandle("DEV-R", MagicMock())
        dev.create_button = MagicMock()
        dev.create_button.click.side_effect = [Exception("busy"), None]

        dev.click_create()  # should not raise
        self.assertEqual(dev.create_button.click.call_count, 2)


# ===================== Concurrency Safety Tests =====================

class TestConcurrencySafety(unittest.TestCase):
    """验证多线程并发访问时的线程安全性。"""

    def test_concurrent_reads_are_serialized(self):
        """Multiple threads reading values should not interleave."""
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl._ensure_window_foreground = MagicMock()
        call_order = []
        original_get_vbus = ctrl._devices["DEV-A"].get_vbus_value

        def slow_vbus():
            call_order.append(('vbus_start', threading.current_thread().name))
            time.sleep(0.05)
            call_order.append(('vbus_end', threading.current_thread().name))
            return "5.0"

        ctrl._devices["DEV-A"].get_vbus_value.side_effect = slow_vbus

        threads = []
        for i in range(3):
            t = threading.Thread(target=ctrl.get_vbus_value, name=f"reader-{i}")
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Verify serialization: each vbus_start should be followed by its vbus_end
        # before the next vbus_start (no interleaving)
        starts = [i for i, (ev, _) in enumerate(call_order) if ev == 'vbus_start']
        ends = [i for i, (ev, _) in enumerate(call_order) if ev == 'vbus_end']
        self.assertEqual(len(starts), 3)
        self.assertEqual(len(ends), 3)
        for s, e in zip(starts, ends):
            self.assertLess(s, e, "vbus_end should come after vbus_start")
        # No interleaving: end[i] < start[i+1]
        for i in range(len(ends) - 1):
            self.assertLess(ends[i], starts[i + 1],
                            f"Operations interleaved at index {i}")

    def test_concurrent_click_and_read(self):
        """A click and a read should not run simultaneously."""
        ctrl = _create_mock_ctrl(["DEV-A"])
        ctrl._ensure_window_foreground = MagicMock()
        active_count = [0]
        max_concurrent = [0]
        lock = threading.Lock()

        def track_entry():
            with lock:
                active_count[0] += 1
                if active_count[0] > max_concurrent[0]:
                    max_concurrent[0] = active_count[0]
            time.sleep(0.03)
            with lock:
                active_count[0] -= 1

        ctrl._devices["DEV-A"].click_start.side_effect = lambda: track_entry()
        ctrl._devices["DEV-A"].get_ibus_value.side_effect = lambda: (track_entry(), "0.1")[1]

        t1 = threading.Thread(target=ctrl.click_start_button)
        t2 = threading.Thread(target=ctrl.get_ibus_value)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(max_concurrent[0], 1,
                         "Click and read ran concurrently (should be serialized)")

    def test_device_handle_has_lock(self):
        """DeviceHandle should have a threading.Lock."""
        dev = DeviceHandle("test", MagicMock())
        self.assertIsInstance(dev.lock, type(threading.Lock()))

    def test_gui_lock_is_reentrant(self):
        """_gui_lock should be an RLock (reentrant) so nested calls don't deadlock."""
        ctrl = _create_mock_ctrl(["DEV-A"])
        self.assertIsInstance(ctrl._gui_lock, type(threading.RLock()))

    def test_kill_acquires_gui_lock(self):
        """kill() should acquire the GUI lock before killing processes."""
        ctrl = _create_mock_ctrl(["DEV-A"])
        lock_acquired = [False]

        # Wrap the RLock with a proxy that tracks acquire calls
        real_lock = ctrl._gui_lock
        proxy_lock = MagicMock(wraps=real_lock)
        ctrl._gui_lock = proxy_lock

        import test_tools.energy_control_impl as energy_control_impl
        mock_proc = MagicMock()
        mock_proc.name.return_value = "other.exe"
        with patch.object(energy_control_impl.psutil, 'process_iter', return_value=[mock_proc]):
            ctrl.kill()

        proxy_lock.__enter__.assert_called()

    @patch('test_tools.energy_control_impl.subprocess')
    @patch('test_tools.energy_control_impl.time')
    def test_ensure_process_running_waits_for_existing_process_window(self, mock_time, mock_subprocess):
        ctrl = _create_mock_ctrl(["DEV-A"])
        proc = MagicMock()
        proc.name.return_value = "UsbMeter.exe"

        with patch.object(ctrl, '_iter_usbmeter_processes', return_value=iter([proc])):
            with patch.object(ctrl, '_wait_for_window_handle', side_effect=[12345]):
                ctrl._ensure_process_running()

        mock_subprocess.Popen.assert_not_called()

    @patch('test_tools.energy_control_impl.subprocess')
    def test_ensure_process_running_restarts_when_window_never_appears(self, mock_subprocess):
        ctrl = _create_mock_ctrl(["DEV-A"])
        proc = MagicMock()
        proc.name.return_value = "UsbMeter.exe"

        with patch.object(ctrl, '_iter_usbmeter_processes', side_effect=[iter([proc]), iter([proc]), iter([])]):
            with patch.object(ctrl, '_wait_for_window_handle', side_effect=[TimeoutError("no window"), TimeoutError("still no window"), 12345]):
                with patch.object(ctrl, 'kill') as mock_kill:
                    ctrl._ensure_process_running()

        mock_kill.assert_called_once()
        mock_subprocess.Popen.assert_called_once()


if __name__ == '__main__':
    unittest.main()
