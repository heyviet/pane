"""Read what an iPhone copy actually carries, beyond plain-text and well-known image types.

pymobiledevice3 only extracts text from the plain-text UTIs and images from a short UTI list, and
it only asks the device for each item's primary representation. Many native copies fall through
that net:

* Messages (long-press a bubble -> Copy) leads with a rich attributed string; the plain text is a
  secondary, promised representation.
* Photos (Share/long-press -> Copy) publishes its picture lazily, or under a type the library does
  not list (``public.heif``, an archived ``UIImage``...).

This module recognises those representations, and pulls every representation when the cheap read
found nothing usable but the copy advertises something this module can decode.
"""
from __future__ import annotations

import asyncio
import html
import logging
import plistlib
import re
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)

FULL_PULL_TIMEOUT_SECONDS = 90.0
_MAX_SCAN_BYTES = 64 * 1024 * 1024

PLAIN_TEXT_UTIS = ("public.utf8-plain-text", "public.plain-text", "public.text")
UTF16_TEXT_UTIS = ("public.utf16-plain-text", "public.utf16-external-plain-text")
ATTRIBUTED_UTIS = ("com.apple.uikit.attributedstring", "com.apple.notes.richtext.attributedstring")
RTF_UTIS = ("public.rtf",)
HTML_UTIS = ("public.html",)
URL_UTIS = ("public.url", "public.url-name")
TEXT_UTIS = PLAIN_TEXT_UTIS + UTF16_TEXT_UTIS + ATTRIBUTED_UTIS + RTF_UTIS + HTML_UTIS + URL_UTIS

# Image types by preference. Bytes under any other UTI are still sniffed (see _sniff_image).
IMAGE_UTIS = (
    "public.png", "public.heic", "public.heif", "public.jpeg", "public.tiff", "com.compuserve.gif",
    "org.webmproject.webp", "com.microsoft.bmp", "public.jpeg-2000", "public.image",
    "com.apple.uikit.image",
)
WEB_ARCHIVE_UTI = "com.apple.webarchive"

# Placeholder characters iOS uses for inline attachments.
_PLACEHOLDERS = "\ufffc \t\r\n"
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"}


def _pasteboard(snapshot: dict[str, Any]) -> dict[str, Any]:
    inner = snapshot.get("pasteboard")
    return inner if isinstance(inner, dict) else snapshot


def _items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _pasteboard(snapshot).get("items") or [] if isinstance(item, dict)]


def _inline(item: dict[str, Any], uti: str) -> bytes | None:
    datum = (item.get("data") or {}).get(uti)
    raw = datum.get("data") if isinstance(datum, dict) else None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return bytes(raw) if isinstance(raw, (bytes, bytearray)) and raw else None


def advertised_types(snapshot: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(uti for item in _items(snapshot) for uti in item.get("types") or []))


# ----- images ---------------------------------------------------------------------------------

def _sniff_image(data: bytes) -> str | None:
    """UTI matching the bytes' image signature (MIME-compatible with pymobiledevice3), or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "public.png"
    if data.startswith(b"\xff\xd8\xff"):
        return "public.jpeg"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "public.heic"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "com.compuserve.gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "org.webmproject.webp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "public.tiff"
    return None


def _image_in_archive(raw: bytes) -> tuple[str, bytes] | None:
    """First image found inside a (keyed-archive / web-archive) property list. Never follows URLs."""
    if len(raw) > _MAX_SCAN_BYTES:
        return None
    try:
        root = plistlib.loads(raw)
    except Exception:
        return None
    stack, visited = [(root, 0)], 0
    while stack and visited < 20000:
        value, depth = stack.pop()
        visited += 1
        if depth > 32:
            continue
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in reversed(list(value.values())))
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in reversed(value))
        elif isinstance(value, (bytes, bytearray)):
            value = bytes(value)
            uti = _sniff_image(value)
            if uti is not None:
                return uti, value
            if value.startswith(b"bplist"):
                try:
                    stack.append((plistlib.loads(value), depth + 1))
                except Exception:
                    pass
    return None


def snapshot_image(snapshot: dict[str, Any]) -> tuple[str, bytes] | None:
    items = _items(snapshot)
    for item in items:
        # Known image types first, in preference order, then any other inline bytes that are an image.
        candidates = [uti for uti in IMAGE_UTIS if uti in (item.get("data") or {})]
        candidates += [uti for uti in item.get("data") or {} if uti not in candidates]
        for uti in candidates:
            data = _inline(item, uti)
            if data is None or uti in TEXT_UTIS:
                continue
            sniffed = _sniff_image(data)
            if sniffed is not None:
                return sniffed, data
    for item in items:
        for uti in (WEB_ARCHIVE_UTI, "com.apple.uikit.image"):
            data = _inline(item, uti)
            found = _image_in_archive(data) if data is not None else None
            if found is not None:
                return found
    return None


# ----- text -----------------------------------------------------------------------------------

def _attributed_text(raw: bytes) -> str | None:
    """Plain string of an NSKeyedArchiver-encoded NSAttributedString."""
    try:
        archive = plistlib.loads(raw)
        objects = archive["$objects"]
        root = archive["$top"]["root"]
    except Exception:
        return None

    def resolve(value, depth=0):
        while isinstance(value, plistlib.UID) and depth < 16:
            value = objects[value.data]
            depth += 1
        return value

    try:
        node = resolve(root)
        for _ in range(8):
            if isinstance(node, str):
                return node
            if isinstance(node, (bytes, bytearray)):
                return bytes(node).decode("utf-8")
            if not isinstance(node, dict):
                return None
            for key in ("NSString", "NS.string", "NSAttributedString", "NS.bytes"):
                if key in node:
                    node = resolve(node[key])
                    break
            else:
                return None
    except Exception:
        return None
    return None


_RTF_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "listtable",
    "listoverridetable", "expandedcolortbl", "filetbl", "themedata", "datastore", "latentstyles",
}
_RTF_TOKEN = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|[\r\n]+|([^\\{}\r\n]+)")


def _rtf_text(raw: bytes) -> str | None:
    source = raw.decode("latin-1")
    if not source.lstrip().startswith("{\\rtf"):
        return None
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skipping, uc, pending_skip = False, 1, 0
    for word, arg, hexcode, symbol, brace, literal in _RTF_TOKEN.findall(source):
        if brace == "{":
            stack.append((skipping, uc))
            continue
        if brace == "}":
            skipping, uc = stack.pop() if stack else (False, 1)
            continue
        if pending_skip and (hexcode or literal):
            if literal:
                drop = min(pending_skip, len(literal))
                literal, pending_skip = literal[drop:], pending_skip - drop
            else:
                pending_skip -= 1
                continue
        if symbol:
            if symbol == "*":
                skipping = True
            elif not skipping and symbol in "\\{}":
                out.append(symbol)
            elif not skipping and symbol == "~":
                out.append("\u00a0")
            elif not skipping and symbol in "\r\n":
                out.append("\n")
            continue
        if word:
            if word in _RTF_DESTINATIONS:
                skipping = True
            elif word == "uc" and arg:
                uc = int(arg)
            elif skipping:
                pass
            elif word == "u" and arg:
                out.append(chr(int(arg) % 0x10000 if int(arg) < 0 else int(arg)))
                pending_skip = uc
            elif word in {"par", "line", "sect", "page"}:
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            continue
        if hexcode and not skipping:
            out.append(bytes([int(hexcode, 16)]).decode("cp1252", errors="replace"))
        elif literal and not skipping:
            out.append(literal)
    return "".join(out).strip("\n") or None


class _HtmlText(HTMLParser):
    _BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "title"}:
            self._hidden += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "title"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def _html_text(raw: bytes) -> str | None:
    parser = _HtmlText()
    try:
        parser.feed(raw.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:
        return None
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", html.unescape("".join(parser.parts)))
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _url_text(raw: bytes) -> str | None:
    if raw.startswith(b"bplist"):
        try:
            value = plistlib.loads(raw)
        except Exception:
            return None
        while isinstance(value, list) and value:
            value = value[0]
        return value if isinstance(value, str) else None
    return raw.decode("utf-8", errors="replace").strip("\0")


def _decode_text(uti: str, raw: bytes) -> str | None:
    try:
        if uti in PLAIN_TEXT_UTIS:
            return raw.decode("utf-8")
        if uti in UTF16_TEXT_UTIS:
            return raw.decode("utf-16-le" if not raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16")
        if uti in ATTRIBUTED_UTIS:
            return _attributed_text(raw)
        if uti in RTF_UTIS:
            return _rtf_text(raw)
        if uti in HTML_UTIS:
            return _html_text(raw)
        if uti in URL_UTIS:
            return _url_text(raw)
    except (UnicodeDecodeError, ValueError):
        return None
    return None


def snapshot_text(snapshot: dict[str, Any]) -> str | None:
    """Best text of the copy: plain text first, then rich representations reduced to plain text."""
    for item in _items(snapshot):
        for uti in TEXT_UTIS:
            raw = _inline(item, uti)
            text = _decode_text(uti, raw) if raw is not None else None
            if text and text.strip(_PLACEHOLDERS):
                return text
    return None


# ----- reading --------------------------------------------------------------------------------

def content_from_snapshot(snapshot: dict[str, Any]):
    from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardContent
    image = snapshot_image(snapshot)
    text = snapshot_text(snapshot)
    return PasteboardContent(
        text=text,
        image=image[1] if image is not None else None,
        image_uti=image[0] if image is not None else None,
    )


def needs_full_pull(snapshot: dict[str, Any], content) -> bool:
    """Whether the copy advertises a decodable representation that the cheap read did not deliver.

    Only when the cheap read found nothing: a full read can stall the device for a minute (Notes'
    RTF), so ordinary text copies, which also promise rich types, must never trigger it.
    """
    if content:
        return False
    for item in _items(snapshot):
        for uti in item.get("types") or []:
            if _inline(item, uti) is not None:
                continue
            if (uti in IMAGE_UTIS or uti in TEXT_UTIS or uti == WEB_ARCHIVE_UTI
                    or uti.startswith("public.") and "image" in uti):
                return True
    return False


def describe(snapshot: dict[str, Any]) -> str:
    types = advertised_types(snapshot)
    return ", ".join(types[:8]) + (" …" if len(types) > 8 else "") if types else "nothing"


async def read(rsd, snapshot: dict[str, Any] | None = None, allow_full_pull: bool = True):
    """``(snapshot, content)`` of the device pasteboard; pulls every representation only if needed."""
    from pymobiledevice3.remote.core_device.pasteboard_service import (
        PasteboardService, POLICY_PROMISE_SECONDARY,
    )
    async with PasteboardService(rsd) as service:
        if snapshot is None:
            snapshot = await service.get(data_policy=POLICY_PROMISE_SECONDARY)
        content = content_from_snapshot(snapshot)
        if allow_full_pull and needs_full_pull(snapshot, content):
            logger.info("iPhone copy needs a full read (types: %s)", describe(snapshot))
            full = await asyncio.wait_for(service.get(), FULL_PULL_TIMEOUT_SECONDS)
            full_content = content_from_snapshot(full)
            if full_content:
                snapshot, content = full, full_content
    if not content:
        logger.info("iPhone copy has no transferable text or image (types: %s)", describe(snapshot))
    return snapshot, content
