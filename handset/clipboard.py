"""Explicit Windows/iPhone clipboard actions, independent of browser permissions."""
import http.client
import io
import json
import threading
import time
import struct
import uuid
from pathlib import Path
from handset.phone_videos import PhotosVideoCopy, valid_path
from handset.video_clipboard import EXTENSION_TYPES, VideoContent, video_from_files

_ACTION_LOCK = threading.Lock()
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


def _png(data):
    from PIL import Image, ImageOps, UnidentifiedImageError
    from pillow_heif import register_heif_opener
    register_heif_opener()
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Clipboard image is too large. Use the Files panel instead.")
            output = io.BytesIO()
            ImageOps.exif_transpose(image).convert("RGBA").save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("This clipboard image cannot be decoded. Copy it as a screenshot or use the Files panel.") from error
    result = output.getvalue()
    if len(result) > MAX_IMAGE_BYTES:
        raise ValueError("Clipboard images must be under 32 MB. Use the Files panel instead.")
    return result


def _dib(png):
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        # Windows apps without PNG support can consume the standard DIB fallback.
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        output = io.BytesIO()
        background.save(output, format="BMP")
    return output.getvalue()[14:]


def _send_key(usages):
    from handset.screen import send_key_report
    send_key_report(usages)


def _rich_clipboard():
    from handset.screen import read_rich_clipboard
    return read_rich_clipboard()


def _read_video():
    from handset.screen import read_video_clipboard
    return read_video_clipboard()


def _send_video(content):
    from handset.screen import set_video_clipboard
    set_video_clipboard(content)


def _windows_clipboard(text=None):
    import win32clipboard
    import win32con

    png_format = win32clipboard.RegisterClipboardFormat("PNG")
    video_path = None
    if isinstance(text, VideoContent):
        video_path = text.path
        if video_path is None:
            folder = Path.home() / "Downloads" / "Pane" / "Clipboard videos"
            folder.mkdir(parents=True, exist_ok=True)
            video_path = folder / (f"iPhone video-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}" + Path(text.name).suffix)
            video_path.write_bytes(text.data)
    image = _png(text) if isinstance(text, bytes) else None
    dib = _dib(image) if image is not None else None
    for attempt in range(10):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            if attempt == 9:
                raise RuntimeError("The PC clipboard is busy. Close any clipboard dialog and try again.")
            time.sleep(0.02)
    try:
        if text is None:
            image_formats = (png_format, win32con.CF_DIBV5, win32con.CF_DIB)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                content = list(win32clipboard.GetClipboardData(win32con.CF_HDROP))
                image_formats = ()
            for image_format in image_formats:
                if win32clipboard.IsClipboardFormatAvailable(image_format):
                    content = bytes(win32clipboard.GetClipboardData(image_format))
                    break
            else:
                if not image_formats:
                    pass  # File references are read only after releasing the Windows clipboard.
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    content = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                else:
                    raise ValueError("The PC clipboard contains no text or image. Copy something first.")
        else:
            win32clipboard.EmptyClipboard()
            if video_path is not None:
                drop = struct.pack("<IiiII", 20, 0, 0, 0, 1) + (str(video_path.resolve()) + "\0\0").encode("utf-16le")
                win32clipboard.SetClipboardData(win32con.CF_HDROP, drop)
                effect = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
                win32clipboard.SetClipboardData(effect, struct.pack("<I", 1))
            elif image is not None:
                win32clipboard.SetClipboardData(png_format, image)
                win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
            else:
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    if text is None:
        if isinstance(content, list):
            return video_from_files(content)
        return _png(content) if isinstance(content, bytes) else content


def _device(path, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", 8766, timeout=20)
    try:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        connection.request("POST" if body is not None else "GET", path, body=body,
                           headers={"Content-Type": "image/png" if isinstance(payload, bytes) else "application/json"})
        response = connection.getresponse()
        data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("The iPhone clipboard content exceeds 32 MB. Use the Files panel instead.")
        if response.status != 200:
            raise RuntimeError(f"iPhone {path} returned HTTP {response.status}. Restart mirroring and try again.")
        if path == "/clipboard":
            return json.loads(data)
        return data if path == "/clipboard/image" and payload is None else None
    finally:
        connection.close()


def _shortcut(key):
    # UIKit needs the modifier transition before the character transition.
    # Give the remote HID queue time between each report, including release.
    try:
        for usages in ([], [0xe3], [0xe3, key], [0xe3]):
            _send_key(usages)
            time.sleep(0.08)
    finally:
        _send_key([])
    time.sleep(0.15)


def _video_library():
    from handset.screen import phone_video_library
    return phone_video_library()


def _fetch_video(path):
    from handset.screen import fetch_phone_video
    return fetch_phone_video(path)


def clipboard_action(action, path=None):
    if action in {"sync-on", "sync-off", "sync-status"}:
        from handset.screen import clipboard_sync_status, set_clipboard_sync
        return clipboard_sync_status() if action == "sync-status" else set_clipboard_sync(action == "sync-on")
    if action == "video-library":
        return {"ok": True, **_video_library()}
    if action == "pick-video":
        # Streaming a large clip takes a while; hold the clipboard lock only to publish it.
        local = _fetch_video(valid_path(path))
        with _ACTION_LOCK:
            _windows_clipboard(VideoContent(b"", EXTENSION_TYPES[local.suffix.lower()], local.name, local))
        return {"ok": True, "message": f"{local.name} copied to PC. Paste into Explorer or an app that accepts video files."}
    with _ACTION_LOCK:
        if action in {"paste", "send"}:
            content = _windows_clipboard()
            if not content:
                raise ValueError("The PC clipboard is empty. Copy text, an image, or one MP4/MOV/M4V file first.")
            if isinstance(content, VideoContent):
                _send_video(content)
                kind = "video"
            elif isinstance(content, bytes):
                if len(content) > MAX_IMAGE_BYTES:
                    raise ValueError("Clipboard images must be under 32 MB. Use the Files panel instead.")
                _device("/clipboard/image", content)
                kind = "image"
            else:
                if len(content.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("Clipboard text is too large. Use the Files panel instead.")
                _device("/clipboard", {"text": content})
                kind = "text"
            if action == "paste":
                _shortcut(0x19)
                return {"ok": True, "message": f"PC {kind} sent; Paste requested on iPhone."}
            return {"ok": True, "message": f"PC {kind} is on the iPhone clipboard. Hold on a field and choose Paste."}
        if action in {"copy", "cut", "get"}:
            if action != "get":
                _shortcut(0x06 if action == "copy" else 0x1b)
            try:
                video = _read_video()
            except PhotosVideoCopy as notice:
                return {"ok": True, "pickVideo": True, "message": str(notice)}
            if video is not None:
                _windows_clipboard(video)
                return {"ok": True, "message": "iPhone video copied to PC. Paste into an app that accepts video files, or into Explorer to save it."}
            content = _device("/clipboard")
            rich_image = None
            if not content.get("image") and not content.get("text"):
                # The fast endpoint deliberately skips images embedded in rich
                # Notes/Safari selections. Only this explicit action resolves them.
                rich = _rich_clipboard()
                rich_image = rich.get("image")
                if rich.get("text"):
                    content["text"] = rich["text"]
            if rich_image:
                _windows_clipboard(rich_image)
                kind = "image"
            elif content.get("image"):
                _windows_clipboard(_device("/clipboard/image"))
                kind = "image"
            else:
                text = content.get("text")
                if not isinstance(text, str) or not text:
                    raise ValueError("The iPhone clipboard contains no text or image. Select and copy something on iPhone first.")
                _windows_clipboard(text)
                kind = "text"
            return {"ok": True, "message": f"iPhone clipboard {kind} copied to PC."}
        if action in {"select-all", "undo", "paste-device"}:
            _shortcut({"select-all": 0x04, "undo": 0x1d, "paste-device": 0x19}[action])
            return {"ok": True, "message": "Shortcut sent to iPhone."}
        raise ValueError("Unknown clipboard action.")


def receive_phone_clipboard(text, image, active, video=None):
    """Called for a real iOS Copy notification, never for an old snapshot or host echo."""
    with _ACTION_LOCK:
        if not active():
            return None
        if video is not None:
            _windows_clipboard(video)
            return "iPhone video copied to PC. Paste into an app that accepts video files."
        if image:
            _windows_clipboard(image)
            return "iPhone image copied to PC."
        if isinstance(text, str) and text:
            _windows_clipboard(text)
            return "iPhone text copied to PC."
        return None
