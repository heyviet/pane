import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from handset import clipboard, phone_videos

ICLOUD = "5747CE98-CABB-4CF9-B108-D8DB9C510839"
JPEG = bytes([0xFF, 0xD8, 0xFF]) + b"thumb"
NOW = datetime(2026, 11, 12, 22, 0).timestamp()


def box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def movie(recorded: datetime, mdat=b"frames") -> bytes:
    """ftyp, mdat, then moov/mvhd, as an iPhone camera writes it."""
    seconds = int(recorded.timestamp() - datetime(1904, 1, 1, tzinfo=timezone.utc).timestamp())
    mvhd = box(b"mvhd", bytes(4) + seconds.to_bytes(4, "big") + bytes(92))
    return box(b"ftyp", b"qt  " + bytes(4)) + box(b"mdat", mdat) + box(b"moov", mvhd + box(b"trak", bytes(16)))


def local(*args):
    return datetime(*args)


class FakeAfc:
    """Device paths -> (bytes, birth time). Thumbnail and metadata entries are ordinary files."""

    def __init__(self, files):
        self.files = files
        self.handles = {}
        self.opened = 0

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def listdir(self, folder):
        prefix = folder.rstrip("/") + "/"
        names = {p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)}
        if not names:
            raise FileNotFoundError(folder)
        return sorted(names) + (["MISC"] if folder in {"/DCIM", "/PhotoData/CPLAssets"} else [])

    async def stat(self, path):
        data, when = self.files[path]
        return {"st_size": len(data), "st_birthtime": when, "st_mtime": when}

    async def fopen(self, path, mode="r"):
        self.opened += 1
        self.handles[self.opened] = self.files[path][0]
        return self.opened

    async def pread(self, handle, size, offset):
        return self.handles[handle][offset:offset + size]

    async def fclose(self, handle):
        self.handles.pop(handle)

    async def get_file_contents(self, path):
        return self.files[path][0]

    async def pull(self, src, dst, progress_bar=True):
        Path(dst).write_bytes(self.files[src][0])


def device():
    return {
        "/DCIM/103APPLE/IMG_3005.MOV": (movie(datetime(2026, 3, 20, 22, 11, tzinfo=timezone.utc)), local(2026, 3, 20, 22, 11)),
        "/DCIM/106APPLE/IMG_6330.MP4": (movie(datetime(2026, 11, 2, 18, 55, tzinfo=timezone.utc)), local(2026, 11, 2, 18, 55)),
        "/DCIM/106APPLE/IMG_6331.MP4": (movie(datetime(2026, 11, 2, 18, 57, tzinfo=timezone.utc)), local(2026, 11, 2, 18, 57)),
        "/DCIM/106APPLE/IMG_6332.HEIC": (b"photo", local(2026, 11, 3)),
        # An old iCloud video Photos downloaded tonight: its file dates are the download time.
        f"/PhotoData/CPLAssets/group249/{ICLOUD}.MOV": (movie(datetime(2025, 8, 23, 17, 1, tzinfo=timezone.utc)), local(2026, 11, 12, 21, 37)),
        "/PhotoData/CPLAssets/group12/AAAAAAAA-0000-0000-0000-000000000000.HEIC": (b"photo", local(2026, 11, 12)),
        # Photos writes this preview the first time IMG_3005 is opened full screen.
        "/PhotoData/Metadata/DCIM/103APPLE/IMG_3005.JPG": (JPEG, local(2026, 11, 12, 21, 44)),
        "/PhotoData/Metadata/DCIM/103APPLE/IMG_3005.THM": (JPEG, local(2026, 3, 20, 22, 12)),
        "/PhotoData/Thumbnails/V2/DCIM/106APPLE/IMG_6331.MP4/5005.JPG": (JPEG, local(2026, 11, 3)),
        f"/PhotoData/Thumbnails/V2/PhotoData/CPLAssets/group249/{ICLOUD}.MOV/5005.JPG": (JPEG + b"cpl", local(2026, 11, 12)),
    }


class PhoneVideoTests(unittest.TestCase):
    def setUp(self):
        phone_videos._FILES.clear()
        phone_videos._OPENED.clear()
        phone_videos._AFC.clear()
        self.files = device()
        self.afc = FakeAfc(self.files)
        patcher = patch("pymobiledevice3.services.afc.AfcService", lambda rsd: self.afc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rsd = object()

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def library(self, now=NOW):
        return self.run_async(phone_videos.library(self.rsd, now))

    def test_all_videos_are_ordered_by_recording_date_from_the_movie_header(self):
        names = [v["name"] for v in self.library()["videos"]]
        self.assertEqual(names, ["IMG_6331.MP4", "IMG_6330.MP4", "IMG_3005.MOV", f"{ICLOUD}.MOV"])
        icloud = self.library()["videos"][-1]
        self.assertEqual(icloud["recorded"], datetime(2025, 8, 23, 17, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(icloud["source"], "iCloud")

    def test_recent_includes_old_video_opened_in_photos_and_icloud_download(self):
        recent = self.library()["recent"]
        self.assertEqual([v["name"] for v in recent], ["IMG_3005.MOV", f"{ICLOUD}.MOV"])
        self.assertEqual(recent[0]["opened"], local(2026, 11, 12, 21, 44).timestamp())

    def test_refresh_picks_up_a_new_download_and_reads_only_new_files(self):
        self.library()
        opened_before = self.afc.opened
        path = "/PhotoData/CPLAssets/group7/BBBBBBBB-1111-2222-3333-444444444444.MOV"
        self.files[path] = (movie(datetime(2024, 1, 2, tzinfo=timezone.utc)), local(2026, 11, 12, 21, 58))
        library = self.library()
        self.assertEqual(library["recent"][0]["path"], path)
        self.assertEqual(self.afc.opened - opened_before, 1)

    def test_a_file_still_downloading_is_listed_only_once_it_settles(self):
        path = "/PhotoData/CPLAssets/group7/BBBBBBBB-1111-2222-3333-444444444444.MOV"
        self.files[path] = (movie(datetime(2024, 1, 2, tzinfo=timezone.utc), b"part"), datetime.fromtimestamp(NOW - 2))
        self.assertNotIn(path, [v["path"] for v in self.library()["videos"]])
        self.files[path] = (movie(datetime(2024, 1, 2, tzinfo=timezone.utc)), datetime.fromtimestamp(NOW - 2))
        later = self.library(NOW + 30)["videos"]
        self.assertEqual([v["size"] for v in later if v["path"] == path], [len(self.files[path][0])])

    def test_headerless_movie_falls_back_to_file_date(self):
        path = "/DCIM/106APPLE/IMG_6333.MOV"
        self.files[path] = (b"not a movie at all", local(2026, 11, 4))
        video = next(v for v in self.library()["videos"] if v["path"] == path)
        self.assertEqual(video["recorded"], video["added"])

    def test_thumbnail_and_fetch(self):
        self.assertEqual(self.run_async(phone_videos.thumbnail(self.rsd, "/DCIM/106APPLE/IMG_6331.MP4")), JPEG)
        self.assertIsNone(self.run_async(phone_videos.thumbnail(self.rsd, "/DCIM/106APPLE/IMG_6330.MP4")))
        path = f"/PhotoData/CPLAssets/group249/{ICLOUD}.MOV"
        self.assertEqual(self.run_async(phone_videos.thumbnail(self.rsd, path)), JPEG + b"cpl")
        with tempfile.TemporaryDirectory() as folder:
            first = self.run_async(phone_videos.fetch(self.rsd, path, Path(folder)))
            second = self.run_async(phone_videos.fetch(self.rsd, path, Path(folder)))
            self.assertEqual(first.name, "iPhone video-5747CE98.MOV")
            self.assertEqual(first.read_bytes(), self.files[path][0])
            self.assertNotEqual(first, second)
            self.assertEqual([p.name for p in Path(folder).iterdir() if p.name.startswith(".")], [])

    def test_paths_outside_the_video_folders_are_rejected(self):
        for path in ("/PhotoData/Photos.sqlite", "/DCIM/106APPLE/../../x.MOV", "/DCIM/106APPLE/IMG_1.HEIC", None,
                     "/PhotoData/CPLAssets/group1/../../Photos.sqlite", "/PhotoData/CPLAssets/group1/x.MOV"):
            with self.assertRaises(ValueError):
                phone_videos.valid_path(path)

    def test_photos_video_detection_needs_movie_first_and_asset_id(self):
        uti = phone_videos.PHOTOS_ASSET_UTI
        self.assertTrue(phone_videos.is_photos_video({"pasteboard": {"items": [{"types": ["public.mpeg-4", uti]}]}}))
        self.assertFalse(phone_videos.is_photos_video({"pasteboard": {"items": [{"types": ["public.jpeg", uti]}]}}))
        self.assertFalse(phone_videos.is_photos_video({"pasteboard": {"items": [{"types": ["public.mpeg-4"]}]}}))


class ClipboardVideoActionTests(unittest.TestCase):
    def test_get_on_photos_video_asks_for_picker(self):
        with (patch.object(clipboard, "_read_video", side_effect=phone_videos.PhotosVideoCopy()),
              patch.object(clipboard, "_device") as device, patch.object(clipboard, "_windows_clipboard") as pc):
            result = clipboard.clipboard_action("get")
        self.assertTrue(result["pickVideo"])
        device.assert_not_called()
        pc.assert_not_called()

    def test_video_library_action(self):
        with patch.object(clipboard, "_video_library", return_value={"recent": [], "videos": []}):
            self.assertEqual(clipboard.clipboard_action("video-library"), {"ok": True, "recent": [], "videos": []})

    def test_pick_video_puts_file_reference_on_pc_clipboard(self):
        local_file = Path("C:/tmp/IMG_6331.MP4")
        with (patch.object(clipboard, "_fetch_video", return_value=local_file) as fetch,
              patch.object(clipboard, "_windows_clipboard") as pc):
            result = clipboard.clipboard_action("pick-video", "/DCIM/106APPLE/IMG_6331.MP4")
        fetch.assert_called_once_with("/DCIM/106APPLE/IMG_6331.MP4")
        content = pc.call_args.args[0]
        self.assertEqual((content.path, content.uti), (local_file, "public.mpeg-4"))
        self.assertIn("IMG_6331.MP4", result["message"])
        with self.assertRaises(ValueError):
            clipboard.clipboard_action("pick-video", "/PhotoData/Photos.sqlite")


if __name__ == "__main__":
    unittest.main()
