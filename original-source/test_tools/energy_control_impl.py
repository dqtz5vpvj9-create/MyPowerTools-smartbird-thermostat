import functools
import logging
import os
import re
import subprocess
import threading
import time
import psutil
import win32gui
from pywinauto import Application, findwindows, mouse
import win32con

BACKEND_UIA = "uia"
BACKEND_HID = "hid"
SAFETY_MODE_SAFE_READONLY = "safe_readonly"
SAFETY_MODE_FULL_COMPAT_RESEARCH = "full_compat_research"
CONTROL_OPERATIONS = frozenset({"create", "start", "pause", "stop"})

try:
    from test_tools.usbmeter_hid_backend import HidBackendManager, UNSAFE_ALLOW_ALL_ENV
except ImportError:
    from usbmeter_hid_backend import HidBackendManager, UNSAFE_ALLOW_ALL_ENV


class UnsafeOperationBlockedError(RuntimeError):
    """Raised when a potentially stateful operation is disabled by safety policy."""

# ---- 重试装饰器 ----

def retry(max_attempts=3, delay=0.3, backoff=2.0, exceptions=(Exception,)):
    """带指数退避的重试装饰器。"""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            wait = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        time.sleep(wait)
                        wait *= backoff
            raise last_exc
        return wrapper
    return decorator


# ---- 超时保护 ----

MAX_SETTING_RETRIES = 20  # while 循环最大迭代次数，防止无限循环
DEVICE_TITLE_RE = re.compile(r'^[A-Za-z][\w-]+-\d+$')


class DeviceHandle:
    """封装单个 USB 测试仪设备的 UI 元素和操作。"""

    def __init__(self, name, device_window):
        self.name = name
        self.device_window = device_window
        self.lock = threading.Lock()  # 设备级锁，保护该设备的所有 UI 操作
        self.control = None
        self.create_button = None
        self.start_button = None
        self.pause_button = None
        self.stop_button = None
        self.value_wd = None
        self.value_p = None
        self.vbus_pane = None
        self.ibus_pane = None

    def initialize_interfaces(self):
        self.control = self.device_window.child_window(title="控制:")
        p = self.control.parent()
        children = p.children()
        if len(children) >= 5:
            self.create_button, self.start_button, self.pause_button, self.stop_button = (
                children[1], children[2], children[3], children[4]
            )
        else:
            raise Exception(f"[{self.name}] Could not find all control buttons")
        try:
            self.value_wd = self.device_window.child_window(title="NRG", control_type="TreeItem")
            self.value_p = self.value_wd.parent()
        except Exception:
            self.value_wd = None
            cumulative_wd = self.device_window.child_window(title="累计值", control_type="TreeItem")
            self.value_p = cumulative_wd.parent()
            print(f"[{self.name}] NRG tree item not available yet; will look it up when reading.")
        self.vbus_pane = self.device_window.child_window(title="VBUS:").parent()
        self.ibus_pane = self.device_window.child_window(title="IBUS:").parent()
        print(f"[{self.name}] Interfaces initialized successfully.")

    @staticmethod
    def _toggle_checkbox(checkbox, target_value, label, name, ensure_foreground_fn):
        """安全切换 checkbox 到目标值，带超时保护。"""
        for i in range(MAX_SETTING_RETRIES):
            if checkbox.window_text() == target_value:
                return
            print(f"[{name}] Setting {label} to {target_value}. (attempt {i+1})")
            ensure_foreground_fn()
            try:
                checkbox.invoke()
                time.sleep(0.1)
                if checkbox.window_text() == target_value:
                    return
            except Exception:
                pass
            checkbox.click_input()
            time.sleep(0.1)
        if checkbox.window_text() != target_value:
            raise TimeoutError(f"[{name}] Failed to set {label} to {target_value} after {MAX_SETTING_RETRIES} attempts")

    @staticmethod
    def _find_table_cell(group, row_title, column_title):
        """Find a Qt table cell by visible row title and column header."""
        descendants = group.descendants()
        headers = [
            cell.window_text()
            for cell in descendants
            if getattr(cell.element_info, "control_type", None) == "Header"
        ]
        if column_title not in headers:
            raise LookupError(f"Column '{column_title}' not found. Headers: {headers}")

        data_items = [
            cell
            for cell in descendants
            if getattr(cell.element_info, "control_type", None) == "DataItem"
        ]
        column_count = len(headers)
        column_index = headers.index(column_title)
        if column_count == 0:
            raise LookupError("No table headers found")

        for row_start in range(0, len(data_items), column_count):
            row = data_items[row_start:row_start + column_count]
            if len(row) < column_count:
                continue
            if row[0].window_text() == row_title:
                return row[column_index]
        raise LookupError(f"Row '{row_title}' not found")

    def initialize_settings(self, ensure_foreground_fn):
        dw = self.device_window
        sampling_rate_slider = dw.child_window(control_type="Slider")
        for _ in range(MAX_SETTING_RETRIES):
            if sampling_rate_slider.value() == 6:
                break
            print(f"[{self.name}] Setting sampling rate to 6.")
            sampling_rate_slider.set_value(6)
        else:
            raise TimeoutError(f"[{self.name}] Failed to set sampling rate after {MAX_SETTING_RETRIES} attempts")
        print(f"[{self.name}] Sampling rate set to 6.")

        ensure_foreground_fn()

        main_table = dw.child_window(title="主要记录项", control_type="Group")

        display_vbus_checkbox = self._find_table_cell(main_table, "VBUS", "显示")
        self._toggle_checkbox(display_vbus_checkbox, 'true', 'VBUS display', self.name, ensure_foreground_fn)
        print(f"[{self.name}] VBUS display checkbox set to true.")

        display_ibus_checkbox = self._find_table_cell(main_table, "IBUS", "显示")
        self._toggle_checkbox(display_ibus_checkbox, 'true', 'IBUS display', self.name, ensure_foreground_fn)
        print(f"[{self.name}] IBUS display checkbox set to true.")

        ensure_foreground_fn()

        aux_table = dw.child_window(title="辅助记录项", control_type="Group")
        nrg_record_checkbox = self._find_table_cell(aux_table, "NRG", "记录")
        self._toggle_checkbox(nrg_record_checkbox, 'true', 'NRG record', self.name, ensure_foreground_fn)
        print(f"[{self.name}] NRG record checkbox set to true.")

        nrg_display_checkbox = self._find_table_cell(aux_table, "NRG", "显示")
        self._toggle_checkbox(nrg_display_checkbox, 'true', 'NRG display', self.name, ensure_foreground_fn)
        print(f"[{self.name}] NRG display checkbox set to true.")

        ensure_foreground_fn()
        assert sampling_rate_slider.value() == 6, f"[{self.name}] Sampling rate slider value is incorrect."
        assert display_vbus_checkbox.window_text() == 'true', f"[{self.name}] VBUS display checkbox value is incorrect."
        assert display_ibus_checkbox.window_text() == 'true', f"[{self.name}] IBUS display checkbox value is incorrect."
        assert nrg_record_checkbox.window_text() == 'true', f"[{self.name}] NRG record checkbox value is incorrect."
        assert nrg_display_checkbox.window_text() == 'true', f"[{self.name}] NRG display checkbox value is incorrect."
        print(f"[{self.name}] All settings verified successfully.")

    @retry(max_attempts=2, delay=0.2)
    def click_create(self):
        self.create_button.click()
        print(f"[{self.name}] Create button clicked.")

    @retry(max_attempts=2, delay=0.2)
    def click_start(self):
        self.start_button.click()
        print(f"[{self.name}] Start button clicked.")

    @retry(max_attempts=2, delay=0.2)
    def click_pause(self):
        self.pause_button.click()
        print(f"[{self.name}] Pause button clicked.")

    @retry(max_attempts=2, delay=0.2)
    def click_stop(self):
        self.stop_button.click()
        print(f"[{self.name}] Stop button clicked.")

    @retry(max_attempts=3, delay=0.2)
    def get_last_value_item(self):
        descendants = self.value_p.descendants()
        for idx, item in enumerate(descendants[:-1]):
            if item.window_text() == "NRG":
                value = descendants[idx + 1].window_text()
                print(f"[{self.name}] NRG value item: {value}")
                return value
        if descendants:
            last_item = descendants[-1]
            print(f"[{self.name}] NRG item not found, last value item: {last_item.window_text()}")
            return last_item.window_text()
        raise Exception(f"[{self.name}] No items found in value_p")

    @retry(max_attempts=3, delay=0.2)
    def get_vbus_value(self):
        descendants = self.vbus_pane.descendants()
        if descendants:
            val = descendants[1].window_text()
            print(f"[{self.name}] VBUS value: {val}")
            return val
        raise Exception(f"[{self.name}] No items found in vbus_pane")

    @retry(max_attempts=3, delay=0.2)
    def get_ibus_value(self):
        descendants = self.ibus_pane.descendants()
        if descendants:
            val = descendants[1].window_text()
            print(f"[{self.name}] IBUS value: {val}")
            return val
        raise Exception(f"[{self.name}] No items found in ibus_pane")


class EnergyControlImpl:
    """
    USB 测试仪控制器，支持多设备管理。

    用法一（单设备，向后兼容）:
        ctrl = EnergyControlImpl(product_serial=6297)

    用法二（多设备）:
        devices = [
            {"title": "FNIRSI_C1-6297"},
            {"title": "FNB-58-69717"},
        ]
        ctrl = EnergyControlImpl(devices=devices)
        ctrl.click_start_button("FNIRSI_C1-6297")
        ctrl.click_start_button("FNB-58-69717")
    """

    def __init__(
        self,
        product_serial=None,
        exe_path=r"UsbMeter_V0_0_6\UsbMeter.exe",
        devices=None,
        backend_name=BACKEND_UIA,
        safety_mode=SAFETY_MODE_SAFE_READONLY,
        allow_unsafe_control=False,
        hid_path=None,
        hid_serial=None,
        hid_allow_all=False,
        hid_discovery_only=False,
    ):
        self.exe_path = os.path.abspath(exe_path)
        self.logger = logging.getLogger(__name__)
        self._gui_lock = threading.RLock()  # 全局 GUI 锁，序列化所有窗口操作
        self._devices = {}  # name -> DeviceHandle
        self._default_device_name = None
        self._hid_manager = None
        self._hid_candidates = []
        self.window_handle = None
        self.app = None
        self.main_window = None
        self.c1_wd = None
        self.backend_name = backend_name
        self.safety_mode = safety_mode
        self.allow_unsafe_control = bool(allow_unsafe_control)
        self.hid_path = hid_path
        self.hid_serial = hid_serial
        self.hid_allow_all = bool(hid_allow_all)
        self.hid_discovery_only = bool(hid_discovery_only)
        self._validate_runtime_mode()
        try:
            if self.backend_name == BACKEND_UIA:
                self._initialize_uia_backend(product_serial, devices)
            else:
                self._initialize_hid_backend(product_serial, devices)
            print(f"Initialization successful. Devices: {list(self._devices.keys())}")
        except Exception as e:
            print(f"Initialization failed: {e}")
            if self._hid_manager is not None:
                try:
                    self._hid_manager.close()
                except Exception:
                    pass
            raise

    def _validate_runtime_mode(self):
        if self.backend_name not in {BACKEND_UIA, BACKEND_HID}:
            raise ValueError(f"Unsupported backend_name: {self.backend_name}")
        if self.safety_mode not in {
            SAFETY_MODE_SAFE_READONLY,
            SAFETY_MODE_FULL_COMPAT_RESEARCH,
        }:
            raise ValueError(f"Unsupported safety_mode: {self.safety_mode}")
        if self.backend_name == BACKEND_HID:
            if self.safety_mode != SAFETY_MODE_SAFE_READONLY:
                raise ValueError(
                    "BACKEND_HID only supports safe_readonly in direct-HID readonly v1"
                )
            if self.allow_unsafe_control:
                raise ValueError(
                    "BACKEND_HID does not support allow_unsafe_control in direct-HID readonly v1"
                )
            if getattr(self, "hid_allow_all", False) and os.environ.get(UNSAFE_ALLOW_ALL_ENV) != "1":
                raise ValueError(
                    "BACKEND_HID hid_allow_all is disabled after multi-device stress "
                    "testing triggered USB descriptor failures. Use hid_path/hid_serial "
                    f"for one explicit device, or set {UNSAFE_ALLOW_ALL_ENV}=1 only "
                    "for isolated lab diagnostics."
                )

    def _initialize_uia_backend(self, product_serial, devices):
        self._ensure_process_running()
        print("UsbMeter.exe process is running.")
        self.window_handle = self._find_window_handle()
        print(f"Window handle found: {self.window_handle}")
        self.app = Application(backend='uia').connect(handle=self.window_handle)
        self.main_window = self.app.window(handle=self.window_handle)
        self.main_window.set_focus()

        device_configs = self._build_device_configs(product_serial, devices)
        self._maximize_window()
        self._ensure_window_foreground()
        time.sleep(0.5)

        if device_configs is None:
            device_configs = self._discover_devices_from_gui()
            if not device_configs:
                raise RuntimeError("No devices discovered from GUI. Please check USB connections.")

        for cfg in device_configs:
            title = cfg["title"]
            print(f"Initializing device: {title}")
            try:
                self._activate_device_panel(title)
                device_window = self.main_window.child_window(title=title, control_type="Window")
                handle = DeviceHandle(name=title, device_window=device_window)
                handle.initialize_settings(self._ensure_window_foreground)
                handle.initialize_interfaces()
                self._devices[title] = handle
            except Exception as e:
                print(f"[{title}] Initialization skipped: {e}")

        if not self._devices:
            raise RuntimeError("No devices initialized successfully.")

        if self._default_device_name is None and self._devices:
            self._default_device_name = next(iter(self._devices))

        default = self._get_default_device()
        if default:
            self.c1_wd = default.device_window

    def _initialize_hid_backend(self, product_serial, devices):
        device_configs = self._build_device_configs(product_serial, devices)
        requested_titles = None
        if device_configs is not None:
            requested_titles = [cfg["title"] for cfg in device_configs]
        self._hid_manager = HidBackendManager(
            requested_titles=requested_titles,
            logger=self.logger,
            hid_path=self.hid_path,
            hid_serial=self.hid_serial,
            allow_all=self.hid_allow_all,
            discovery_only=self.hid_discovery_only,
        )
        self._devices = self._hid_manager.discover_devices()
        self._hid_candidates = self._hid_manager.list_candidates()
        if not self._devices:
            mode = "discovery-only" if self.hid_discovery_only else "waiting"
            print(
                f"HID {mode} mode: candidates found "
                f"{len(self._hid_candidates)}, no device opened."
            )
            return
        self._default_device_name = next(iter(self._devices))

    def supports_control_operations(self):
        return (
            getattr(self, "backend_name", BACKEND_UIA) == BACKEND_UIA
            and getattr(self, "safety_mode", SAFETY_MODE_SAFE_READONLY)
            == SAFETY_MODE_FULL_COMPAT_RESEARCH
            and bool(getattr(self, "allow_unsafe_control", False))
        )

    def get_capabilities(self):
        control_allowed = self.supports_control_operations()
        read_allowed = not (
            getattr(self, "backend_name", BACKEND_UIA) == BACKEND_HID
            and not bool(getattr(self, "_devices", {}))
        )
        return {
            "read_vbus": read_allowed,
            "read_ibus": read_allowed,
            "read_nrg": read_allowed,
            "list_devices": True,
            "create_session": control_allowed,
            "start_session": control_allowed,
            "pause_session": control_allowed,
            "stop_session": control_allowed,
        }

    def get_backend_status(self):
        status = {
            "backend_name": getattr(self, "backend_name", BACKEND_UIA),
            "safety_mode": getattr(self, "safety_mode", SAFETY_MODE_SAFE_READONLY),
            "allow_unsafe_control": bool(getattr(self, "allow_unsafe_control", False)),
            "control_operations_allowed": self.supports_control_operations(),
            "capabilities": self.get_capabilities(),
        }
        if getattr(self, "backend_name", BACKEND_UIA) == BACKEND_HID:
            status["device_health"] = self.get_all_device_health()
            status["hid_candidates"] = list(getattr(self, "_hid_candidates", []))
            if self._hid_manager is not None:
                status["hid_startup_errors"] = self._hid_manager.get_startup_errors()
                status["hid_candidate_filter_warnings"] = (
                    self._hid_manager.get_candidate_filter_warnings()
                )
            else:
                status["hid_startup_errors"] = []
                status["hid_candidate_filter_warnings"] = []
            status["hid_discovery_only"] = bool(getattr(self, "hid_discovery_only", False))
            status["hid_waiting"] = (
                not bool(getattr(self, "hid_discovery_only", False))
                and not bool(getattr(self, "_devices", {}))
            )
            status["hid_allow_all"] = bool(getattr(self, "hid_allow_all", False))
            status["hid_path"] = getattr(self, "hid_path", None)
            status["hid_serial"] = getattr(self, "hid_serial", None)
        return status

    def refresh_hid_devices(self, restart_device_names=None):
        if getattr(self, "backend_name", BACKEND_UIA) != BACKEND_HID:
            return {"added": [], "removed": [], "restarted": [], "devices": self.device_names}
        if self._hid_manager is None:
            return {"added": [], "removed": [], "restarted": [], "devices": self.device_names}
        restart_set = set(restart_device_names or [])
        with self._gui_lock:
            before = set(self._devices)
            self._devices = self._hid_manager.refresh_devices(restart_set)
            self._hid_candidates = [candidate.as_dict() for candidate in self._hid_manager.candidates]
            after = set(self._devices)
            if self._default_device_name not in self._devices:
                self._default_device_name = next(iter(self._devices), None)
            return {
                "added": sorted(after - before),
                "removed": sorted(before - after),
                "restarted": sorted(restart_set & after),
                "devices": list(self._devices),
            }

    def _ensure_control_allowed(self, operation_name):
        if self.supports_control_operations():
            return
        mode = getattr(self, "safety_mode", SAFETY_MODE_SAFE_READONLY)
        raise UnsafeOperationBlockedError(
            f"Operation '{operation_name}' is blocked in {mode} mode. "
            "Restart with --safety-mode full_compat_research --allow-unsafe-control "
            "only after the protocol path is fully validated."
        )

    @staticmethod
    def _build_device_configs(product_serial, devices):
        """根据参数构建设备配置列表。返回 None 表示需要自动发现。"""
        if devices is not None:
            return devices
        if product_serial is not None:
            return [{"title": f"FNIRSI_C1-{product_serial}"}]
        # 既没有 product_serial 也没有 devices，触发自动发现
        return None

    def _discover_devices_from_gui(self):
        """从 UsbMeter GUI 自动发现所有已连接的设备。

        通过遍历主窗口中 control_type='Window' 的子控件，
        匹配形如 'FNB-58-86360'、'FNIRSI_C1-6297' 等设备面板标题。
        """
        discovered = []
        seen = set()
        try:
            for item in self.main_window.descendants(control_type="TabItem"):
                try:
                    title = item.window_text()
                    if not title or not DEVICE_TITLE_RE.match(title) or title in seen:
                        continue
                    seen.add(title)
                    discovered.append({"title": title})
                    print(f"Auto-discovered device tab: {title}")
                except Exception:
                    continue
            windows = self.main_window.descendants(control_type="Window")
            for win in windows:
                try:
                    title = win.window_text()
                    if title and DEVICE_TITLE_RE.match(title) and title not in seen:
                        seen.add(title)
                        discovered.append({"title": title})
                        print(f"Auto-discovered device window: {title}")
                except Exception:
                    continue
        except Exception as e:
            print(f"Failed to discover devices from GUI: {e}")
        print(f"Auto-discovered {len(discovered)} device(s): {[d['title'] for d in discovered]}")
        return discovered

    def _activate_device_panel(self, title):
        """Bring the UsbMeter window forward and select a device tab if present."""
        self._ensure_window_foreground()
        for item in self.main_window.descendants(control_type="TabItem"):
            try:
                if item.window_text() == title:
                    item.click_input()
                    time.sleep(0.5)
                    break
            except Exception as e:
                print(f"[{title}] Failed to click device tab: {e}")
        device_window = self.main_window.child_window(title=title, control_type="Window")
        rect = device_window.rectangle()
        main_rect = self.main_window.rectangle()
        if not self._is_rect_interactable(rect, main_rect):
            raise RuntimeError(f"Device panel is not interactable after activation: rect={rect}")

    @staticmethod
    def _is_rect_interactable(rect, container_rect, min_visible_ratio=0.5):
        """Return True when a UI element is mostly inside the main window."""
        left = max(rect.left, container_rect.left)
        top = max(rect.top, container_rect.top)
        right = min(rect.right, container_rect.right)
        bottom = min(rect.bottom, container_rect.bottom)
        visible_width = max(0, right - left)
        visible_height = max(0, bottom - top)
        rect_width = max(0, rect.right - rect.left)
        rect_height = max(0, rect.bottom - rect.top)
        rect_area = rect_width * rect_height
        if rect_area == 0:
            return False
        visible_ratio = (visible_width * visible_height) / rect_area
        return visible_ratio >= min_visible_ratio

    # ---- 设备查询 ----

    @property
    def device_names(self):
        """返回所有已注册设备的名称列表。"""
        return list(self._devices.keys())

    def get_device(self, device_name=None):
        """获取指定设备的 DeviceHandle，不指定则返回默认设备。"""
        name = device_name or self._default_device_name
        if name not in self._devices:
            raise KeyError(f"Device '{name}' not found. Available: {self.device_names}")
        return self._devices[name]

    def _get_default_device(self):
        if self._default_device_name and self._default_device_name in self._devices:
            return self._devices[self._default_device_name]
        return None

    # ---- 窗口管理（全局，非设备级别）----

    def _maximize_window(self):
        try:
            win32gui.ShowWindow(self.window_handle, win32con.SW_MAXIMIZE)
            print("Window maximized successfully")
        except Exception as e:
            print(f"Failed to maximize window: {e}")

    def _center_window(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)
            rect = win32gui.GetWindowRect(self.window_handle)
            window_width = rect[2] - rect[0]
            window_height = rect[3] - rect[1]
            x = (screen_width - window_width) // 2
            y = (screen_height - window_height) // 2
            win32gui.SetWindowPos(self.window_handle, win32con.HWND_TOP, x, y,
                                  window_width, window_height,
                                  win32con.SWP_SHOWWINDOW)
            print(f"Window centered at position ({x}, {y})")
        except Exception as e:
            print(f"Failed to center window: {e}")

    def _ensure_window_foreground(self):
        try:
            if win32gui.GetForegroundWindow() == self.window_handle:
                return
            win32gui.ShowWindow(self.window_handle, win32con.SW_RESTORE)
            time.sleep(0.1)
            win32gui.ShowWindow(self.window_handle, win32con.SW_MAXIMIZE)
            win32gui.SetWindowPos(
                self.window_handle,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            win32gui.SetWindowPos(
                self.window_handle,
                win32con.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            win32gui.SetForegroundWindow(self.window_handle)
            time.sleep(0.1)
        except Exception as e:
            print(f"Failed to bring window to foreground: {e}")

    def _ensure_process_running(self):
        try:
            self._wait_for_window_handle(timeout_sec=3, poll_sec=0.5)
            return
        except Exception:
            pass

        if self._has_usbmeter_process():
            print("UsbMeter.exe process exists but window is not ready. Waiting for window to appear.")
            try:
                self._wait_for_window_handle(timeout_sec=10, poll_sec=1)
                return
            except Exception:
                print("UsbMeter.exe window did not appear in time. Restarting the process.")
                self.kill()
                self._wait_for_process_exit(timeout_sec=10, poll_sec=0.5)

        print("UsbMeter.exe is not running. Starting the process.")
        subprocess.Popen(self.exe_path, shell=True)
        self._wait_for_window_handle(timeout_sec=30, poll_sec=1)

    def _iter_usbmeter_processes(self):
        for proc in psutil.process_iter():
            try:
                if proc.name() == "UsbMeter.exe":
                    yield proc
            except Exception:
                continue

    def _has_usbmeter_process(self):
        return any(True for _ in self._iter_usbmeter_processes())

    def _wait_for_process_exit(self, timeout_sec=10, poll_sec=0.5):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if not self._has_usbmeter_process():
                return
            time.sleep(poll_sec)
        raise TimeoutError("UsbMeter.exe process did not exit within timeout")

    def _wait_for_window_handle(self, timeout_sec=30, poll_sec=1):
        deadline = time.time() + timeout_sec
        last_exc = None
        while time.time() < deadline:
            try:
                return self._find_window_handle()
            except Exception as e:
                last_exc = e
                print("Waiting for UsbMeter.exe window to appear.")
                time.sleep(poll_sec)
        raise TimeoutError("UsbMeter.exe window did not appear within timeout") from last_exc

    def _find_window_handle(self):
        try:
            window_handle = []
            def winEnumHandler(hwnd, ctx):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if 'USB测试仪' in title:
                        ctx.append(hwnd)
            win32gui.EnumWindows(winEnumHandler, window_handle)
            if window_handle:
                return window_handle[0]
            else:
                raise Exception("USB测试仪 window not found")
        except Exception as e:
            print(f"Failed to find window handle: {e}")
            raise

    # ---- 设备操作（支持 device_name 参数，线程安全）----

    def click_create_button(self, device_name=None):
        try:
            self._ensure_control_allowed("create")
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                self._ensure_window_foreground()
                dev.click_create()
        except Exception as e:
            print(f"Failed to click create button: {e}")
            raise

    def click_start_button(self, device_name=None):
        try:
            self._ensure_control_allowed("start")
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                self._ensure_window_foreground()
                dev.click_start()
        except Exception as e:
            print(f"Failed to click start button: {e}")
            raise

    def click_pause_button(self, device_name=None):
        try:
            self._ensure_control_allowed("pause")
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                self._ensure_window_foreground()
                dev.click_pause()
        except Exception as e:
            print(f"Failed to click pause button: {e}")
            raise

    def click_stop_button(self, device_name=None):
        try:
            self._ensure_control_allowed("stop")
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                self._ensure_window_foreground()
                dev.click_stop()
        except Exception as e:
            print(f"Failed to click stop button: {e}")
            raise

    def get_last_value_item(self, device_name=None):
        try:
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                return dev.get_last_value_item()
        except Exception as e:
            print(f"Failed to get last value item: {e}")
            raise

    def get_nrg_wh_value(self, device_name=None):
        try:
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                if hasattr(dev, "get_nrg_wh_value"):
                    return float(dev.get_nrg_wh_value())
                return float(dev.get_last_value_item().replace("Wh", "").strip())
        except Exception as e:
            print(f"Failed to get NRG Wh value: {e}")
            raise

    def get_vbus_value(self, device_name=None):
        try:
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                return dev.get_vbus_value()
        except Exception as e:
            print(f"Failed to get VBUS value: {e}")
            raise

    def get_ibus_value(self, device_name=None):
        try:
            dev = self.get_device(device_name)
            with self._gui_lock, dev.lock:
                return dev.get_ibus_value()
        except Exception as e:
            print(f"Failed to get IBUS value: {e}")
            raise

    # ---- 批量操作（同时控制所有设备）----

    def click_create_all(self):
        self._ensure_control_allowed("create")
        with self._gui_lock:
            self._ensure_window_foreground()
            for dev in self._devices.values():
                with dev.lock:
                    dev.click_create()

    def click_start_all(self):
        self._ensure_control_allowed("start")
        with self._gui_lock:
            self._ensure_window_foreground()
            for dev in self._devices.values():
                with dev.lock:
                    dev.click_start()

    def click_pause_all(self):
        self._ensure_control_allowed("pause")
        with self._gui_lock:
            self._ensure_window_foreground()
            for dev in self._devices.values():
                with dev.lock:
                    dev.click_pause()

    def click_stop_all(self):
        self._ensure_control_allowed("stop")
        with self._gui_lock:
            self._ensure_window_foreground()
            for dev in self._devices.values():
                with dev.lock:
                    dev.click_stop()

    def get_all_values(self):
        """获取所有设备的 VBUS、IBUS、NRG 值，返回 {device_name: {vbus, ibus, nrg}} 字典。"""
        result = {}
        with self._gui_lock:
            for name, dev in self._devices.items():
                try:
                    with dev.lock:
                        result[name] = {
                            "vbus": dev.get_vbus_value(),
                            "ibus": dev.get_ibus_value(),
                            "nrg": dev.get_last_value_item(),
                        }
                except Exception as e:
                    print(f"[{name}] Failed to get values: {e}")
                    result[name] = {"error": str(e)}
        return result

    def get_device_health(self, device_name=None):
        dev = self.get_device(device_name)
        with self._gui_lock, dev.lock:
            if hasattr(dev, "get_health"):
                return dev.get_health()
            return {"connected": True, "title": dev.name, "backend_name": self.backend_name}

    def get_all_device_health(self):
        result = {}
        with self._gui_lock:
            for name, dev in self._devices.items():
                try:
                    with dev.lock:
                        if hasattr(dev, "get_health"):
                            result[name] = dev.get_health()
                        else:
                            result[name] = {
                                "connected": True,
                                "title": dev.name,
                                "backend_name": self.backend_name,
                            }
                except Exception as e:
                    result[name] = {"connected": False, "error": str(e)}
        return result

    def kill(self):
        with self._gui_lock:
            if getattr(self, "backend_name", BACKEND_UIA) == BACKEND_HID:
                if self._hid_manager is not None:
                    try:
                        self._hid_manager.close()
                    finally:
                        self._hid_manager = None
                        self._devices = {}
                return
            try:
                for proc in self._iter_usbmeter_processes():
                    if proc.name() == "UsbMeter.exe":
                        proc.kill()
                        print("UsbMeter.exe process killed.")
            except Exception as e:
                print(f"Failed to kill process: {e}")

# Example usage:
#
# 单设备（向后兼容）:
# control = EnergyControlImpl(product_serial=6297)
# control.click_create_button()
# control.click_start_button()
# print(control.get_last_value_item())
# print(f"VBUS: {control.get_vbus_value()}, IBUS: {control.get_ibus_value()}")
#
# 多设备:
# devices = [
#     {"title": "FNIRSI_C1-6297"},
#     {"title": "FNB-58-69717"},
# ]
# control = EnergyControlImpl(devices=devices)
# print(f"Devices: {control.device_names}")
#
# # 操作单个设备
# control.click_create_button("FNIRSI_C1-6297")
# control.click_start_button("FNIRSI_C1-6297")
# print(control.get_vbus_value("FNB-58-69717"))
#
# # 批量操作所有设备
# control.click_start_all()
# values = control.get_all_values()
# for name, v in values.items():
#     print(f"{name}: {v}")
