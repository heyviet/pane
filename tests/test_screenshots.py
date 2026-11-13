import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handset import screenshots

PNG = screenshots.PNG_MAGIC + b"pixels"


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        screenshots._SAVED.clear()
        screenshots._last_folder = None
        self.folder = Path(tempfile.mkdtemp())
        self.asked = []

    def ask(self, name, folder):
        self.asked.append((name, folder))
        return self.folder / name

    def test_saves_where_the_user_chose_and_remembers_the_folder(self):
        result = screenshots.save(PNG, "iPhone screenshot 2026-11-13 at 13.05.22.png", ask=self.ask)
        self.assertEqual(Path(result["path"]).read_bytes(), PNG)
        self.assertEqual(result["name"], "iPhone screenshot 2026-11-13 at 13.05.22.png")
        screenshots.save(PNG, "second.png", ask=self.ask)
        self.assertEqual(self.asked[1][1], self.folder)

    def test_cancelled_dialog_writes_nothing(self):
        self.assertEqual(screenshots.save(PNG, "x.png", ask=lambda name, folder: None), {"cancelled": True})
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_rejects_non_png_data(self):
        with self.assertRaises(ValueError):
            screenshots.save(b"<html>", "x.png", ask=self.ask)
        self.assertEqual(self.asked, [])

    def test_unsafe_names_get_a_default(self):
        self.assertEqual(screenshots.safe_name("..\\..\\evil"), "iPhone screenshot.png")
        self.assertEqual(screenshots.safe_name("shot"), "shot.png")

    def test_only_saved_files_can_be_opened_or_revealed(self):
        other = self.folder / "other.exe"
        other.write_bytes(b"MZ")
        with self.assertRaises(ValueError):
            screenshots.open_file(str(other))
        with self.assertRaises(ValueError):
            screenshots.show_in_folder(str(other))
        saved = screenshots.save(PNG, "iPhone screenshot 2026-11-13 at 13.46.22.png", ask=self.ask)["path"]
        with patch("subprocess.Popen") as popen:
            screenshots.show_in_folder(saved)
        self.assertEqual(popen.call_args.args[0], f'explorer /select,"{Path(saved).resolve()}"')

    def test_moved_screenshot_is_reported(self):
        saved = screenshots.save(PNG, "shot.png", ask=self.ask)["path"]
        Path(saved).unlink()
        with self.assertRaisesRegex(ValueError, "moved or deleted"):
            screenshots.open_file(saved)


if __name__ == "__main__":
    unittest.main()
