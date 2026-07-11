from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
import time
import zlib
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass


FRAME_LEN = 64
REPORT_LEN = 65
APP_REPORT_ID = 0x00
AA_HEADER = 0xAA
CMD_INFO = 0x03
CMD_LIVE = 0x04
CMD_INIT = 0x81
CMD_START_STREAM = 0x82
CMD_KEEPALIVE = 0x83
TARGET_SAMPLE_RATE_SPS = 20.0
TARGET_SAMPLE_INTERVAL_MS = 50
RAW_SAMPLE_RATE_SPS = 100.0
RAW_SAMPLE_INTERVAL_SEC = 1.0 / RAW_SAMPLE_RATE_SPS
TARGET_KEEPALIVE_SEC = 1.0
STARTUP_INFO_TIMEOUT_SEC = 3.0
STARTUP_INFO_RETRY_WAIT_SEC = 1.0
STARTUP_STREAM_REPEAT = 2
STARTUP_STREAM_GAP_SEC = 0.01
THREAD_JOIN_TIMEOUT_SEC = 0.5
UNSAFE_ALLOW_ALL_ENV = "USBMETER_HID_ALLOW_ALL_UNSAFE"
GLOBAL_WRITE_MIN_GAP_SEC = 0.02
DEVICE_STARTUP_COOLDOWN_SEC = 0.3
ALLOW_ALL_MAX_UNSAFE_CANDIDATES = 8

OFFICIAL_MODE2_VID_PIDS = frozenset(
    {
        (0x0483, 0x0039),
        (0x0483, 0x003A),
        (0x0483, 0x003B),
        (0x2E3C, 0x0049),
        (0x2E3C, 0x5558),
    }
)
DEFAULT_DIRECT_HID_VID_PIDS = frozenset({(0x2E3C, 0x5558)})
PREFERRED_MI03_VID_PIDS = frozenset({(0x2E3C, 0x5558)})

MODEL_CODE_TO_NAME = {
    8: "FNC-88",
    9: "FNIRSI_C1",
    38: "FNB-38",
    48: "FNB-48",
    49: "FNB-48S",
    58: "FNB-58",
}


def crc8_39(data63: bytes) -> int:
    crc = 0
    for b in data63:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x39) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    if len(payload) > 61:
        raise ValueError(f"payload too large for UsbMeter frame: {len(payload)}")
    frame = bytearray(FRAME_LEN)
    frame[0] = AA_HEADER
    frame[1] = cmd & 0xFF
    frame[2 : 2 + len(payload)] = payload
    frame[63] = crc8_39(frame[:63])
    return bytes(frame)


@dataclass(slots=True)
class UsbMeterRealtimeSample:
    vbus: float
    ibus: float
    dp: float
    dm: float
    pbus: float
    impd: float
    temp: float
    received_monotonic: float


@dataclass(slots=True)
class UsbMeterDeviceInfo:
    path: str
    hid_serial: str | None = None
    product_string: str | None = None
    manufacturer_string: str | None = None
    vid: int | None = None
    pid: int | None = None
    run_mode: int | None = None
    model_code: int | None = None
    model_name: str | None = None
    lab_sn: int | None = None
    app_version_raw: int | None = None
    dword3: int | None = None

    @property
    def title(self) -> str:
        if self.model_name and self.lab_sn is not None:
            return f"{self.model_name}-{self.lab_sn}"
        if self.product_string:
            return self.product_string
        return self.path


@dataclass(slots=True)
class HidDeviceCandidate:
    path: str
    vid: int
    pid: int
    hid_serial: str | None = None
    device_instance_id: str | None = None
    parent_instance_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "vid": self.vid,
            "pid": self.pid,
            "hid_serial": self.hid_serial,
            "device_instance_id": self.device_instance_id,
            "parent_instance_ids": list(self.parent_instance_ids),
        }


class TwentySpsEnergyAccumulator:
    def __init__(self, sample_interval_ms: int = TARGET_SAMPLE_INTERVAL_MS):
        self.sample_interval_ms = sample_interval_ms
        self.cap_ah = 0.0
        self.nrg_wh = 0.0
        self.prev_ibus = 0.0

    def add_tick(self, ibus_now: float, live_vbus_now: float) -> tuple[float, float]:
        dt_hours = self.sample_interval_ms / 1000.0 / 3600.0
        self.cap_ah += ((ibus_now + self.prev_ibus) * 0.5) * dt_hours
        self.nrg_wh += live_vbus_now * ibus_now * 0.25 * dt_hours
        self.prev_ibus = ibus_now
        return self.cap_ah, self.nrg_wh


def parse_aa03(frame: bytes) -> dict[str, int | str]:
    if len(frame) != FRAME_LEN or frame[0] != AA_HEADER or frame[1] != CMD_INFO:
        raise ValueError("not an aa03 frame")
    dword0 = int.from_bytes(frame[2:6], "little")
    dword1 = int.from_bytes(frame[6:10], "little")
    dword2 = int.from_bytes(frame[10:14], "little")
    dword3 = int.from_bytes(frame[14:18], "little")
    model_code = dword1 & 0xFFFF
    model_name = MODEL_CODE_TO_NAME.get(model_code, f"MODEL-{model_code}")
    return {
        "run_mode": dword0 & 0xFF,
        "model_code": model_code,
        "model_name": model_name,
        "app_version_raw": (dword1 >> 16) & 0xFFFF,
        "lab_sn": dword2,
        "dword3": dword3,
    }


def _parse_signed_temp(sign_flag: int, raw_temp: int) -> float:
    temp = raw_temp / 10.0
    return -temp if sign_flag == 0 else temp


def parse_aa04_samples(frame: bytes, received_monotonic: float | None = None) -> list[UsbMeterRealtimeSample]:
    if len(frame) != FRAME_LEN or frame[0] != AA_HEADER or frame[1] != CMD_LIVE:
        raise ValueError("not an aa04 frame")
    ts = received_monotonic if received_monotonic is not None else time.monotonic()
    samples: list[UsbMeterRealtimeSample] = []
    payload = frame[2:62]
    chunks = [payload[off : off + 15] for off in range(0, len(payload), 15)]
    chunks = [chunk for chunk in chunks if len(chunk) >= 15]
    first_ts = ts - (max(0, len(chunks) - 1) * RAW_SAMPLE_INTERVAL_SEC)
    for index, chunk in enumerate(chunks):
        vbus = int.from_bytes(chunk[0:4], "little") / 100000.0
        ibus = int.from_bytes(chunk[4:8], "little") / 100000.0
        dp = int.from_bytes(chunk[8:10], "little") / 1000.0
        dm = int.from_bytes(chunk[10:12], "little") / 1000.0
        sign_flag = chunk[12]
        raw_temp = int.from_bytes(chunk[13:15], "little")
        temp = _parse_signed_temp(sign_flag, raw_temp)
        pbus = vbus * ibus
        impd = (vbus / ibus) if ibus else 0.0
        samples.append(
            UsbMeterRealtimeSample(
                vbus=vbus,
                ibus=ibus,
                dp=dp,
                dm=dm,
                pbus=pbus,
                impd=impd,
                temp=temp,
                received_monotonic=first_ts + (index * RAW_SAMPLE_INTERVAL_SEC),
            )
        )
    return samples


def format_vbus(vbus: float) -> str:
    return f"{vbus:.5f}"


def format_ibus(ibus: float) -> str:
    return f"{ibus:.5f}"


def format_nrg_wh(nrg_wh: float) -> str:
    return f"{nrg_wh:.4f} Wh"


GUID = ctypes.c_byte * 16
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ULONG_PTR),
    ]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ULONG_PTR),
    ]


class _SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", wintypes.WCHAR * 1024),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    ctypes.POINTER(_OVERLAPPED),
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    ctypes.POINTER(_OVERLAPPED),
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.CreateEventW.argtypes = [
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
kernel32.GetOverlappedResult.restype = wintypes.BOOL

setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DETAIL_DATA_W),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetHidGuid.restype = None
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetSerialNumberString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetSerialNumberString.restype = wintypes.BOOL
hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetManufacturerString.restype = wintypes.BOOL
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetProductString.restype = wintypes.BOOL
hid.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
hid.HidD_SetNumInputBuffers.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.LPVOID)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [wintypes.LPVOID]
hid.HidD_FreePreparsedData.restype = wintypes.BOOL
hid.HidP_GetCaps.argtypes = [wintypes.LPVOID, ctypes.POINTER(_HIDP_CAPS)]
hid.HidP_GetCaps.restype = ctypes.c_long

cfgmgr32.CM_Get_Device_IDW.argtypes = [
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]
cfgmgr32.CM_Get_Device_IDW.restype = wintypes.DWORD
cfgmgr32.CM_Get_Parent.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
    wintypes.ULONG,
]
cfgmgr32.CM_Get_Parent.restype = wintypes.DWORD


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_OVERLAPPED = 0x40000000

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
ERROR_NO_MORE_ITEMS = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
CR_SUCCESS = 0

VID_PID_RE = re.compile(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})")
USB_SERIAL_RE = re.compile(r"^usb\\vid_[0-9a-f]{4}&pid_[0-9a-f]{4}\\([^\\]+)$", re.IGNORECASE)


def _candidate_vid_pid_from_path(path: str) -> tuple[int, int] | None:
    m = VID_PID_RE.search(path.lower())
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


def _is_preferred_application_interface(path: str, vid_pid: tuple[int, int]) -> bool:
    if vid_pid not in PREFERRED_MI03_VID_PIDS:
        return True
    return "mi_03" in path.lower()


def _get_device_instance_id(devinst: int) -> str | None:
    buf = ctypes.create_unicode_buffer(1024)
    if cfgmgr32.CM_Get_Device_IDW(devinst, buf, len(buf), 0) != CR_SUCCESS:
        return None
    return buf.value or None


def _get_parent_instance_ids(devinst: int, max_depth: int = 8) -> tuple[str, ...]:
    parent_ids: list[str] = []
    current = wintypes.DWORD(devinst)
    for _ in range(max_depth):
        parent = wintypes.DWORD()
        if cfgmgr32.CM_Get_Parent(ctypes.byref(parent), current.value, 0) != CR_SUCCESS:
            break
        parent_id = _get_device_instance_id(parent.value)
        if parent_id:
            parent_ids.append(parent_id)
        current = parent
    return tuple(parent_ids)


def _extract_usb_serial_from_parent_ids(parent_ids: tuple[str, ...]) -> str | None:
    for parent_id in parent_ids:
        m = USB_SERIAL_RE.match(parent_id)
        if m:
            return m.group(1)
    return None


def enumerate_supported_hid_candidates(
    allowed_vid_pids: frozenset[tuple[int, int]] = DEFAULT_DIRECT_HID_VID_PIDS,
) -> list[HidDeviceCandidate]:
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    dev_info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid),
        None,
        None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if dev_info == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    candidates: list[HidDeviceCandidate] = []
    seen_paths: set[str] = set()
    try:
        index = 0
        while True:
            interface_data = _SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                dev_info,
                None,
                ctypes.byref(guid),
                index,
                ctypes.byref(interface_data),
            )
            if not ok:
                err = ctypes.get_last_error()
                if err == ERROR_NO_MORE_ITEMS:
                    break
                raise ctypes.WinError(err)

            detail = _SP_DEVICE_INTERFACE_DETAIL_DATA_W()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            devinfo_data = _SP_DEVINFO_DATA()
            devinfo_data.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            required = wintypes.DWORD()
            ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info,
                ctypes.byref(interface_data),
                ctypes.byref(detail),
                ctypes.sizeof(detail),
                ctypes.byref(required),
                ctypes.byref(devinfo_data),
            )
            if ok:
                path = detail.DevicePath
                vid_pid = _candidate_vid_pid_from_path(path)
                if (
                    vid_pid in allowed_vid_pids
                    and _is_preferred_application_interface(path, vid_pid)
                    and path not in seen_paths
                ):
                    parent_ids = _get_parent_instance_ids(int(devinfo_data.DevInst))
                    candidates.append(
                        HidDeviceCandidate(
                            path=path,
                            vid=vid_pid[0],
                            pid=vid_pid[1],
                            hid_serial=_extract_usb_serial_from_parent_ids(parent_ids),
                            device_instance_id=_get_device_instance_id(int(devinfo_data.DevInst)),
                            parent_instance_ids=parent_ids,
                        )
                    )
                    seen_paths.add(path)
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev_info)
    return candidates


def enumerate_supported_hid_paths(
    allowed_vid_pids: frozenset[tuple[int, int]] = DEFAULT_DIRECT_HID_VID_PIDS,
) -> list[str]:
    return [candidate.path for candidate in enumerate_supported_hid_candidates(allowed_vid_pids)]


class WindowsHidTransport:
    def __init__(self, path: str):
        self.path = path
        self.handle: int | None = None
        self.input_report_len = FRAME_LEN
        self.output_report_len = REPORT_LEN
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._read_event: int | None = None
        self._write_event: int | None = None
        self._read_ov = _OVERLAPPED()
        self._write_ov = _OVERLAPPED()

    def open(self) -> None:
        if self.handle:
            return
        handle = kernel32.CreateFileW(
            self.path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())

        self.handle = handle
        self._read_event = kernel32.CreateEventW(None, True, False, None)
        self._write_event = kernel32.CreateEventW(None, True, False, None)
        if not self._read_event or not self._write_event:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        self._read_ov.hEvent = self._read_event
        self._write_ov.hEvent = self._write_event
        self._load_report_lengths()
        hid.HidD_SetNumInputBuffers(self.handle, 64)

    def close(self) -> None:
        for h in (self._read_event, self._write_event, self.handle):
            if h:
                kernel32.CloseHandle(h)
        self._read_event = None
        self._write_event = None
        self.handle = None
        self._read_ov = _OVERLAPPED()
        self._write_ov = _OVERLAPPED()

    def cancel_pending_io(self) -> None:
        if not self.handle:
            return
        for ov in (self._read_ov, self._write_ov):
            kernel32.CancelIoEx(self.handle, ctypes.byref(ov))

    def _load_report_lengths(self) -> None:
        if not self.handle:
            return
        preparsed = wintypes.LPVOID()
        if not hid.HidD_GetPreparsedData(self.handle, ctypes.byref(preparsed)):
            return
        try:
            caps = _HIDP_CAPS()
            status = hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
            if status >= 0:
                if caps.InputReportByteLength:
                    self.input_report_len = int(caps.InputReportByteLength)
                if caps.OutputReportByteLength:
                    self.output_report_len = int(caps.OutputReportByteLength)
        finally:
            hid.HidD_FreePreparsedData(preparsed)

    def _query_string(self, fn) -> str | None:
        if not self.handle:
            return None
        buf = ctypes.create_unicode_buffer(256)
        ok = fn(self.handle, buf, ctypes.sizeof(buf))
        if not ok:
            return None
        value = buf.value.strip()
        return value or None

    def fetch_strings(self) -> tuple[str | None, str | None, str | None]:
        return (
            self._query_string(hid.HidD_GetSerialNumberString),
            self._query_string(hid.HidD_GetProductString),
            self._query_string(hid.HidD_GetManufacturerString),
        )

    def fetch_attributes(self) -> tuple[int, int] | None:
        if not self.handle:
            return None
        attrs = _HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(self.handle, ctypes.byref(attrs)):
            return None
        return attrs.VendorID, attrs.ProductID

    def _finish_overlapped(self, ov: _OVERLAPPED, timeout_ms: int) -> int:
        assert self.handle
        wait = kernel32.WaitForSingleObject(ov.hEvent, timeout_ms)
        if wait == WAIT_TIMEOUT:
            kernel32.CancelIoEx(self.handle, ctypes.byref(ov))
            kernel32.WaitForSingleObject(ov.hEvent, 1000)
            transferred = wintypes.DWORD()
            if not kernel32.GetOverlappedResult(
                self.handle,
                ctypes.byref(ov),
                ctypes.byref(transferred),
                False,
            ):
                err = ctypes.get_last_error()
                if err == ERROR_OPERATION_ABORTED:
                    return 0
                raise ctypes.WinError(err)
            return 0
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        transferred = wintypes.DWORD()
        if not kernel32.GetOverlappedResult(
            self.handle,
            ctypes.byref(ov),
            ctypes.byref(transferred),
            False,
        ):
            err = ctypes.get_last_error()
            if err == ERROR_OPERATION_ABORTED:
                return 0
            raise ctypes.WinError(err)
        return int(transferred.value)

    def read_frame(self, timeout_ms: int = 200) -> bytes:
        if not self.handle or not self._read_event:
            raise RuntimeError("transport is not open")
        with self._read_lock:
            kernel32.ResetEvent(self._read_event)
            buf = ctypes.create_string_buffer(self.input_report_len)
            ok = kernel32.ReadFile(
                self.handle,
                buf,
                self.input_report_len,
                None,
                ctypes.byref(self._read_ov),
            )
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise ctypes.WinError(err)
            transferred = self._finish_overlapped(self._read_ov, timeout_ms)
            if transferred <= 0:
                return b""
            raw = bytes(buf.raw[:transferred])
            if not raw:
                return b""
            if raw[0] == APP_REPORT_ID and len(raw) >= FRAME_LEN + 1:
                return raw[1 : 1 + FRAME_LEN]
            if len(raw) >= FRAME_LEN:
                return raw[:FRAME_LEN]
            return raw

    def write_frame(self, frame: bytes, timeout_ms: int = 1000) -> None:
        if len(frame) != FRAME_LEN:
            raise ValueError(f"expected {FRAME_LEN}-byte frame, got {len(frame)}")
        if not self.handle or not self._write_event:
            raise RuntimeError("transport is not open")
        if self.output_report_len <= FRAME_LEN:
            report = frame[: self.output_report_len]
        else:
            report = bytes([APP_REPORT_ID]) + frame
            if len(report) < self.output_report_len:
                report = report + (b"\x00" * (self.output_report_len - len(report)))
            elif len(report) > self.output_report_len:
                report = report[: self.output_report_len]
        with self._write_lock:
            kernel32.ResetEvent(self._write_event)
            buf = ctypes.create_string_buffer(report)
            ok = kernel32.WriteFile(
                self.handle,
                buf,
                len(report),
                None,
                ctypes.byref(self._write_ov),
            )
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise ctypes.WinError(err)
            transferred = self._finish_overlapped(self._write_ov, timeout_ms)
            if transferred != len(report):
                raise IOError(f"short HID write: {transferred} != {len(report)}")


class HidGlobalWriteThrottle:
    def __init__(self, min_gap_sec: float = GLOBAL_WRITE_MIN_GAP_SEC):
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._gap = float(min_gap_sec)

    def wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self._gap


def _stable_phase_seconds(key: str, period_sec: float) -> float:
    if period_sec <= 0:
        return 0.0
    value = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    return (value % 1000) / 1000.0 * period_sec


def _normalize_selector_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


class DirectHidDeviceHandle:
    def __init__(
        self,
        path: str,
        logger: logging.Logger | None = None,
        write_throttle: HidGlobalWriteThrottle | None = None,
    ):
        self.lock = threading.Lock()
        self.path = path
        self.logger = logger or logging.getLogger(__name__)
        self.transport = WindowsHidTransport(path)
        self._write_throttle = write_throttle
        self._keepalive_phase_sec = 0.0
        self.info = UsbMeterDeviceInfo(path=path)
        self.name = path
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._info_ready = threading.Event()
        self._first_live_ready = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._sampler_thread: threading.Thread | None = None
        self._pending_samples: deque[UsbMeterRealtimeSample] = deque(maxlen=1024)
        self._latest_live_sample: UsbMeterRealtimeSample | None = None
        self._latest_sampled_sample: UsbMeterRealtimeSample | None = None
        self._accumulator = TwentySpsEnergyAccumulator(TARGET_SAMPLE_INTERVAL_MS)
        self._connected = False
        self._read_errors = 0
        self._write_errors = 0
        self._crc_errors = 0
        self._last_error: str | None = None
        self._last_frame_monotonic: float | None = None

    def start(self) -> None:
        self.transport.open()
        vid_pid = self.transport.fetch_attributes()
        if vid_pid:
            self.info.vid, self.info.pid = vid_pid
        (
            self.info.hid_serial,
            self.info.product_string,
            self.info.manufacturer_string,
        ) = self.transport.fetch_strings()
        self._connected = True

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"hid-reader-{self.path}",
            daemon=True,
        )
        self._reader_thread.start()
        self._await_device_info()
        if not self._info_ready.is_set():
            raise RuntimeError(f"HID device did not answer aa03 on path: {self.path}")
        if self.info.run_mode != 2:
            raise RuntimeError(
                f"HID device is not in application mode on path {self.path}: run_mode={self.info.run_mode}"
            )
        self._start_stream_with_official_parity()
        phase_key = self.info.hid_serial or str(self.info.lab_sn or "") or self.path
        self._keepalive_phase_sec = _stable_phase_seconds(phase_key, TARGET_KEEPALIVE_SEC)
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name=f"hid-keepalive-{self.name}",
            daemon=True,
        )
        self._keepalive_thread.start()
        self._sampler_thread = threading.Thread(
            target=self._sampler_loop,
            name=f"hid-sampler-{self.name}",
            daemon=True,
        )
        self._sampler_thread.start()
        if not self._first_live_ready.wait(timeout=STARTUP_INFO_TIMEOUT_SEC):
            raise RuntimeError(f"HID device did not stream aa04 on path: {self.path}")

    def close(self) -> None:
        self._stop_event.set()
        self._connected = False
        self.transport.cancel_pending_io()
        for thread in (self._reader_thread, self._keepalive_thread, self._sampler_thread):
            if thread and thread.is_alive():
                thread.join(timeout=THREAD_JOIN_TIMEOUT_SEC)
        self.transport.close()

    def _await_device_info(self) -> None:
        deadline = time.monotonic() + STARTUP_INFO_TIMEOUT_SEC
        while not self._info_ready.is_set():
            if time.monotonic() >= deadline:
                return
            self._send_command(CMD_INIT)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._info_ready.wait(timeout=min(STARTUP_INFO_RETRY_WAIT_SEC, remaining))

    def _start_stream_with_official_parity(self) -> None:
        for idx in range(STARTUP_STREAM_REPEAT):
            self._send_command(CMD_START_STREAM)
            if idx + 1 < STARTUP_STREAM_REPEAT:
                self._stop_event.wait(STARTUP_STREAM_GAP_SEC)

    def _set_error(self, exc: Exception) -> None:
        self._last_error = str(exc)
        self.logger.warning("HID backend device %s error: %s", self.path, exc)

    def _send_command(self, cmd: int, payload: bytes = b"") -> None:
        try:
            if self._write_throttle is not None:
                self._write_throttle.wait_turn()
            self.transport.write_frame(build_frame(cmd, payload))
        except Exception as exc:
            self._write_errors += 1
            self._set_error(exc)
            raise

    def _keepalive_loop(self) -> None:
        next_send = time.monotonic() + self._keepalive_phase_sec
        while not self._stop_event.wait(max(0.0, next_send - time.monotonic())):
            try:
                self._send_command(CMD_KEEPALIVE)
            except Exception:
                self._connected = False
                return
            next_send += TARGET_KEEPALIVE_SEC

    def _sampler_loop(self) -> None:
        next_tick = time.monotonic() + (TARGET_SAMPLE_INTERVAL_MS / 1000.0)
        while not self._stop_event.wait(max(0.0, next_tick - time.monotonic())):
            self._consume_pending_samples_until(next_tick)
            next_tick += TARGET_SAMPLE_INTERVAL_MS / 1000.0

    def _consume_pending_sample_for_tick(self) -> bool:
        return self._consume_pending_samples_until(time.monotonic())

    def _consume_pending_samples_until(self, tick_deadline: float) -> bool:
        with self._state_lock:
            if not self._pending_samples:
                return False
            sample = None
            while self._pending_samples and self._pending_samples[0].received_monotonic <= tick_deadline:
                sample = self._pending_samples.popleft()
            if sample is None:
                return False
            live_vbus = self._latest_live_sample.vbus if self._latest_live_sample else sample.vbus
            self._latest_sampled_sample = sample
            self._accumulator.add_tick(sample.ibus, live_vbus)
            return True

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self.transport.read_frame(timeout_ms=200)
            except Exception as exc:
                self._connected = False
                self._read_errors += 1
                self._set_error(exc)
                return
            if not frame:
                continue
            if len(frame) != FRAME_LEN:
                continue
            self._last_frame_monotonic = time.monotonic()
            if frame[63] != crc8_39(frame[:63]):
                self._crc_errors += 1
                continue
            if frame[0] != AA_HEADER:
                continue
            cmd = frame[1]
            if cmd == CMD_INFO:
                self._handle_aa03(frame)
            elif cmd == CMD_LIVE:
                self._handle_aa04(frame)

    def _handle_aa03(self, frame: bytes) -> None:
        parsed = parse_aa03(frame)
        with self._state_lock:
            self.info.run_mode = int(parsed["run_mode"])
            self.info.model_code = int(parsed["model_code"])
            self.info.model_name = str(parsed["model_name"])
            self.info.lab_sn = int(parsed["lab_sn"])
            self.info.app_version_raw = int(parsed["app_version_raw"])
            self.info.dword3 = int(parsed["dword3"])
            self.name = self.info.title
        self._info_ready.set()

    def _handle_aa04(self, frame: bytes) -> None:
        samples = parse_aa04_samples(frame, received_monotonic=time.monotonic())
        if not samples:
            return
        with self._state_lock:
            self._latest_live_sample = samples[-1]
            self._pending_samples.extend(samples)
        self._first_live_ready.set()

    def _require_live_sample(self) -> UsbMeterRealtimeSample:
        with self._state_lock:
            sample = self._latest_live_sample
        if sample is None:
            raise RuntimeError(f"No live sample received yet for {self.name}")
        return sample

    def get_vbus_value(self) -> str:
        sample = self._require_live_sample()
        return format_vbus(sample.vbus)

    def get_ibus_value(self) -> str:
        sample = self._require_live_sample()
        return format_ibus(sample.ibus)

    def get_last_value_item(self) -> str:
        with self._state_lock:
            return format_nrg_wh(self._accumulator.nrg_wh)

    def get_nrg_wh_value(self) -> float:
        with self._state_lock:
            return float(self._accumulator.nrg_wh)

    def get_health(self) -> dict[str, object]:
        with self._state_lock:
            pending_sample_count = len(self._pending_samples)
            latest_sampled_monotonic = (
                self._latest_sampled_sample.received_monotonic
                if self._latest_sampled_sample
                else None
            )
        return {
            "connected": self._connected,
            "last_sample_monotonic": self._last_frame_monotonic,
            "latest_sampled_monotonic": latest_sampled_monotonic,
            "pending_sample_count": pending_sample_count,
            "read_errors": self._read_errors,
            "write_errors": self._write_errors,
            "crc_errors": self._crc_errors,
            "last_error": self._last_error,
            "path": self.path,
            "hid_serial": self.info.hid_serial,
            "protocol_lab_sn": self.info.lab_sn,
            "title": self.name,
        }


class HidBackendManager:
    def __init__(
        self,
        requested_titles: list[str] | None = None,
        logger: logging.Logger | None = None,
        hid_path=None,
        hid_serial=None,
        allow_all: bool = False,
        discovery_only: bool = False,
        startup_cooldown_sec: float = DEVICE_STARTUP_COOLDOWN_SEC,
        write_min_gap_sec: float = GLOBAL_WRITE_MIN_GAP_SEC,
    ):
        self.requested_titles = set(requested_titles or [])
        self.logger = logger or logging.getLogger(__name__)
        self.hid_paths = _normalize_selector_list(hid_path)
        self.hid_serials = _normalize_selector_list(hid_serial)
        self.hid_path = self.hid_paths[0] if len(self.hid_paths) == 1 else list(self.hid_paths)
        self.hid_serial = self.hid_serials[0] if len(self.hid_serials) == 1 else list(self.hid_serials)
        self.allow_all = allow_all
        self.discovery_only = discovery_only
        self.startup_cooldown_sec = float(startup_cooldown_sec)
        self._write_throttle = HidGlobalWriteThrottle(write_min_gap_sec)
        self.devices: dict[str, DirectHidDeviceHandle] = {}
        self.candidates: list[HidDeviceCandidate] = []
        self.startup_errors: list[dict[str, object]] = []
        self.candidate_filter_warnings: list[dict[str, object]] = []

    def list_candidates(self) -> list[dict[str, object]]:
        self.candidates = enumerate_supported_hid_candidates()
        return [candidate.as_dict() for candidate in self.candidates]

    def discover_devices(self) -> dict[str, DirectHidDeviceHandle]:
        return self._reconcile_devices(
            reset=True,
            strict_requested=self._has_strict_selection(),
        )

    def refresh_devices(
        self,
        restart_titles: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, DirectHidDeviceHandle]:
        return self._reconcile_devices(
            reset=False,
            strict_requested=False,
            restart_titles=set(restart_titles or []),
        )

    def _reconcile_devices(
        self,
        *,
        reset: bool,
        strict_requested: bool,
        restart_titles: set[str] | None = None,
    ) -> dict[str, DirectHidDeviceHandle]:
        restart_titles = restart_titles or set()
        self.candidates = enumerate_supported_hid_candidates()
        self.startup_errors = []
        self.candidate_filter_warnings = []
        if self.discovery_only:
            if reset:
                self.close()
            self.devices = {}
            return {}

        candidates = self._select_candidates_for_probe()
        requested = set(self.requested_titles)
        if reset:
            self.close()
            found: dict[str, DirectHidDeviceHandle] = {}
        else:
            found = dict(self.devices)

        selected_paths = {candidate.path.lower() for candidate in candidates}
        for key, dev in list(found.items()):
            path_missing = dev.path.lower() not in selected_paths
            should_restart = key in restart_titles or dev.name in restart_titles
            if path_missing or should_restart:
                try:
                    dev.close()
                except Exception:
                    pass
                found.pop(key, None)

        active_paths = {dev.path.lower() for dev in found.values()}
        errors: list[str] = []
        for idx, candidate in enumerate(candidates):
            if candidate.path.lower() in active_paths:
                continue
            dev = DirectHidDeviceHandle(
                path=candidate.path,
                logger=self.logger,
                write_throttle=self._write_throttle,
            )
            try:
                dev.start()
                title = dev.name
                if requested and title not in requested:
                    dev.close()
                    continue
                found[self._build_unique_device_key(title, dev, found)] = dev
                active_paths.add(candidate.path.lower())
                requested.discard(title)
            except Exception as exc:
                try:
                    dev.close()
                finally:
                    errors.append(f"{candidate.path}: {exc}")
                    self.startup_errors.append(
                        {
                            "stage": "start",
                            "path": candidate.path,
                            "hid_serial": candidate.hid_serial,
                            "device_instance_id": candidate.device_instance_id,
                            "error": str(exc),
                        }
                    )
                if not self.allow_all and len(candidates) == 1:
                    break
            finally:
                if idx + 1 < len(candidates) and self.startup_cooldown_sec > 0:
                    time.sleep(self.startup_cooldown_sec)
        if strict_requested and requested:
            missing = ", ".join(sorted(requested))
            raise RuntimeError(f"Requested HID devices not found: {missing}")
        if strict_requested and not found and errors:
            raise RuntimeError("No HID devices initialized successfully: " + "; ".join(errors))
        self.devices = found
        return found

    def _select_candidates_for_probe(self) -> list[HidDeviceCandidate]:
        if self.allow_all:
            if os.environ.get(UNSAFE_ALLOW_ALL_ENV) != "1":
                raise RuntimeError(
                    "Direct-HID allow_all multi-device probing is disabled after "
                    "multi-device stress testing triggered USB descriptor failures. "
                    "Use hid_path/hid_serial to probe one explicit device, or set "
                    f"{UNSAFE_ALLOW_ALL_ENV}=1 only for isolated lab diagnostics."
                )
            return self._select_allow_all_candidates()
        if self.requested_titles:
            raise RuntimeError(
                "HID title selection requires probing devices. "
                "Use hid_path/hid_serial for single-device safe probing, or enable hid_allow_all."
            )
        if self.hid_paths and self.hid_serials:
            raise RuntimeError("Specify only one HID selector type: hid_path or hid_serial")
        if self.hid_paths:
            return self._select_path_candidates()
        if self.hid_serials:
            return self._select_serial_candidates()
        return self._select_auto_candidates()

    def _has_strict_selection(self) -> bool:
        return bool(self.allow_all or self.hid_paths or self.hid_serials or self.requested_titles)

    def _select_auto_candidates(self) -> list[HidDeviceCandidate]:
        return list(self.candidates)

    def _select_allow_all_candidates(self) -> list[HidDeviceCandidate]:
        by_serial: dict[str, list[HidDeviceCandidate]] = {}
        for candidate in self.candidates:
            serial = (candidate.hid_serial or "").strip()
            if not serial:
                self._record_candidate_filter_warning(
                    candidate,
                    reason="missing_hid_serial",
                    detail="allow_all skips candidates without a stable HID serial",
                )
                continue
            by_serial.setdefault(serial.lower(), []).append(candidate)

        selected: list[HidDeviceCandidate] = []
        for _serial_key, candidates in sorted(
            by_serial.items(),
            key=lambda item: item[1][0].hid_serial or item[0],
        ):
            if len(candidates) != 1:
                for candidate in candidates:
                    self._record_candidate_filter_warning(
                        candidate,
                        reason="duplicate_hid_serial",
                        detail=(
                            "allow_all skips duplicate HID serial groups because the "
                            "concrete runtime instance is ambiguous"
                        ),
                    )
                continue
            selected.append(candidates[0])

        if len(selected) > ALLOW_ALL_MAX_UNSAFE_CANDIDATES:
            serials = ", ".join(candidate.hid_serial or candidate.path for candidate in selected)
            raise RuntimeError(
                "Direct-HID allow_all refused to probe "
                f"{len(selected)} candidates; max is {ALLOW_ALL_MAX_UNSAFE_CANDIDATES}. "
                f"Use explicit hid_serial/hid_path selectors. Candidates: {serials}"
            )
        return selected

    def _record_candidate_filter_warning(
        self,
        candidate: HidDeviceCandidate,
        *,
        reason: str,
        detail: str,
    ) -> None:
        self.candidate_filter_warnings.append(
            {
                "stage": "candidate_filter",
                "reason": reason,
                "detail": detail,
                "path": candidate.path,
                "hid_serial": candidate.hid_serial,
                "device_instance_id": candidate.device_instance_id,
            }
        )

    def _select_path_candidates(self) -> list[HidDeviceCandidate]:
        selected: list[HidDeviceCandidate] = []
        seen_paths: set[str] = set()
        for selector in self.hid_paths:
            needle = selector.lower()
            matches = [
                candidate
                for candidate in self.candidates
                if candidate.path.lower() == needle or needle in candidate.path.lower()
            ]
            for candidate in self._require_single_match(matches, f"hid_path={selector}"):
                if candidate.path not in seen_paths:
                    selected.append(candidate)
                    seen_paths.add(candidate.path)
        return selected

    def _select_serial_candidates(self) -> list[HidDeviceCandidate]:
        selected: list[HidDeviceCandidate] = []
        seen_paths: set[str] = set()
        for selector in self.hid_serials:
            needle = selector.lower()
            matches = [
                candidate
                for candidate in self.candidates
                if candidate.hid_serial and candidate.hid_serial.lower() == needle
            ]
            for candidate in self._require_single_match(matches, f"hid_serial={selector}"):
                if candidate.path not in seen_paths:
                    selected.append(candidate)
                    seen_paths.add(candidate.path)
        return selected

    @staticmethod
    def _require_single_match(
        matches: list[HidDeviceCandidate],
        selector: str,
    ) -> list[HidDeviceCandidate]:
        if not matches:
            raise RuntimeError(f"No HID candidate matched {selector}")
        if len(matches) > 1:
            paths = "; ".join(candidate.path for candidate in matches)
            raise RuntimeError(f"HID selector {selector} matched multiple candidates: {paths}")
        return matches

    def close(self) -> None:
        for dev in list(self.devices.values()):
            try:
                dev.close()
            except Exception:
                pass
        self.devices = {}

    def get_startup_errors(self) -> list[dict[str, object]]:
        return list(self.startup_errors)

    def get_candidate_filter_warnings(self) -> list[dict[str, object]]:
        return list(self.candidate_filter_warnings)

    @staticmethod
    def _build_unique_device_key(
        title: str,
        dev: DirectHidDeviceHandle,
        found: dict[str, DirectHidDeviceHandle],
    ) -> str:
        if title not in found:
            return title
        suffix = dev.info.hid_serial or dev.path.rsplit("#", 1)[-1]
        candidate = f"{title}@{suffix}"
        index = 2
        while candidate in found:
            candidate = f"{title}@{suffix}-{index}"
            index += 1
        return candidate
