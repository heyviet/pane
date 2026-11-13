import asyncio
import threading
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock

from handset import screen


class TransportSwitchTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.started = {name: threading.Event() for name in ("usb", "wifi")}
        self.workers = []
        self.order = []

        async def serve(udid, transport):
            self.workers.append(threading.current_thread())
            self.order.append(("start", transport))
            screen._set(phase="running")
            self.started[transport].set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
            finally:
                self.order.append(("stop", transport))

        for target, value in (
            ("_usb_udids", lambda: ["phone"]),
            ("wifi_paired_udids", lambda: ["phone"]),
            ("developer_mode_enabled", lambda _: True),
            ("_serve", serve),
        ):
            patcher = patch.object(screen, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        screen._set(phase="idle", udid=None, transport=None)

    def tearDown(self):
        self.release.set()
        for worker in self.workers:
            worker.join(3)

    def test_switch_releases_usb_before_starting_wifi(self):
        screen.start_screen("phone", "usb")
        self.assertTrue(self.started["usb"].wait(2))
        result = screen.start_screen("phone", "wifi")
        self.assertEqual(result["transport"], "wifi")
        self.assertTrue(self.started["wifi"].wait(2))
        self.assertEqual(self.order[:3], [("start", "usb"), ("stop", "usb"), ("start", "wifi")])

    def test_same_transport_reuses_stream(self):
        screen.start_screen("phone", "usb")
        self.assertTrue(self.started["usb"].wait(2))
        self.assertEqual(screen.start_screen("phone", "usb")["phase"], "running")
        self.assertEqual(self.order, [("start", "usb")])


class KeyboardAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_movie_sync_uses_video_and_ignores_host_echo(self):
        from handset import clipboard
        from handset.video_clipboard import VideoContent
        video = VideoContent(b"test movie", "public.mpeg-4", "clip.mp4")
        snapshot = {"pasteboard":{"metadata":{"nonce":"n","changeCount":2},
                    "items":[{"types":["public.png","public.mpeg-4"]}]}}

        class Monitor:
            def __init__(self, rsd, on_change, allow_full_pull):
                self._on_change = on_change
                self._last_change = ("n",1)
                self._own_changes = []
                self._recent_host_content = {}
                self._check = AsyncMock()
            async def start(self):
                pass

        service = AsyncMock()
        service.__aenter__.return_value = service
        service.get.return_value = snapshot
        server = SimpleNamespace(_clipboard_monitor=None, _rsd=object(),
                                 _on_device_clipboard=Mock(), _stop_clipboard_monitor=AsyncMock())
        state = {"enabled":True,"phase":"waiting","message":"","count":0}
        with (patch.object(screen,"_SERVER",server), patch.object(screen,"_SYNC_STATE",state),
              patch("pymobiledevice3.remote.core_device.pasteboard_service.PasteboardMonitor",Monitor),
              patch("pymobiledevice3.remote.core_device.pasteboard_service.PasteboardService",return_value=service),
              patch("handset.video_clipboard.read_video",AsyncMock(return_value=video)),
              patch.object(clipboard,"_windows_clipboard") as pc):
            await screen._configure_clipboard_sync(server,True)
            await server._clipboard_monitor._check()
            await asyncio.gather(*server._pane_sync_tasks)
            pc.assert_called_once_with(video)
            self.assertEqual(state["count"],1)
            pc.reset_mock()
            server._clipboard_monitor._last_change = ("n",1)
            server._clipboard_monitor._own_changes = [("n",2)]
            await server._clipboard_monitor._check()
            pc.assert_not_called()

    async def test_native_copy_monitor_ignores_startup_and_transfers_new_content(self):
        from handset import clipboard
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardContent

        class Monitor:
            def __init__(self, rsd, on_change, allow_full_pull):
                self._on_change = on_change
                self._last_change = ("initial clipboard", 1)
                self.allow_full_pull = allow_full_pull

            async def start(self):
                pass

            async def _check(self):
                pass

        server = SimpleNamespace(_clipboard_monitor=None, _rsd=object(),
                                 _on_device_clipboard=Mock(), _stop_clipboard_monitor=AsyncMock())
        state = {"enabled":True,"phase":"waiting","message":"","count":0}
        with (patch.object(screen, "_SERVER", server), patch.object(screen, "_SYNC_STATE", state),
              patch("pymobiledevice3.remote.core_device.pasteboard_service.PasteboardMonitor", Monitor),
              patch.object(clipboard, "_windows_clipboard") as pc):
            await screen._configure_clipboard_sync(server, True)
            self.assertTrue(server._clipboard_monitor.allow_full_pull)
            pc.assert_not_called()
            server._clipboard_monitor._on_change(PasteboardContent(text="copied through native menu"))
            await asyncio.gather(*server._pane_sync_tasks)
            pc.assert_called_once_with("copied through native menu")
            self.assertEqual(screen.clipboard_sync_status()["count"], 1)

    async def test_report_waits_for_actual_hid_dispatch(self):
        server = SimpleNamespace(_hid_queue=asyncio.Queue(), _pane_key_acks={})
        session = SimpleNamespace(loop=asyncio.get_running_loop())
        with patch.object(screen, "_SESSION", session), patch.object(screen, "_SERVER", server):
            operation = asyncio.create_task(asyncio.to_thread(screen.send_key_report, [0xe3, 6]))
            path, body = await asyncio.wait_for(server._hid_queue.get(), 2)
            self.assertEqual(path, "/key")
            payload = json.loads(body)
            self.assertEqual(payload["usages"], [0xe3, 6])
            self.assertFalse(operation.done(), "enqueueing must not count as completed dispatch")
            server._pane_key_acks[payload["_pane_ack"]].set_result(None)
            await asyncio.wait_for(operation, 2)
            self.assertEqual(server._pane_key_acks, {})


if __name__ == "__main__":
    unittest.main()
