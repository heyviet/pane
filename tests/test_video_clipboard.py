import tempfile
import unittest
import struct
import sys
import plistlib
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from handset import clipboard
from handset.video_clipboard import VideoContent, video_from_files, video_from_snapshot, video_content, movie_item

MOVIE = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class VideoClipboardTests(unittest.TestCase):
    def test_notes_native_copy_extracts_embedded_movie_without_following_urls(self):
        movie = MOVIE + b"\x00\x00\x00\x0cmdatTEST"
        archive = plistlib.dumps({"$archiver":"NSKeyedArchiver", "$objects":[
            {"$classname":"ICNotePasteboardData"},
            {"NS.data":movie}, {"NS.relative":"file:///private/attachment.mov"},
        ]}, fmt=plistlib.FMT_BINARY)
        snapshot={"pasteboard":{"items":[{"types":["com.apple.notes.richtext"],
                  "data":{"com.apple.notes.richtext":{"data":archive}}}]}}
        video=video_from_snapshot(snapshot)
        self.assertEqual(video.data,movie)
        self.assertEqual(video.uti,"public.mpeg-4")

    def test_notes_rich_text_without_a_movie_does_not_get_saved_as_video(self):
        archive=plistlib.dumps({"$objects":[{"NS.data":b"ordinary note data"},
            {"NS.relative":"file:///private/attachment.mov"}]},fmt=plistlib.FMT_BINARY)
        snapshot={"items":[{"types":["com.apple.notes.richtext"],"data":{
            "com.apple.notes.richtext":{"data":archive}}}]}
        self.assertIsNone(video_from_snapshot(snapshot))

    def test_movie_advertises_concrete_and_generic_types_for_native_apps(self):
        video = VideoContent(MOVIE,"public.mpeg-4","clip.mp4")
        item = movie_item(video)
        self.assertEqual(item["types"],["public.mpeg-4","public.movie"])
        self.assertEqual(item["data"]["public.movie"]["data"],MOVIE)
    def test_copied_video_file_uses_native_video_pasteboard_before_paste(self):
        video = VideoContent(MOVIE, "public.mpeg-4", "clip.mp4")
        order = []
        with (patch.object(clipboard, "_windows_clipboard", return_value=video),
              patch.object(clipboard, "_send_video", side_effect=lambda v: order.append(v)),
              patch.object(clipboard, "_shortcut", side_effect=lambda k: order.append(k))):
            result = clipboard.clipboard_action("paste")
        self.assertEqual(order, [video, 0x19])
        self.assertIn("video", result["message"])

    def test_file_types_and_limit_are_checked_before_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.MP4"
            path.write_bytes(MOVIE)
            video = video_from_files([path])
            self.assertEqual((video.data, video.uti, video.path), (MOVIE, "public.mpeg-4", path))
            with patch("handset.video_clipboard.MAX_VIDEO_BYTES", 4), self.assertRaises(ValueError):
                video_from_files([path])
            with self.assertRaises(ValueError):
                video_from_files([path, path])
            invalid = Path(folder) / "not-video.txt"
            invalid.write_text("text")
            with self.assertRaises(ValueError):
                video_from_files([invalid])

    def test_native_video_does_not_get_misread_as_its_thumbnail(self):
        snapshot = {"pasteboard":{"items":[{"types":["public.png","public.mpeg-4"],"data":{
            "public.png":{"data":b"thumbnail"}, "public.mpeg-4":{"data":MOVIE}}}]}}
        self.assertEqual(video_from_snapshot(snapshot).data, MOVIE)

    def test_device_only_file_reference_is_not_saved_as_video_bytes(self):
        with self.assertRaisesRegex(ValueError, "reference"):
            video_content(b"file:///private/var/mobile/movie.mp4", "public.mpeg-4")

    def test_get_video_sets_windows_file_clipboard(self):
        video = VideoContent(MOVIE, "public.mpeg-4", "clip.mp4")
        with (patch.object(clipboard, "_read_video", return_value=video),
              patch.object(clipboard, "_windows_clipboard") as pc,
              patch.object(clipboard, "_device") as image_or_text):
            clipboard.clipboard_action("get")
        pc.assert_called_once_with(video)
        image_or_text.assert_not_called()

    def test_native_video_copy_sync_does_not_write_a_thumbnail(self):
        video = VideoContent(MOVIE, "public.mpeg-4", "clip.mp4")
        with patch.object(clipboard, "_windows_clipboard") as pc:
            clipboard.receive_phone_clipboard("caption", b"thumbnail", lambda: True, video)
        pc.assert_called_once_with(video)

    def test_windows_video_file_reference_is_pasteable_and_round_trips(self):
        formats = {}
        def get(format):
            if format == 15:
                raw = formats[format]
                self.assertEqual(struct.unpack("<IiiII", raw[:20]), (20,0,0,0,1))
                return tuple(raw[20:].decode("utf-16le").rstrip("\0").split("\0"))
            return formats[format]
        native = SimpleNamespace(RegisterClipboardFormat=lambda n: 50000 if n=="PNG" else 50001,
            OpenClipboard=lambda:None, CloseClipboard=lambda:None, EmptyClipboard=lambda:formats.clear(),
            IsClipboardFormatAvailable=lambda f:f in formats, GetClipboardData=get,
            SetClipboardData=lambda f,data:formats.__setitem__(f,data))
        constants = SimpleNamespace(CF_DIBV5=17, CF_DIB=8, CF_UNICODETEXT=13, CF_HDROP=15)
        with tempfile.TemporaryDirectory() as folder:
            with (patch.dict(sys.modules,{"win32clipboard":native,"win32con":constants}),
                  patch.object(clipboard.Path,"home",return_value=Path(folder))):
                clipboard._windows_clipboard(VideoContent(MOVIE,"public.mpeg-4","clip.mp4"))
                result=clipboard._windows_clipboard()
            self.assertEqual(result.data,MOVIE)
            self.assertTrue(result.path.is_file())
            self.assertEqual(formats[50001],struct.pack("<I",1))


if __name__ == "__main__":
    unittest.main()
