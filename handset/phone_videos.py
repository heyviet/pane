"""iPhone videos read straight from the device's media folder (AFC), for Photos video copies.

Photos' ••• -> Copy on a video only *promises* the movie: the device's pasteboard service never
delivers the bytes (or even the asset id, which sits behind the movie) to a remote reader. The
original file is still on the device, so Pane lists the videos and lets the user choose:

* ``/DCIM/1xxAPPLE/IMG_nnnn.MOV``: videos recorded or saved on this iPhone.
* ``/PhotoData/CPLAssets/groupN/<asset UUID>.MOV``: iCloud Photos originals downloaded to the
  iPhone (with Optimize iPhone Storage, an older video lands here when Photos downloads it).

Pane cannot tell *which* video was copied, so the picker offers two views:

* Recent: videos that arrived on the iPhone (recorded, saved or downloaded from iCloud) or were
  opened in Photos in the last day. Photos writes ``/PhotoData/Metadata/<path minus suffix>.JPG``
  the first time a video is opened full screen (and for its neighbours), which is the "opened"
  signal.
* All videos: everything, newest recording first, like the Photos library. The recording date
  comes from the movie header (``moov/mvhd``), because an iCloud download's file dates are its
  download time.

AFC requests are pipelined, so the ~1000 directory listings and stats a full scan needs take well
under a second, and anything already seen is cached.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

VIDEO_SUFFIXES = {".mov": "com.apple.quicktime-movie", ".mp4": "public.mpeg-4", ".m4v": "com.apple.m4v-video"}
PHOTOS_ASSET_UTI = "com.apple.mobileslideshow.asset.localidentifier"
_DIR = re.compile(r"\d{3}[A-Z_]{1,16}")
_GROUP = re.compile(r"group\d{1,6}")
_PATH = re.compile(r"(?:/DCIM/\d{3}[A-Z_]{1,16}/[A-Za-z0-9_\-]{1,64}"
                   r"|/PhotoData/CPLAssets/group\d{1,6}/[A-Fa-f0-9\-]{36})\.(?:mov|mp4|m4v)", re.IGNORECASE)
CPL_ROOT = "/PhotoData/CPLAssets"
METADATA_ROOT = "/PhotoData/Metadata"
RECENT_SECONDS = 24 * 3600
RECENT_LIMIT = 12
SETTLE_SECONDS = 10
_PARALLEL = 64
_MAC_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc).timestamp()
DOWNLOAD_FOLDER = Path.home() / "Downloads" / "Pane" / "Clipboard videos"

# path -> {"size", "added", "recorded"}; files in these folders never change once written.
_FILES: dict[str, dict] = {}
# metadata JPG path -> its birth time (when Photos first opened that video).
_OPENED: dict[str, float] = {}
# One AFC connection per tunnel, reused for listings and thumbnails.
_AFC: dict[int, tuple[object, object]] = {}


class PhotosVideoCopy(ValueError):
    """The copy is a Photos video whose data the device will not hand over."""

    def __init__(self):
        super().__init__("Photos keeps copied videos on the iPhone. Choose the video in Pane's picker to copy it to PC.")


def is_photos_video(snapshot) -> bool:
    """A Photos library copy whose primary representation is a (promised) movie."""
    from handset.video_clipboard import VIDEO_TYPES
    pasteboard = snapshot.get("pasteboard", snapshot)
    for item in pasteboard.get("items") or []:
        types = item.get("types") or []
        if types and types[0] in VIDEO_TYPES and PHOTOS_ASSET_UTI in types:
            return True
    return False


def valid_path(path) -> str:
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        raise ValueError("Unknown iPhone video.")
    return path


def _is_video(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in VIDEO_SUFFIXES


def _metadata_path(path: str) -> str:
    return f"{METADATA_ROOT}{PurePosixPath(path).with_suffix('.JPG')}"


async def _gather(limit, calls):
    """Run ``calls`` (zero-argument coroutine factories) pipelined, at most ``limit`` at once."""
    gate = asyncio.Semaphore(limit)

    async def run(call):
        async with gate:
            try:
                return await call()
            except Exception:
                return None
    return await asyncio.gather(*(run(call) for call in calls))


async def _shared_afc(rsd):
    from pymobiledevice3.services.afc import AfcService
    entry = _AFC.get(id(rsd))
    if entry is not None and entry[0] is rsd:
        return entry[1]
    afc = AfcService(rsd)
    await afc.__aenter__()
    _AFC[id(rsd)] = (rsd, afc)
    return afc


async def _with_afc(rsd, work):
    """Run ``work(afc)`` on the shared connection, reconnecting once if it has dropped."""
    for attempt in range(2):
        afc = await _shared_afc(rsd)
        try:
            return await work(afc)
        except (ConnectionError, OSError, EOFError, asyncio.IncompleteReadError):
            stale = _AFC.pop(id(rsd), None)
            if stale is not None:
                try:
                    await stale[1].__aexit__(None, None, None)
                except Exception:
                    pass
            if attempt:
                raise


async def _listing(afc, root: str, pattern) -> list[str]:
    """Video paths in every ``pattern``-named folder of ``root``."""
    try:
        folders = [f for f in await afc.listdir(root) if pattern.fullmatch(f)]
    except Exception:
        return []
    listings = await _gather(_PARALLEL, [lambda f=f: afc.listdir(f"{root}/{f}") for f in folders])
    return [f"{root}/{f}/{n}" for f, names in zip(folders, listings) for n in names or [] if _is_video(n)]


async def _opened_jpgs(afc, root: str, pattern) -> set[str]:
    try:
        folders = [f for f in await afc.listdir(root) if pattern.fullmatch(f)]
    except Exception:
        return set()
    listings = await _gather(_PARALLEL, [lambda f=f: afc.listdir(f"{root}/{f}") for f in folders])
    return {f"{root}/{f}/{n}" for f, names in zip(folders, listings) for n in names or [] if n.upper().endswith(".JPG")}


def _mvhd_time(box: bytes) -> float | None:
    """Recording time (POSIX seconds) from the children of a ``moov`` box."""
    offset = 0
    while offset + 8 <= len(box):
        size = int.from_bytes(box[offset:offset + 4], "big")
        if box[offset + 4:offset + 8] == b"mvhd":
            version = box[offset + 8] if len(box) > offset + 8 else 0
            width = 8 if version == 1 else 4
            raw = box[offset + 12:offset + 12 + width]
            if len(raw) < width:
                return None
            seconds = int.from_bytes(raw, "big")
            # Camera clips always carry a real date; 0 (or nonsense) means "unknown".
            when = _MAC_EPOCH + seconds
            return when if 946684800 < when < time.time() + 86400 else None
        if size < 8:
            return None
        offset += size
    return None


async def _recorded(afc, path: str, size: int) -> float | None:
    """Walk the top-level boxes (ftyp, wide, mdat, moov...) with tiny reads to reach ``mvhd``."""
    handle = await afc.fopen(path)
    try:
        offset = 0
        for _ in range(32):
            if offset + 8 > size:
                return None
            head = await afc.pread(handle, 16, offset)
            if len(head) < 8:
                return None
            box_size, kind = int.from_bytes(head[:4], "big"), head[4:8]
            header = 8
            if box_size == 1 and len(head) >= 16:
                box_size, header = int.from_bytes(head[8:16], "big"), 16
            elif box_size == 0:
                box_size = size - offset
            if kind == b"moov":
                return _mvhd_time(await afc.pread(handle, min(box_size - header, 8192), offset + header))
            if box_size < header:
                return None
            offset += box_size
        return None
    finally:
        await afc.fclose(handle)


async def library(rsd, now: float | None = None) -> dict:
    """``{"recent": [...], "videos": [...]}``; each video is ``{path, name, size, added, recorded, opened, source}``."""
    now = time.time() if now is None else now

    async def scan(afc):
        paths = await _listing(afc, "/DCIM", _DIR) + await _listing(afc, CPL_ROOT, _GROUP)
        jpgs = (await _opened_jpgs(afc, f"{METADATA_ROOT}/DCIM", _DIR)
                | await _opened_jpgs(afc, f"{METADATA_ROOT}{CPL_ROOT}", _GROUP))
        new = [p for p in paths if p not in _FILES]
        stats = await _gather(_PARALLEL, [lambda p=p: afc.stat(p) for p in new])
        # A file written in the last few seconds may still be downloading: list it on a later refresh.
        fresh = [(p, s) for p, s in zip(new, stats) if s and now - s["st_mtime"].timestamp() > SETTLE_SECONDS]
        dates = await _gather(_PARALLEL, [lambda p=p, s=s: _recorded(afc, p, s["st_size"]) for p, s in fresh])
        for (path, info), recorded in zip(fresh, dates):
            added = (info.get("st_birthtime") or info.get("st_mtime")).timestamp()
            _FILES[path] = {"size": info["st_size"], "added": added, "recorded": recorded or added}
        wanted = {_metadata_path(p) for p in paths} & jpgs
        unseen = [j for j in wanted if j not in _OPENED]
        jpg_stats = await _gather(_PARALLEL, [lambda j=j: afc.stat(j) for j in unseen])
        for jpg, info in zip(unseen, jpg_stats):
            if info:
                _OPENED[jpg] = (info.get("st_birthtime") or info.get("st_mtime")).timestamp()
        return paths

    paths = await _with_afc(rsd, scan)
    videos = []
    for path in paths:
        info = _FILES.get(path)
        if info is None:
            continue
        videos.append({"path": path, "name": PurePosixPath(path).name, **info,
                       "opened": _OPENED.get(_metadata_path(path)),
                       "source": "iCloud" if path.startswith(CPL_ROOT) else "Camera roll"})
    videos.sort(key=lambda v: v["recorded"], reverse=True)

    def activity(video):
        return max(video["added"], video["opened"] or 0)
    recent = sorted((v for v in videos if now - activity(v) < RECENT_SECONDS), key=activity, reverse=True)
    return {"recent": recent[:RECENT_LIMIT], "videos": videos}


async def thumbnail(rsd, path: str) -> bytes | None:
    """A JPEG preview of the video, or None.

    Camera-roll videos have a per-asset thumbnail folder under ``/PhotoData/Thumbnails/V2``.
    iCloud originals downloaded into ``CPLAssets`` don't, so fall back to the poster frame Photos
    writes to ``/PhotoData/Metadata`` (``.JPG``, or the older ``.THM``) when the video is opened.
    """
    folder = f"/PhotoData/Thumbnails/V2{valid_path(path)}"
    metadata = _metadata_path(path)
    posters = (metadata, metadata[:-len(".JPG")] + ".THM")

    async def read(afc):
        try:
            names = await afc.listdir(folder)
        except Exception:
            names = []
        jpegs = sorted((n for n in names if n.lower().endswith(".jpg")), key=lambda n: n != "5005.JPG")
        if jpegs:
            return await afc.get_file_contents(f"{folder}/{jpegs[0]}")
        for poster in posters:
            try:
                data = await afc.get_file_contents(poster)
            except (ConnectionError, EOFError, asyncio.IncompleteReadError):
                raise  # let _with_afc reconnect
            except Exception:
                continue  # no such poster
            if data and data[:3] == b"\xff\xd8\xff":
                return data
        return None
    return await _with_afc(rsd, read)


async def fetch(rsd, path: str, folder: Path = DOWNLOAD_FOLDER) -> Path:
    """Stream the original video to ``folder`` and return its local path."""
    from pymobiledevice3.services.afc import AfcService
    source = PurePosixPath(valid_path(path))
    folder.mkdir(parents=True, exist_ok=True)
    # iCloud originals are named by asset UUID; give them a readable name.
    name = f"iPhone video-{source.stem[:8]}{source.suffix}" if str(source).startswith(CPL_ROOT) else source.name
    target = folder / name
    if target.exists():
        target = folder / f"{PurePosixPath(name).stem}-{time.strftime('%Y%m%d-%H%M%S')}{source.suffix}"
    partial = folder / f".{uuid.uuid4().hex}.part"
    try:
        # A connection of its own, so a long transfer never holds up thumbnails.
        async with AfcService(rsd) as afc:
            await afc.pull(str(source), str(partial), progress_bar=False)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target
