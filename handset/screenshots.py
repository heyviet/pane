"""Save screenshots of the mirrored screen to the PC, then open them or show them in Explorer.

The page captures the picture; this module asks where to save it with the native Windows
"Save as" dialog, so Pane knows the file's real path afterwards (a browser save dialog never
tells the page where the file went).
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_NAME = re.compile(r"[^\\/:*?\"<>|\x00-\x1f]{1,200}")

_LOCK = threading.Lock()
# Only files saved here in this session may be opened or revealed.
_SAVED: set[str] = set()
_last_folder: Path | None = None


def default_folder() -> Path:
    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


def safe_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.fullmatch(name):
        name = "iPhone screenshot.png"
    return name if name.lower().endswith(".png") else f"{name}.png"


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR), ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR), ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR), ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR), ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM), ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR), ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD),
    ]


_OFN_OVERWRITEPROMPT = 0x2
_OFN_NOCHANGEDIR = 0x8
_OFN_PATHMUSTEXIST = 0x800
_OFN_EXPLORER = 0x80000


def ask_save_path(name: str, folder: Path) -> Path | None:
    """Show the Windows "Save as" dialog over the Pane window; None if cancelled."""
    if sys.platform != "win32":
        raise RuntimeError("Saving screenshots needs Windows.")
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # this request thread hosts the dialog
    buffer = ctypes.create_unicode_buffer(name, 1024)
    dialog = _OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    # The window the user just clicked in: the dialog opens in front of it.
    dialog.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    dialog.lpstrFilter = "PNG image (*.png)\0*.png\0\0"
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrInitialDir = str(folder)
    dialog.lpstrTitle = "Save iPhone screenshot"
    dialog.lpstrDefExt = "png"
    dialog.Flags = _OFN_OVERWRITEPROMPT | _OFN_NOCHANGEDIR | _OFN_PATHMUSTEXIST | _OFN_EXPLORER
    if not ctypes.windll.comdlg32.GetSaveFileNameW(ctypes.byref(dialog)):
        if ctypes.windll.comdlg32.CommDlgExtendedError():
            raise RuntimeError("Windows could not open the save dialog.")
        return None
    return Path(buffer.value)


def save(data: bytes, name: str, ask=ask_save_path) -> dict:
    """Ask where to save ``data`` (a PNG) and write it. ``{"path"}``, or ``{"cancelled": True}``."""
    global _last_folder
    if not data.startswith(PNG_MAGIC) or len(data) > MAX_BYTES:
        raise ValueError("That screenshot is not a PNG image.")
    # One dialog at a time: a second click while it's open would stack another behind it.
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("The save dialog is already open.")
    try:
        target = ask(safe_name(name), _last_folder or default_folder())
    finally:
        _LOCK.release()
    if target is None:
        return {"cancelled": True}
    target.write_bytes(data)
    _last_folder = target.parent
    _SAVED.add(str(target.resolve()))
    return {"path": str(target), "name": target.name}


def _known(path: str) -> Path:
    resolved = Path(path).resolve() if isinstance(path, str) and path else None
    if resolved is None or str(resolved) not in _SAVED:
        raise ValueError("Pane can only open screenshots it saved.")
    if not resolved.is_file():
        raise ValueError("That screenshot was moved or deleted.")
    return resolved


def open_file(path: str) -> None:
    os.startfile(_known(path))  # the user's default image viewer


def show_in_folder(path: str) -> None:
    # Explorer opens the folder with the screenshot selected.
    subprocess.Popen(["explorer", f"/select,{_known(path)}"])
