import asyncio
import io
import plistlib
import unittest
from unittest.mock import patch

from PIL import Image

from handset import phone_pasteboard as pb


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 10, 10)).save(output, format="PNG")
    return output.getvalue()


def jpeg_bytes():
    output = io.BytesIO()
    Image.new("RGB", (4, 3), (10, 200, 10)).save(output, format="JPEG")
    return output.getvalue()


def keyed_attributed_string(text):
    U = plistlib.UID
    return plistlib.dumps({
        "$archiver": "NSKeyedArchiver", "$version": 100000, "$top": {"root": U(1)},
        "$objects": ["$null", {"NSString": U(2), "NSAttributes": U(3), "$class": U(4)},
                     {"NS.string": text, "$class": U(5)}, {"NSColor": U(0)},
                     {"$classname": "NSAttributedString", "$classes": ["NSAttributedString", "NSObject"]},
                     {"$classname": "NSMutableString", "$classes": ["NSMutableString", "NSString", "NSObject"]}],
    }, fmt=plistlib.FMT_BINARY)


def snapshot(*items):
    return {"pasteboard": {"items": [
        {"types": [uti for uti, _ in reps],
         "data": {uti: ({"data": data} if data is not None else {"isPromised": True, "isAvailable": False, "size": 10})
                  for uti, data in reps}}
        for reps in items]}}


class PhonePasteboardTests(unittest.TestCase):
    def test_messages_bubble_attributed_string_becomes_text(self):
        shot = snapshot([("com.apple.uikit.attributedstring", keyed_attributed_string("Hey, see you at 7 👋")),
                         ("public.utf8-plain-text", None)])
        self.assertEqual(pb.snapshot_text(shot), "Hey, see you at 7 👋")

    def test_messages_bubble_with_only_promised_text_requests_full_read(self):
        shot = snapshot([("com.apple.messages.private", b"\x00\x01"), ("public.utf8-plain-text", None)])
        content = pb.content_from_snapshot(shot)
        self.assertFalse(content)
        self.assertTrue(pb.needs_full_pull(shot, content))

    def test_plain_text_copy_with_promised_rich_types_does_not_stall(self):
        shot = snapshot([("public.utf8-plain-text", b"hello"), ("public.rtf", None), ("com.apple.webarchive", None)])
        content = pb.content_from_snapshot(shot)
        self.assertEqual(content.text, "hello")
        self.assertFalse(pb.needs_full_pull(shot, content))

    def test_rtf_and_html_fall_back_to_plain_text(self):
        bs = "\\"
        rtf = ("{" + bs + "rtf1" + bs + "ansi{" + bs + "fonttbl" + bs + "f0 Helvetica;}{" + bs + "colortbl;}"
               + bs + "f0 Caf" + bs + "'e9 " + bs + "u8217" + bs + "'92s" + bs + "par line two}").encode("ascii")
        self.assertEqual(pb._rtf_text(rtf), "Café ’s\nline two")
        html = b"<html><head><style>p{}</style></head><body><p>One &amp; two</p><div>three<br>four</div></body></html>"
        self.assertEqual(pb._html_text(html), "One & two\nthree\nfour")
        self.assertEqual(pb.snapshot_text(snapshot([("public.html", html)])), "One & two\nthree\nfour")

    def test_utf16_text(self):
        self.assertEqual(pb.snapshot_text(snapshot([("public.utf16-plain-text", "Việt".encode("utf-16-le"))])), "Việt")

    def test_photos_image_under_unlisted_type_is_sniffed(self):
        jpeg = jpeg_bytes()
        shot = snapshot([("com.apple.photos.asset", jpeg), ("public.jpeg", None)])
        content = pb.content_from_snapshot(shot)
        self.assertEqual(content.image, jpeg)
        self.assertEqual(content.image_mime, "image/jpeg")

    def test_heif_and_archived_uiimage_are_found(self):
        from pillow_heif import register_heif_opener
        register_heif_opener()
        output = io.BytesIO()
        Image.new("RGB", (16, 16), (1, 2, 3)).save(output, format="HEIF", quality=-1)
        heif = output.getvalue()
        self.assertEqual(pb.snapshot_image(snapshot([("public.heif", heif)])), ("public.heic", heif))
        png = png_bytes()
        archive = plistlib.dumps({"$objects": ["$null", {"UIImageData": png}]}, fmt=plistlib.FMT_BINARY)
        self.assertEqual(pb.snapshot_image(snapshot([("com.apple.uikit.image", archive)])), ("public.png", png))

    def test_promised_photo_triggers_full_read_and_uses_it(self):
        jpeg = jpeg_bytes()
        cheap = snapshot([("public.jpeg", None), ("public.heic", None)])
        full = snapshot([("public.jpeg", jpeg), ("public.heic", None)])

        class Service:
            def __init__(self, rsd): self.calls = []
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, data_policy=None):
                return full

        with patch("pymobiledevice3.remote.core_device.pasteboard_service.PasteboardService", Service):
            shot, content = asyncio.run(pb.read(object(), cheap))
        self.assertIs(shot, full)
        self.assertEqual(content.image, jpeg)

    def test_text_placeholder_alone_is_not_text(self):
        shot = snapshot([("public.utf8-plain-text", "￼".encode())])
        self.assertIsNone(pb.snapshot_text(shot))

    def test_movie_bytes_are_not_mistaken_for_heif(self):
        movie = b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00mdat"
        self.assertIsNone(pb._sniff_image(movie))


if __name__ == "__main__":
    unittest.main()
