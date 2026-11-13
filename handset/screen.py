"""Xcode's device screen, on Windows.

After Trust, iOS 17.4+ exposes CoreDeviceProxy over the USB service broker,
or RemotePairing over Wi-Fi once paired for it. Either tunnel carries a
display service. The phone then sends the screen as HEVC video. The browser
decodes it.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import threading

from handset.device import developer_mode_enabled
from handset.mux import MuxError, list_devices
from handset.wifi import wifi_paired_udids, wifi_tunnel

DEVELOPER_MODE_MESSAGE = (
    "The picture stays black because Developer Mode is off. On the iPhone open Settings, "
    "Privacy & Security, Developer Mode, turn it on, and let the phone restart. After it boots, "
    "unlock it, tap Turn On, then press Show screen again."
)

VIEW_URL = "http://127.0.0.1:8766/"
_LOCK = threading.Lock()
_START_LOCK = threading.Lock()
_SESSION = None
_SERVER = None
_SYNC_LOCK = threading.Lock()
_SYNC_STATE = {"enabled": False, "phase": "off", "message": "Native iOS Copy sync is off.", "count": 0}
_STATE = {"phase": "idle", "udid": None, "url": None, "error": None, "note": None, "transport": None}


class _ScreenSession:
    def __init__(self, udid: str, transport: str):
        self.udid = udid
        self.transport = transport
        self.ready = threading.Event()
        self.loop = None
        self.task = None
        self.thread = threading.Thread(target=self.run, name="handset-screen", daemon=True)

    def run(self):
        async def serve():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            self.ready.set()
            await _serve(self.udid, self.transport)

        try:
            asyncio.run(serve())
            _set(phase="idle", url=None, error="The screen stream stopped.")
        except asyncio.CancelledError:
            pass
        except Exception as error:
            _set(phase="error", url=None, error=_plain(error))
        finally:
            self.ready.set()

    def stop(self) -> bool:
        if not self.thread.is_alive():
            return True
        if not self.ready.wait(2):
            return False
        try:
            if self.loop is not None and self.task is not None:
                self.loop.call_soon_threadsafe(self.task.cancel)
        except RuntimeError:
            pass  # The loop finished between checking the thread and cancelling.
        self.thread.join(15)
        return not self.thread.is_alive()


def screen_status() -> dict:
    with _LOCK:
        return dict(_STATE)


def restart_screen(udid: str | None = None) -> dict:
    """Nudge the device encoder and keep the HTTP stream on 8766 alive."""
    import http.client

    chosen = (udid or "").strip() or _STATE.get("udid") or _only_udid()
    nudged = False
    for path in ("/restart", "/pli"):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8766, timeout=20)
            conn.request("POST", path, body=b"", headers={})
            response = conn.getresponse()
            response.read()
            conn.close()
            nudged = True
        except OSError:
            continue
    with _LOCK:
        running = _STATE.get("phase") == "running"
    if running:
        return _set(note="Stream restarted. If the picture is still black, press Restart screen again.")
    if chosen:
        return start_screen(chosen, _STATE.get("transport") or "auto")
    return _set(error="Connect the iPhone first, then start mirroring.")


def start_screen(udid: str | None, transport: str = "auto") -> dict:
    # Only one display server / userspace tunnel can own port 8766 at a time.
    with _START_LOCK:
        return _start_screen(udid, transport)


def _start_screen(udid: str | None, transport: str) -> dict:
    global _SESSION
    usb = _usb_udids()
    wifi = wifi_paired_udids()
    chosen = (udid or "").strip() or _only_udid(usb, wifi, transport)
    if not chosen:
        if transport == "wifi":
            return _set(phase="idle", udid=None, url=None, error="Set up Wi-Fi once with the cable plugged in.")
        return _set(phase="idle", udid=None, url=None, error="Connect the iPhone first.")

    if transport == "auto":
        transport = "usb" if chosen in usb else "wifi"
    if transport == "usb" and chosen not in usb:
        return _set(phase="error", udid=chosen, url=None, error="The iPhone is not on the cable. Plug it in or choose Wi-Fi.")
    if transport == "wifi" and chosen not in wifi:
        return _set(phase="error", udid=chosen, url=None, error="Set up Wi-Fi once with the cable plugged in.")
    if transport == "usb" and developer_mode_enabled(chosen) is False:
        return _set(phase="error", udid=chosen, url=None, error=DEVELOPER_MODE_MESSAGE)

    with _LOCK:
        if (_STATE["phase"] in {"starting", "running"}
                and _STATE["udid"] == chosen and _STATE["transport"] == transport
                and _SESSION is not None and _SESSION.thread.is_alive()):
            return dict(_STATE)
    if _SESSION is not None and not _SESSION.stop():
        return _set(error="The previous stream is still closing. Try Start mirroring again.")
    with _LOCK:
        note = "Looking for the iPhone on Wi-Fi…" if transport == "wifi" else None
        _STATE.update(phase="starting", udid=chosen, url=None, error=None, note=note, transport=transport)
    _SESSION = _ScreenSession(chosen, transport)
    _SESSION.thread.start()
    return screen_status()


DISPLAY_SERVICE = "com.apple.coredevice.displayservice"


def _open_tunnel(udid: str, transport: str):
    if transport == "wifi":
        return wifi_tunnel(udid)
    from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel

    return PreferredRsdTunnel(serial=udid, autopair=True)


async def _serve(udid: str, transport: str) -> None:
    from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer
    from pymobiledevice3.services.mobile_image_mounter import auto_mount

    for attempt in range(2):
        async with _open_tunnel(udid, transport) as rsd:
            if DISPLAY_SERVICE in (rsd.peer_info or {}).get("Services", {}):
                await _stream(rsd, udid, ScreenStreamServer)
                return
            if attempt:
                raise RuntimeError(
                    "The developer image mounted, but the iPhone still offers no display service. "
                    "Unplug it, plug it back in, and press Show screen again."
                )
            _set(note="Mounting the developer image on the iPhone. This downloads it the first time.")
            await auto_mount(rsd)


async def _stream(rsd, udid: str, ScreenStreamServer) -> None:
    global _SERVER
    from pymobiledevice3.remote.core_device.display_service import is_media_in_use_error

    server = ScreenStreamServer(
        rsd,
        bind="127.0.0.1",
        http_port=8766,
        audio_default_on=False,
    )

    # The library's HTTP /key endpoint acknowledges enqueueing, not dispatch.
    # Clipboard actions need an acknowledgement after the real HID RPC finishes.
    pending_keys = {}
    original_key = server._handle_key

    async def acknowledged_key(body):
        request_id = json.loads(body).get("_pane_ack")
        future = pending_keys.get(request_id)
        try:
            result = await original_key(body)
            if future is not None and not future.done():
                if result[0] == 200:
                    future.set_result(None)
                else:
                    future.set_exception(RuntimeError("The iPhone rejected a keyboard report."))
            return result
        except Exception as error:
            if future is not None and not future.done():
                future.set_exception(error)
            raise

    server._handle_key = acknowledged_key
    server._pane_key_acks = pending_keys

    async def _eager_video_only() -> None:
        # Windows cannot decode AAC-ELD; skip eager audio so logs stay clean and
        # the display tunnel still comes up for /codec and /stream.bin.
        try:
            await server._ensure_fresh_stream(force=False)
        except Exception as error:
            if is_media_in_use_error(error):
                raise
            import logging

            logging.getLogger(__name__).warning(
                "eager video start failed (will retry on /codec)",
                exc_info=True,
            )

    server._eager_stream_start = _eager_video_only
    _set(phase="running", udid=udid, url=VIEW_URL, error=None, note=None)
    _SERVER = server
    try:
        if clipboard_sync_status()["enabled"]:
            await _configure_clipboard_sync(server, True)
        await server.serve()
    finally:
        _SERVER = None
        for task in getattr(server, "_pane_sync_tasks", set()):
            task.cancel()
        if clipboard_sync_status()["enabled"]:
            _sync_state(phase="waiting", message="Clipboard sync will resume when mirroring connects.")
        for future in pending_keys.values():
            if not future.done():
                future.set_exception(RuntimeError("The screen connection stopped."))


def _sync_state(**values):
    with _SYNC_LOCK:
        _SYNC_STATE.update(values)


def clipboard_sync_status():
    with _SYNC_LOCK:
        return {"ok": True, **_SYNC_STATE}


async def _configure_clipboard_sync(server, enabled):
    if not enabled:
        await server._stop_clipboard_monitor()
        server._pane_sync_active = False
        for task in getattr(server, "_pane_sync_tasks", set()):
            task.cancel()
        return
    if getattr(server, "_pane_sync_active", False):
        return
    from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardMonitor
    server._pane_sync_tasks = set()

    def active():
        return clipboard_sync_status()["enabled"] and _SERVER is server

    async def receive(content, video=None):
        from handset.clipboard import receive_phone_clipboard
        try:
            result = await asyncio.to_thread(receive_phone_clipboard, content.text, content.image, active, video)
            if result and active():
                with _SYNC_LOCK:
                    _SYNC_STATE.update(phase="watching", message=result, count=_SYNC_STATE["count"] + 1)
        except Exception:
            if active():
                _sync_state(phase="error", message="Could not copy the iPhone clipboard to PC. Try Get iPhone clipboard.")

    def changed(content):
        server._on_device_clipboard(content)
        if active():
            _sync_state(phase="copying", message="Copying iPhone clipboard to PC…")
            task = asyncio.create_task(receive(content))
            server._pane_sync_tasks.add(task)
            task.add_done_callback(server._pane_sync_tasks.discard)

    monitor = server._clipboard_monitor
    if monitor is None:
        monitor = PasteboardMonitor(server._rsd, changed, allow_full_pull=True)
        server._clipboard_monitor = monitor
    else:
        monitor._on_change = changed
        monitor._allow_full_pull = True
    original_check = monitor._check

    async def check():
        if active():
            _sync_state(phase="reading", message="Reading iPhone copy… rich images can take about a minute.")
        try:
            # Movie copies may include a still-image thumbnail. Inspect the real
            # movie representation first, instead of synchronizing its cover.
            import hashlib
            from handset.video_clipboard import movie_types, read_video
            from pymobiledevice3.remote.core_device.pasteboard_service import (
                PasteboardService, PasteboardContent, POLICY_PROMISE_SECONDARY, _change_id,
            )
            async with PasteboardService(server._rsd) as service:
                snapshot = await service.get(data_policy=POLICY_PROMISE_SECONDARY)
            if movie_types(snapshot):
                change = _change_id(snapshot)
                if change is not None and change == monitor._last_change:
                    return
                if change is not None and change in monitor._own_changes:
                    monitor._last_change = change
                    return
                video = await read_video(server._rsd, snapshot)
                if video is not None:
                    monitor._last_change = change
                    recent = monitor._recent_host_content.get(hashlib.sha1(video.data).hexdigest(), 0)
                    if recent and time.monotonic() - recent < 60:
                        return
                    if active():
                        _sync_state(phase="copying", message="Copying iPhone video to PC…")
                        task = asyncio.create_task(receive(PasteboardContent(), video))
                        server._pane_sync_tasks.add(task)
                        task.add_done_callback(server._pane_sync_tasks.discard)
                    return
            await original_check()
        except ValueError as error:
            if active():
                _sync_state(phase="error", message=str(error))
        finally:
            if active() and clipboard_sync_status()["phase"] == "reading":
                _sync_state(phase="watching", message="Watching native iOS Copy. No new transferable content yet.")

    monitor._check = check
    server._pane_sync_active = True
    await monitor.start()
    for _ in range(20):
        if monitor._last_change is not None:
            break
        await asyncio.sleep(0.1)
    _sync_state(phase="watching", message="Native iOS Copy will now update the PC clipboard.")


def set_clipboard_sync(enabled):
    _sync_state(enabled=enabled, phase="waiting" if enabled else "off",
                message="Clipboard sync starts with mirroring." if enabled else "Native iOS Copy sync is off.")
    session, server = _SESSION, _SERVER
    if session is not None and session.loop is not None and server is not None:
        operation = asyncio.run_coroutine_threadsafe(_configure_clipboard_sync(server, enabled), session.loop)
        try:
            operation.result(timeout=15)
        except TimeoutError:
            operation.cancel()
            _sync_state(phase="error", message="Clipboard sync did not respond. Restart mirroring.")
    return clipboard_sync_status()


def send_key_report(usages):
    session, server = _SESSION, _SERVER
    if session is None or session.loop is None or server is None:
        raise RuntimeError("Start mirroring before using clipboard shortcuts.")

    async def dispatch():
        request_id = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        server._pane_key_acks[request_id] = future
        try:
            server._hid_queue.put_nowait(("/key", json.dumps({"usages": usages, "_pane_ack": request_id}).encode()))
            await asyncio.wait_for(future, 12)
        finally:
            server._pane_key_acks.pop(request_id, None)

    operation = asyncio.run_coroutine_threadsafe(dispatch(), session.loop)
    try:
        operation.result(timeout=15)
    except TimeoutError:
        operation.cancel()
        raise RuntimeError("The iPhone keyboard did not respond. Restart mirroring and try again.")


def read_rich_clipboard():
    """Explicit image copies may need Notes' secondary web-archive representation."""
    session, server = _SESSION, _SERVER
    if session is None or session.loop is None or server is None:
        raise RuntimeError("Start mirroring before reading the iPhone clipboard.")

    async def read():
        from pymobiledevice3.remote.core_device.pasteboard_service import read_pasteboard
        _, content = await read_pasteboard(server._rsd, allow_full_pull=True)
        return {"text": content.text, "image": content.image}

    operation = asyncio.run_coroutine_threadsafe(read(), session.loop)
    try:
        return operation.result(timeout=90)
    except TimeoutError:
        operation.cancel()
        raise RuntimeError("The app took too long to provide its copied image. Copy the image itself from Photos or use the Files panel.")


def _media_operation(coroutine):
    session = _SESSION
    if session is None or session.loop is None or _SERVER is None:
        coroutine.close()
        raise RuntimeError("Start mirroring before using video clipboard transfer.")
    operation = asyncio.run_coroutine_threadsafe(coroutine, session.loop)
    try:
        return operation.result(timeout=100)
    except TimeoutError:
        operation.cancel()
        raise RuntimeError("The video clipboard did not respond. Try copying the video itself, or use Files.")


def set_video_clipboard(content):
    server = _SERVER
    async def send():
        import hashlib
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
        from handset.video_clipboard import movie_item
        monitor = server._clipboard_monitor
        if monitor is not None:
            # Mark host-origin video so it is not reflected back by clipboard sync.
            monitor._recent_host_content[hashlib.sha1(content.data).hexdigest()] = time.monotonic()
        async with PasteboardService(server._rsd) as service:
            reply = await asyncio.wait_for(service.set([movie_item(content)]), 20)
        if monitor is not None:
            from pymobiledevice3.remote.core_device.pasteboard_service import _change_id
            change = _change_id(reply)
            if change is not None:
                monitor._own_changes = [*monitor._own_changes, change][-16:]
                monitor._recent_host_content.pop(hashlib.sha1(content.data).hexdigest(), None)
    return _media_operation(send())


def read_video_clipboard():
    server = _SERVER
    async def read():
        from handset.video_clipboard import read_video
        return await read_video(server._rsd)
    return _media_operation(read())


def _usb_udids() -> list[str]:
    try:
        return [device["udid"] for device in list_devices()]
    except MuxError:
        return []


def _only_udid(usb: list[str] | None = None, wifi: list[str] | None = None, transport: str = "auto") -> str | None:
    usb = _usb_udids() if usb is None else usb
    wifi = wifi_paired_udids() if wifi is None else wifi
    if transport != "wifi" and len(usb) == 1:
        return usb[0]
    if transport != "usb" and len(wifi) == 1:
        return wifi[0]
    return None


def _set(**values) -> dict:
    with _LOCK:
        _STATE.update(values)
        return dict(_STATE)


def _plain(error: Exception) -> str:
    text = str(error).strip() or type(error).__name__
    name = type(error).__name__
    if name == "WifiNotFoundError":
        return text
    if name in {"ConnectionFailedToUsbmuxdError", "MuxError"} or "27015" in text or "usbmux" in text.lower():
        return "Apple Mobile Device Service is not running. Install Apple Devices, reconnect the iPhone, and tap Trust."
    if name == "UserspaceTunnelUnavailableError":
        return "This phone did not open the display tunnel. It needs iOS 17.4 or later, Trust, and Developer Mode."
    if "CoreDeviceProxy" in text or name == "InvalidServiceError":
        return "The phone did not offer its display tunnel. Turn on Developer Mode, unlock the phone, and tap Trust."
    if name == "DeviceNotFoundError":
        return "The iPhone is not on the cable. Plug it in, unlock it, and press Show screen again."
    if name in {"DeveloperModeIsNotEnabledError", "DeveloperModeError"}:
        return DEVELOPER_MODE_MESSAGE
    if "PasswordProtected" in text or "locked" in text.lower():
        return "Unlock the iPhone and keep it unlocked, then press Show screen again."
    if "PairingDialog" in text or "Trust" in text:
        return "Unlock the iPhone and tap Trust, then show the screen again."
    return text
