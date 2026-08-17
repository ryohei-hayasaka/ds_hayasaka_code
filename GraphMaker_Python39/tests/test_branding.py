import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tga_analyzer.branding import APP_DISPLAY_NAME, MAIN_WINDOW_TITLE
from tga_analyzer.import_profiles import default_user_profiles_dir
from tga_analyzer.settings import default_settings_path, load_last_root, save_last_root


class BrandingTests(unittest.TestCase):
    def test_display_name_and_window_title_are_graphmaker(self):
        self.assertEqual(APP_DISPLAY_NAME, "GraphMaker Python 3.9互換版")
        self.assertEqual(
            MAIN_WINDOW_TITLE,
            "GraphMaker Python 3.9互換版 — TGA / DSC / IR / UV-Vis / GPC / 粒度分布",
        )

    def test_local_data_paths_use_graphmaker(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/LocalData"}):
            self.assertEqual(default_settings_path().parts[-2:], ("GraphMaker_Python39", "settings.json"))
            self.assertEqual(default_user_profiles_dir().parts[-2:], ("GraphMaker_Python39", "profiles"))

    def test_settings_do_not_read_or_modify_existing_editions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            existing_root = base / "existing_data"
            existing_root.mkdir()
            original_settings = base / "GraphMaker" / "settings.json"
            original_settings.parent.mkdir()
            original_settings.write_text(
                json.dumps({"last_root": str(existing_root)}), encoding="utf-8"
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": str(base)}):
                self.assertIsNone(load_last_root())
                python39_root = base / "python39_data"
                python39_root.mkdir()
                save_last_root(python39_root)
                self.assertEqual(load_last_root(), python39_root)
            self.assertEqual(
                json.loads(original_settings.read_text(encoding="utf-8"))["last_root"],
                str(existing_root),
            )


if __name__ == "__main__":
    unittest.main()
