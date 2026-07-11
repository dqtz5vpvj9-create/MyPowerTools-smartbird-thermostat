import math
import unittest

from unittest.mock import ANY, MagicMock, patch

from test_tools.usbmeter_hid_backend import (
    AA_HEADER,
    CMD_INFO,
    CMD_LIVE,
    CMD_INIT,
    CMD_KEEPALIVE,
    CMD_START_STREAM,
    DirectHidDeviceHandle,
    HidGlobalWriteThrottle,
    HidBackendManager,
    HidDeviceCandidate,
    RAW_SAMPLE_INTERVAL_SEC,
    TARGET_SAMPLE_INTERVAL_MS,
    TwentySpsEnergyAccumulator,
    UNSAFE_ALLOW_ALL_ENV,
    UsbMeterRealtimeSample,
    _parse_signed_temp,
    _stable_phase_seconds,
    build_frame,
    crc8_39,
    parse_aa03,
    parse_aa04_samples,
)


class TestUsbMeterHidBackend(unittest.TestCase):
    def test_build_frame_sets_crc(self):
        frame = build_frame(0x81)

        self.assertEqual(len(frame), 64)
        self.assertEqual(frame[0], AA_HEADER)
        self.assertEqual(frame[1], 0x81)
        self.assertEqual(frame[63], crc8_39(frame[:63]))

    def test_parse_aa03(self):
        frame = bytearray(64)
        frame[0] = AA_HEADER
        frame[1] = CMD_INFO
        frame[2:6] = (2).to_bytes(4, "little")
        frame[6:10] = (0x003F003A).to_bytes(4, "little")
        frame[10:14] = (69343).to_bytes(4, "little")
        frame[14:18] = (0x30).to_bytes(4, "little")
        frame[63] = crc8_39(frame[:63])

        parsed = parse_aa03(bytes(frame))

        self.assertEqual(parsed["run_mode"], 2)
        self.assertEqual(parsed["model_code"], 58)
        self.assertEqual(parsed["model_name"], "FNB-58")
        self.assertEqual(parsed["app_version_raw"], 0x003F)
        self.assertEqual(parsed["lab_sn"], 69343)
        self.assertEqual(parsed["dword3"], 0x30)

    def test_parse_aa04_samples_uses_four_15_byte_samples(self):
        frame = bytearray(64)
        frame[0] = AA_HEADER
        frame[1] = CMD_LIVE
        values = [
            (5.10000, 0.10000, 0.521, 0.520, 0, 253),
            (5.11000, 0.11000, 0.522, 0.521, 0, 254),
            (5.12000, 0.12000, 0.523, 0.522, 0, 255),
            (5.13000, 0.13000, 0.524, 0.523, 0, 256),
        ]
        off = 2
        for vbus, ibus, dp, dm, sign_flag, temp10 in values:
            frame[off:off + 4] = int(round(vbus * 100000)).to_bytes(4, "little")
            frame[off + 4:off + 8] = int(round(ibus * 100000)).to_bytes(4, "little")
            frame[off + 8:off + 10] = int(round(dp * 1000)).to_bytes(2, "little")
            frame[off + 10:off + 12] = int(round(dm * 1000)).to_bytes(2, "little")
            frame[off + 12] = sign_flag
            frame[off + 13:off + 15] = int(temp10).to_bytes(2, "little")
            off += 15
        frame[63] = crc8_39(frame[:63])

        samples = parse_aa04_samples(bytes(frame), received_monotonic=123.0)

        self.assertEqual(len(samples), 4)
        self.assertAlmostEqual(samples[-1].vbus, 5.13, places=5)
        self.assertAlmostEqual(samples[-1].ibus, 0.13, places=5)
        self.assertAlmostEqual(samples[-1].dp, 0.524, places=3)
        self.assertAlmostEqual(samples[-1].dm, 0.523, places=3)
        self.assertAlmostEqual(samples[-1].temp, -25.6, places=1)
        self.assertAlmostEqual(samples[-1].pbus, 5.13 * 0.13, places=6)
        self.assertAlmostEqual(samples[-1].impd, 5.13 / 0.13, places=6)
        self.assertEqual(samples[-1].received_monotonic, 123.0)
        self.assertAlmostEqual(samples[0].received_monotonic, 123.0 - 3 * RAW_SAMPLE_INTERVAL_SEC)
        self.assertAlmostEqual(samples[1].received_monotonic, 123.0 - 2 * RAW_SAMPLE_INTERVAL_SEC)
        self.assertAlmostEqual(samples[2].received_monotonic, 123.0 - RAW_SAMPLE_INTERVAL_SEC)

    def test_parse_signed_temp_negative_when_sign_zero(self):
        self.assertEqual(_parse_signed_temp(0, 256), -25.6)
        self.assertEqual(_parse_signed_temp(1, 256), 25.6)
        self.assertEqual(_parse_signed_temp(2, 256), 25.6)

    def test_twenty_sps_accumulator_matches_closed_host_parity_formula(self):
        acc = TwentySpsEnergyAccumulator(TARGET_SAMPLE_INTERVAL_MS)

        cap_1, nrg_1 = acc.add_tick(ibus_now=0.09170, live_vbus_now=5.11505)
        cap_2, nrg_2 = acc.add_tick(ibus_now=0.09334, live_vbus_now=5.16383)

        dt_hours = TARGET_SAMPLE_INTERVAL_MS / 1000.0 / 3600.0
        expected_cap_1 = ((0.09170 + 0.0) * 0.5) * dt_hours
        expected_nrg_1 = 5.11505 * 0.09170 * 0.25 * dt_hours
        expected_cap_2 = expected_cap_1 + ((0.09334 + 0.09170) * 0.5) * dt_hours
        expected_nrg_2 = expected_nrg_1 + 5.16383 * 0.09334 * 0.25 * dt_hours

        self.assertTrue(math.isclose(cap_1, expected_cap_1, rel_tol=0, abs_tol=1e-15))
        self.assertTrue(math.isclose(nrg_1, expected_nrg_1, rel_tol=0, abs_tol=1e-15))
        self.assertTrue(math.isclose(cap_2, expected_cap_2, rel_tol=0, abs_tol=1e-15))
        self.assertTrue(math.isclose(nrg_2, expected_nrg_2, rel_tol=0, abs_tol=1e-15))

    def test_consume_pending_samples_for_tick_drains_until_deadline(self):
        dev = DirectHidDeviceHandle(path="hid-path")
        dev._latest_live_sample = UsbMeterRealtimeSample(
            vbus=5.2,
            ibus=0.3,
            dp=0.0,
            dm=0.0,
            pbus=1.56,
            impd=17.3,
            temp=25.0,
            received_monotonic=1.0,
        )
        sample_a = UsbMeterRealtimeSample(5.0, 0.1, 0.0, 0.0, 0.5, 50.0, 20.0, 1.0)
        sample_b = UsbMeterRealtimeSample(5.1, 0.2, 0.0, 0.0, 1.02, 25.5, 20.0, 2.0)
        sample_c = UsbMeterRealtimeSample(5.2, 0.3, 0.0, 0.0, 1.56, 17.3, 20.0, 3.0)
        dev._pending_samples.extend([sample_a, sample_b, sample_c])

        consumed = dev._consume_pending_samples_until(2.5)

        self.assertTrue(consumed)
        self.assertEqual(dev._accumulator.prev_ibus, 0.2)
        self.assertEqual(list(dev._pending_samples), [sample_c])

    def test_pending_sample_queue_has_official_sized_headroom(self):
        dev = DirectHidDeviceHandle(path="hid-path")
        self.assertEqual(dev._pending_samples.maxlen, 1024)

    def test_get_nrg_wh_value_returns_unrounded_accumulator(self):
        dev = DirectHidDeviceHandle(path="hid-path")
        dev._accumulator.nrg_wh = 0.123456789

        self.assertEqual(dev.get_last_value_item(), "0.1235 Wh")
        self.assertAlmostEqual(dev.get_nrg_wh_value(), 0.123456789)

    def test_send_command_uses_global_write_throttle(self):
        throttle = MagicMock(spec=HidGlobalWriteThrottle)
        dev = DirectHidDeviceHandle(path="hid-path", write_throttle=throttle)
        dev.transport = MagicMock()

        dev._send_command(CMD_KEEPALIVE)

        throttle.wait_turn.assert_called_once()
        dev.transport.write_frame.assert_called_once()

    def test_stable_phase_is_deterministic_and_bounded(self):
        phase_a = _stable_phase_seconds("SERIAL-A", 1.0)
        phase_b = _stable_phase_seconds("SERIAL-A", 1.0)

        self.assertEqual(phase_a, phase_b)
        self.assertGreaterEqual(phase_a, 0.0)
        self.assertLess(phase_a, 1.0)

    def test_start_retries_init_and_sends_two_stream_enables(self):
        dev = DirectHidDeviceHandle(path="hid-path")
        dev.transport = MagicMock()
        dev.transport.fetch_attributes.return_value = (0x2E3C, 0x5558)
        dev.transport.fetch_strings.return_value = ("SERIAL", "FNB", "FNIRSI")
        command_order = []

        def fake_send(cmd, payload=b""):
            command_order.append(cmd)
            if cmd == CMD_INIT and command_order.count(CMD_INIT) == 2:
                dev.info.run_mode = 2
                dev._info_ready.set()
            if cmd == CMD_START_STREAM and command_order.count(CMD_START_STREAM) == 2:
                dev._first_live_ready.set()

        dev._send_command = fake_send
        dev._reader_thread = MagicMock()
        dev._keepalive_thread = MagicMock()
        dev._sampler_thread = MagicMock()

        with patch('test_tools.usbmeter_hid_backend.threading.Thread') as mock_thread_cls:
            mock_thread_cls.side_effect = [
                MagicMock(start=MagicMock()),
                MagicMock(start=MagicMock()),
                MagicMock(start=MagicMock()),
            ]
            dev.start()

        self.assertEqual(command_order[:4], [CMD_INIT, CMD_INIT, CMD_START_STREAM, CMD_START_STREAM])

    def test_close_joins_threads(self):
        dev = DirectHidDeviceHandle(path="hid-path")
        reader = MagicMock()
        reader.is_alive.return_value = True
        keepalive = MagicMock()
        keepalive.is_alive.return_value = True
        sampler = MagicMock()
        sampler.is_alive.return_value = True
        dev._reader_thread = reader
        dev._keepalive_thread = keepalive
        dev._sampler_thread = sampler
        dev.transport = MagicMock()

        dev.close()

        reader.join.assert_called_once()
        keepalive.join.assert_called_once()
        sampler.join.assert_called_once()

    def test_discover_devices_closes_failed_start(self):
        fake_dev = MagicMock()
        fake_dev.start.side_effect = RuntimeError("boom")
        fake_dev.close = MagicMock()
        candidate = HidDeviceCandidate(path="p1", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle', return_value=fake_dev):
                with patch.dict('os.environ', {UNSAFE_ALLOW_ALL_ENV: "1"}):
                    mgr = HidBackendManager(allow_all=True)
                    with self.assertRaises(RuntimeError):
                        mgr.discover_devices()

        fake_dev.close.assert_called_once()

    def test_allow_all_is_blocked_without_lab_env(self):
        candidate = HidDeviceCandidate(path="p1", vid=0x2E3C, pid=0x5558)

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle') as mock_handle_cls:
                with patch.dict('os.environ', {}, clear=True):
                    mgr = HidBackendManager(allow_all=True)
                    with self.assertRaisesRegex(RuntimeError, "allow_all multi-device probing is disabled"):
                        mgr.discover_devices()

        mock_handle_cls.assert_not_called()

    def test_allow_all_filters_unstable_and_duplicate_candidates(self):
        no_serial = HidDeviceCandidate(path="path-no-serial", vid=0x2E3C, pid=0x5558)
        duplicate_a = HidDeviceCandidate(path="path-dup-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-DUP")
        duplicate_b = HidDeviceCandidate(path="path-dup-b", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-DUP")
        candidate_b = HidDeviceCandidate(path="path-b", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-B")
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")
        dev_a = MagicMock()
        dev_a.name = "DEV-A"
        dev_a.info.hid_serial = "SERIAL-A"
        dev_b = MagicMock()
        dev_b.name = "DEV-B"
        dev_b.info.hid_serial = "SERIAL-B"

        with patch(
            'test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates',
            return_value=[no_serial, duplicate_a, candidate_b, duplicate_b, candidate_a],
        ):
            with patch(
                'test_tools.usbmeter_hid_backend.DirectHidDeviceHandle',
                side_effect=[dev_a, dev_b],
            ) as mock_handle_cls:
                with patch.dict('os.environ', {UNSAFE_ALLOW_ALL_ENV: "1"}):
                    mgr = HidBackendManager(allow_all=True, startup_cooldown_sec=0)
                    devices = mgr.discover_devices()

        self.assertEqual(list(devices), ["DEV-A", "DEV-B"])
        self.assertEqual(
            [call.kwargs["path"] for call in mock_handle_cls.call_args_list],
            ["path-a", "path-b"],
        )
        reasons = [item["reason"] for item in mgr.get_candidate_filter_warnings()]
        self.assertEqual(reasons.count("missing_hid_serial"), 1)
        self.assertEqual(reasons.count("duplicate_hid_serial"), 2)

    def test_allow_all_refuses_too_many_candidates(self):
        candidates = [
            HidDeviceCandidate(path=f"path-{idx}", vid=0x2E3C, pid=0x5558, hid_serial=f"SERIAL-{idx}")
            for idx in range(9)
        ]

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=candidates):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle') as mock_handle_cls:
                with patch.dict('os.environ', {UNSAFE_ALLOW_ALL_ENV: "1"}):
                    mgr = HidBackendManager(allow_all=True)
                    with self.assertRaisesRegex(RuntimeError, "refused to probe 9 candidates"):
                        mgr.discover_devices()

        mock_handle_cls.assert_not_called()

    def test_default_discover_devices_auto_opens_all_candidates(self):
        candidate_a = HidDeviceCandidate(
            path="path-a",
            vid=0x2E3C,
            pid=0x5558,
            hid_serial="SERIAL-A",
        )
        candidate_b = HidDeviceCandidate(
            path="path-b",
            vid=0x2E3C,
            pid=0x5558,
            hid_serial="SERIAL-B",
        )
        dev_a = MagicMock()
        dev_a.name = "DEV-A"
        dev_a.info.hid_serial = "SERIAL-A"
        dev_b = MagicMock()
        dev_b.name = "DEV-B"
        dev_b.info.hid_serial = "SERIAL-B"

        with patch(
            'test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates',
            return_value=[candidate_a, candidate_b],
        ):
            with patch(
                'test_tools.usbmeter_hid_backend.DirectHidDeviceHandle',
                side_effect=[dev_a, dev_b],
            ) as mock_handle_cls:
                mgr = HidBackendManager(startup_cooldown_sec=0)
                devices = mgr.discover_devices()

        self.assertEqual(list(devices), ["DEV-A", "DEV-B"])
        self.assertEqual(
            [call.kwargs["path"] for call in mock_handle_cls.call_args_list],
            ["path-a", "path-b"],
        )
        dev_a.start.assert_called_once()
        dev_b.start.assert_called_once()

    def test_default_auto_probe_records_failures_without_raising(self):
        candidate = HidDeviceCandidate(
            path="path-a",
            vid=0x2E3C,
            pid=0x5558,
            hid_serial="SERIAL-A",
        )
        dev = MagicMock()
        dev.start.side_effect = RuntimeError("probe failed")

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle', return_value=dev):
                mgr = HidBackendManager()
                devices = mgr.discover_devices()

        self.assertEqual(devices, {})
        self.assertEqual(mgr.get_startup_errors()[0]["error"], "probe failed")
        dev.close.assert_called_once()

    def test_explicit_hid_path_probes_only_matching_candidate(self):
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558)
        candidate_b = HidDeviceCandidate(path="path-b", vid=0x2E3C, pid=0x5558)
        fake_dev = MagicMock()
        fake_dev.name = "DEV-A"
        fake_dev.info.hid_serial = "SERIAL-A"

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate_a, candidate_b]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle', return_value=fake_dev) as mock_handle_cls:
                mgr = HidBackendManager(hid_path="path-a")
                devices = mgr.discover_devices()

        self.assertEqual(list(devices), ["DEV-A"])
        mock_handle_cls.assert_called_once_with(path="path-a", logger=mgr.logger, write_throttle=ANY)
        fake_dev.start.assert_called_once()

    def test_explicit_hid_serial_probes_only_matching_candidate(self):
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")
        candidate_b = HidDeviceCandidate(path="path-b", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-B")
        fake_dev = MagicMock()
        fake_dev.name = "DEV-B"
        fake_dev.info.hid_serial = "SERIAL-B"

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate_a, candidate_b]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle', return_value=fake_dev) as mock_handle_cls:
                mgr = HidBackendManager(hid_serial="serial-b")
                devices = mgr.discover_devices()

        self.assertEqual(list(devices), ["DEV-B"])
        mock_handle_cls.assert_called_once_with(path="path-b", logger=mgr.logger, write_throttle=ANY)
        fake_dev.start.assert_called_once()

    def test_explicit_multi_serial_continues_after_one_start_failure(self):
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")
        candidate_b = HidDeviceCandidate(path="path-b", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-B")
        failed_dev = MagicMock()
        failed_dev.start.side_effect = RuntimeError("boom")
        good_dev = MagicMock()
        good_dev.name = "DEV-B"
        good_dev.info.hid_serial = "SERIAL-B"

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate_a, candidate_b]):
            with patch(
                'test_tools.usbmeter_hid_backend.DirectHidDeviceHandle',
                side_effect=[failed_dev, good_dev],
            ) as mock_handle_cls:
                mgr = HidBackendManager(
                    hid_serial=["SERIAL-A", "SERIAL-B"],
                    startup_cooldown_sec=0,
                )
                devices = mgr.discover_devices()

        self.assertEqual(list(devices), ["DEV-B"])
        self.assertEqual(mock_handle_cls.call_count, 2)
        failed_dev.close.assert_called_once()
        good_dev.start.assert_called_once()
        self.assertEqual(mgr.get_startup_errors()[0]["hid_serial"], "SERIAL-A")

    def test_refresh_devices_adds_new_allow_all_candidate_without_restarting_existing(self):
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")
        candidate_b = HidDeviceCandidate(path="path-b", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-B")
        dev_a = MagicMock()
        dev_a.name = "DEV-A"
        dev_a.path = "path-a"
        dev_a.info.hid_serial = "SERIAL-A"
        dev_b = MagicMock()
        dev_b.name = "DEV-B"
        dev_b.path = "path-b"
        dev_b.info.hid_serial = "SERIAL-B"

        with patch(
            'test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates',
            side_effect=[[candidate_a], [candidate_a, candidate_b]],
        ):
            with patch(
                'test_tools.usbmeter_hid_backend.DirectHidDeviceHandle',
                side_effect=[dev_a, dev_b],
            ) as mock_handle_cls:
                with patch.dict('os.environ', {UNSAFE_ALLOW_ALL_ENV: "1"}):
                    mgr = HidBackendManager(allow_all=True, startup_cooldown_sec=0)
                    self.assertEqual(list(mgr.discover_devices()), ["DEV-A"])
                    refreshed = mgr.refresh_devices()

        self.assertEqual(list(refreshed), ["DEV-A", "DEV-B"])
        self.assertEqual(mock_handle_cls.call_count, 2)
        dev_a.start.assert_called_once()
        dev_a.close.assert_not_called()
        dev_b.start.assert_called_once()

    def test_refresh_devices_closes_missing_candidate(self):
        candidate_a = HidDeviceCandidate(path="path-a", vid=0x2E3C, pid=0x5558, hid_serial="SERIAL-A")
        dev_a = MagicMock()
        dev_a.name = "DEV-A"
        dev_a.path = "path-a"
        dev_a.info.hid_serial = "SERIAL-A"

        with patch(
            'test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates',
            side_effect=[[candidate_a], []],
        ):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle', return_value=dev_a):
                with patch.dict('os.environ', {UNSAFE_ALLOW_ALL_ENV: "1"}):
                    mgr = HidBackendManager(allow_all=True, startup_cooldown_sec=0)
                    self.assertEqual(list(mgr.discover_devices()), ["DEV-A"])
                    refreshed = mgr.refresh_devices()

        self.assertEqual(refreshed, {})
        dev_a.close.assert_called_once()

    def test_requested_title_without_allow_all_is_rejected_before_opening(self):
        candidate = HidDeviceCandidate(path="p1", vid=0x2E3C, pid=0x5558)

        with patch('test_tools.usbmeter_hid_backend.enumerate_supported_hid_candidates', return_value=[candidate]):
            with patch('test_tools.usbmeter_hid_backend.DirectHidDeviceHandle') as mock_handle_cls:
                mgr = HidBackendManager(requested_titles=["FNB-58-1"])
                with self.assertRaises(RuntimeError):
                    mgr.discover_devices()

        mock_handle_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
