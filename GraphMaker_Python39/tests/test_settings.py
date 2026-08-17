import tempfile
import unittest
from pathlib import Path

from tga_analyzer.settings import load_last_root, save_last_root


class SettingsTests(unittest.TestCase):
    def test_round_trip_existing_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "data"
            root.mkdir()
            settings = Path(temp_dir) / "settings.json"
            save_last_root(root, settings)
            self.assertEqual(load_last_root(settings), root)

    def test_missing_root_returns_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Path(temp_dir) / "settings.json"
            settings.write_text('{"last_root": "Z:/missing"}', encoding="utf-8")
            self.assertIsNone(load_last_root(settings))


if __name__ == "__main__":
    unittest.main()

