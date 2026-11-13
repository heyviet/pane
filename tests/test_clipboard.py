import unittest
import io
import sys
from types import SimpleNamespace
from unittest.mock import patch, call
from PIL import Image
from handset import clipboard


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        reader = patch.object(clipboard, "_read_video", return_value=None)
        reader.start()
        self.addCleanup(reader.stop)

    @staticmethod
    def image_bytes(format="PNG"):
        image = Image.new("RGBA", (3, 2), (30, 90, 210, 128))
        image.putpixel((1, 0), (0, 240, 20, 255))
        output = io.BytesIO()
        (image.convert("RGB") if format == "JPEG" else image).save(output, format=format)
        return output.getvalue()

    def test_modifier_reports_are_separate_and_released(self):
        with patch.object(clipboard, "_send_key") as device, patch.object(clipboard.time, "sleep"):
            clipboard._shortcut(0x19)
        self.assertEqual(device.call_args_list, [call(u) for u in ([], [0xe3], [0xe3, 0x19], [0xe3], [])])

    def test_paste_sets_phone_clipboard_before_shortcut(self):
        operations = []
        with (patch.object(clipboard, "_windows_clipboard", return_value="Unicode Việt 👋"),
              patch.object(clipboard, "_device", side_effect=lambda *a: operations.append(a)),
              patch.object(clipboard, "_shortcut", side_effect=lambda k: operations.append((k,)))):
            clipboard.clipboard_action("paste")
        self.assertEqual(operations, [("/clipboard", {"text": "Unicode Việt 👋"}), (0x19,)])

    def test_copy_writes_device_text_to_windows_after_copy(self):
        operations = []
        def device(*args):
            operations.append("read phone")
            return {"text": "copied selection"}
        with (patch.object(clipboard, "_windows_clipboard", side_effect=lambda t: operations.append(("write PC", t))),
              patch.object(clipboard, "_device", side_effect=device),
              patch.object(clipboard, "_shortcut", side_effect=lambda k: operations.append(("shortcut", k)))):
            clipboard.clipboard_action("copy")
        self.assertEqual(operations, [("shortcut", 6), "read phone", ("write PC", "copied selection")])

    def test_get_and_paste_device_do_not_overwrite_source_clipboard(self):
        with (patch.object(clipboard, "_windows_clipboard") as pc,
              patch.object(clipboard, "_device", return_value={"text": "on phone"}) as device,
              patch.object(clipboard, "_shortcut") as shortcut):
            clipboard.clipboard_action("get")
            shortcut.assert_not_called()
            pc.assert_called_once_with("on phone")
            device.reset_mock()
            pc.reset_mock()
            clipboard.clipboard_action("paste-device")
            shortcut.assert_called_once_with(0x19)
            device.assert_not_called()
            pc.assert_not_called()

    def test_empty_pc_clipboard_sends_no_shortcut(self):
        with (patch.object(clipboard, "_windows_clipboard", return_value=""),
              patch.object(clipboard, "_device") as device,
              patch.object(clipboard, "_shortcut") as shortcut):
            with self.assertRaises(ValueError):
                clipboard.clipboard_action("paste")
            device.assert_not_called()
            shortcut.assert_not_called()

    def test_image_paste_uploads_raw_png_before_paste(self):
        image = self.image_bytes()
        operations = []
        with (patch.object(clipboard, "_windows_clipboard", return_value=image),
              patch.object(clipboard, "_device", side_effect=lambda *a: operations.append(a)),
              patch.object(clipboard, "_shortcut", side_effect=lambda k: operations.append((k,)))):
            result = clipboard.clipboard_action("paste")
        self.assertEqual(operations, [("/clipboard/image", image), (0x19,)])
        self.assertIn("image", result["message"])

    def test_send_preserves_text_whitespace_without_issuing_any_keys(self):
        text = "  exact text\t\nends here"
        with (patch.object(clipboard, "_windows_clipboard", return_value=text),
              patch.object(clipboard, "_device") as device,
              patch.object(clipboard, "_shortcut") as shortcut):
            clipboard.clipboard_action("send")
        device.assert_called_once_with("/clipboard", {"text":text})
        shortcut.assert_not_called()

    def test_phone_image_is_preferred_over_text_representation(self):
        image = self.image_bytes()
        with (patch.object(clipboard, "_device", side_effect=[{"text":"image caption", "image":{"type":"image/png"}}, image]) as device,
              patch.object(clipboard, "_windows_clipboard") as pc):
            clipboard.clipboard_action("get")
        self.assertEqual(device.call_args_list, [call("/clipboard"), call("/clipboard/image")])
        pc.assert_called_once_with(image)

    def test_notes_secondary_image_is_resolved_for_explicit_get(self):
        image = self.image_bytes()
        with (patch.object(clipboard, "_device", return_value={"text":None,"image":None}),
              patch.object(clipboard, "_rich_clipboard", return_value={"text":None,"image":image}) as rich,
              patch.object(clipboard, "_windows_clipboard") as pc):
            result = clipboard.clipboard_action("get")
        rich.assert_called_once_with()
        pc.assert_called_once_with(image)
        self.assertIn("image", result["message"])

    def test_windows_png_and_dib_are_read_and_written_with_pixel_integrity(self):
        formats = {}
        native = SimpleNamespace(
            RegisterClipboardFormat=lambda _: 50000,
            OpenClipboard=lambda: None,
            CloseClipboard=lambda: None,
            EmptyClipboard=lambda: formats.clear(),
            SetClipboardData=lambda f, data: formats.__setitem__(f, data),
            GetClipboardData=lambda f: formats[f],
            IsClipboardFormatAvailable=lambda f: f in formats,
        )
        constants = SimpleNamespace(CF_DIBV5=17, CF_DIB=8, CF_UNICODETEXT=13, CF_HDROP=15)
        with patch.dict(sys.modules, {"win32clipboard":native, "win32con":constants}):
            source = self.image_bytes()
            clipboard._windows_clipboard(source)
            self.assertEqual(set(formats), {50000, 8})
            actual = clipboard._windows_clipboard()
            with Image.open(io.BytesIO(actual)) as image, Image.open(io.BytesIO(source)) as expected:
                self.assertEqual(image.tobytes(), expected.tobytes())
            # Screenshot tools often supply only DIB. Validate that fallback too.
            formats.pop(50000)
            actual = clipboard._windows_clipboard()
            with Image.open(io.BytesIO(actual)) as image:
                self.assertEqual(image.size, (3, 2))
                self.assertEqual(image.getpixel((1, 0)), (0, 240, 20, 255))

    def test_jpeg_is_normalized_and_invalid_or_oversize_images_fail(self):
        result = clipboard._png(self.image_bytes("JPEG"))
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(ValueError):
            clipboard._png(b"not an image")
        with patch.object(clipboard, "MAX_IMAGE_PIXELS", 1), self.assertRaises(ValueError):
            clipboard._png(self.image_bytes())

    def test_native_iphone_heic_is_converted_to_windows_png(self):
        from pillow_heif import register_heif_opener
        register_heif_opener()
        source = Image.new("RGB", (32, 24), (20, 130, 210))
        output = io.BytesIO()
        source.save(output, format="HEIF", quality=-1)
        result = clipboard._png(output.getvalue())
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, source.size)

    def test_device_image_transport_uses_binary_not_json(self):
        image = self.image_bytes()
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = image
        with patch.object(clipboard.http.client, "HTTPConnection", return_value=connection):
            self.assertEqual(clipboard._device("/clipboard/image"), image)
            clipboard._device("/clipboard/image", image)
        self.assertEqual(connection.request.call_args, call("POST", "/clipboard/image", body=image, headers={"Content-Type":"image/png"}))

    def test_native_copy_sync_writes_text_and_images_without_keys(self):
        with patch.object(clipboard, "_windows_clipboard") as pc:
            clipboard.receive_phone_clipboard("native copy", None, lambda: True)
            pc.assert_called_once_with("native copy")
            pc.reset_mock()
            image = self.image_bytes()
            clipboard.receive_phone_clipboard(None, image, lambda: True)
            pc.assert_called_once_with(image)

    def test_disabling_sync_prevents_late_clipboard_overwrites(self):
        with patch.object(clipboard, "_windows_clipboard") as pc:
            clipboard.receive_phone_clipboard("stale copy", None, lambda: False)
            pc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
