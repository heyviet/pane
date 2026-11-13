"""Bounded, inline movie data for native clipboard transfer (no video conversion)."""
from dataclasses import dataclass
from pathlib import Path
import plistlib

MAX_VIDEO_BYTES = 32 * 1024 * 1024
VIDEO_TYPES = {
    "public.mpeg-4": ".mp4",
    "com.apple.quicktime-movie": ".mov",
    "com.apple.m4v-video": ".m4v",
    "public.movie": ".mp4",
    "public.video": ".mp4",
}
EXTENSION_TYPES = {".mp4":"public.mpeg-4", ".mov":"com.apple.quicktime-movie", ".m4v":"com.apple.m4v-video"}
RICH_VIDEO_TYPES = {"com.apple.notes.richtext", "com.apple.webarchive"}


@dataclass(frozen=True)
class VideoContent:
    data: bytes
    uti: str
    name: str
    path: Path | None = None


def movie_item(content):
    # Some apps ask for the generic movie type rather than its concrete subtype.
    types = list(dict.fromkeys((content.uti, "public.movie")))
    return {"types":types, "data":{uti:{"data":content.data} for uti in types}}


def video_content(data, uti, name=""):
    if uti not in VIDEO_TYPES:
        raise ValueError("Unsupported video format. Copy an MP4, MOV or M4V file.")
    if len(data) > MAX_VIDEO_BYTES:
        raise ValueError("Direct video clipboard paste supports up to 32 MB. Use the Files panel for larger videos.")
    # These supported formats are ISO media / QuickTime containers, not URLs or bookmarks.
    if len(data) < 12 or data[4:8] not in {b"ftyp", b"moov", b"wide", b"mdat", b"free", b"skip"}:
        raise ValueError("The app copied a video reference instead of video data. Copy the video itself or use the Files panel.")
    return VideoContent(bytes(data), uti, name or "iPhone video" + VIDEO_TYPES[uti])


def video_from_files(paths):
    if len(paths) != 1:
        raise ValueError("Copy one video file at a time for direct paste. Use Files for multiple files.")
    path = Path(paths[0])
    uti = EXTENSION_TYPES.get(path.suffix.lower())
    if not uti or not path.is_file():
        raise ValueError("Direct file clipboard paste supports MP4, MOV and M4V. Use the Files panel for other files.")
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError("Direct video clipboard paste supports up to 32 MB. Use the Files panel for larger videos.")
    with path.open("rb") as source:
        content = video_content(source.read(MAX_VIDEO_BYTES + 1), uti, path.name)
    return VideoContent(content.data, content.uti, content.name, path)


def movie_types(snapshot):
    """Representations that contain, or may embed, a movie."""
    pasteboard = snapshot.get("pasteboard", snapshot)
    return [uti for item in pasteboard.get("items", []) for uti in item.get("types", []) if uti in VIDEO_TYPES or uti in RICH_VIDEO_TYPES]


def _embedded_movie(raw):
    # Notes uses an NSKeyedArchive with the actual file in an NSData object.
    # Inspect inline bytes only: never follow NSURLs or device-local file paths.
    if len(raw) > MAX_VIDEO_BYTES * 4:
        raise ValueError("This Notes selection is too large. Copy just the video or use Files.")
    try:
        root = plistlib.loads(raw)
    except (ValueError, TypeError, plistlib.InvalidFileException):
        return None
    stack = [(root, 0)]
    visited = 0
    while stack and visited < 10000:
        value, depth = stack.pop()
        visited += 1
        if depth > 24:
            continue
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in reversed(list(value.values())))
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in reversed(value))
        elif isinstance(value, bytes):
            header = value[4:8]
            if header in {b"ftyp", b"moov", b"wide", b"mdat", b"free", b"skip"} and b"mdat" in value:
                uti = "com.apple.quicktime-movie" if header != b"ftyp" or value[8:12] == b"qt  " else "public.mpeg-4"
                return video_content(value, uti)
            if value.startswith(b"bplist"):
                try:
                    stack.append((plistlib.loads(value), depth + 1))
                except (ValueError, TypeError, plistlib.InvalidFileException):
                    pass
    return None


def video_from_snapshot(snapshot):
    pasteboard = snapshot.get("pasteboard", snapshot)
    for item in pasteboard.get("items", []):
        representations = item.get("data") or {}
        for uti in VIDEO_TYPES:
            value = representations.get(uti, {})
            raw = value.get("data")
            if isinstance(raw, (bytes, bytearray)) and raw:
                return video_content(raw, uti)
            if isinstance(value.get("size"), int) and value["size"] > MAX_VIDEO_BYTES:
                raise ValueError("This video exceeds the 32 MB clipboard limit. Use the Files panel instead.")
        for uti in RICH_VIDEO_TYPES:
            raw = representations.get(uti, {}).get("data")
            if isinstance(raw, bytes):
                embedded = _embedded_movie(raw)
                if embedded is not None:
                    return embedded
    return None


async def read_video(rsd, snapshot=None):
    import asyncio
    from handset.phone_videos import PhotosVideoCopy, is_photos_video
    from pymobiledevice3.remote.core_device.pasteboard_service import (
        PasteboardService, POLICY_ALL_PROMISED, POLICY_PROMISE_SECONDARY,
    )
    async with PasteboardService(rsd) as service:
        if snapshot is None:
            # Types first: Photos never provides a copied video's data, so asking for it would hang.
            if is_photos_video(await asyncio.wait_for(service.get(data_policy=POLICY_ALL_PROMISED), 15)):
                raise PhotosVideoCopy()
            snapshot = await asyncio.wait_for(service.get(data_policy=POLICY_PROMISE_SECONDARY), 30)
        if not movie_types(snapshot):
            return None
        content = video_from_snapshot(snapshot)
        if content is None:
            snapshot = await asyncio.wait_for(service.get(), 90)
            content = video_from_snapshot(snapshot)
        direct_movie = any(uti in VIDEO_TYPES for uti in movie_types(snapshot))
        if content is None and direct_movie:
            raise ValueError("This app did not provide the copied video's data. Copy the video itself or use Files.")
        return content
