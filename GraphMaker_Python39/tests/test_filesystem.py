import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tga_analyzer.filesystem import list_child_directories, list_csv_names


class FileSystemTests(unittest.TestCase):
    def test_lists_direct_csv_names_in_natural_order_without_opening_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "sample10.csv").write_text("should not be read", encoding="utf-8")
            (folder / "sample2.CSV").write_text("should not be read", encoding="utf-8")
            (folder / "notes.txt").write_text("ignored", encoding="utf-8")
            child = folder / "child"
            child.mkdir()
            (child / "nested.csv").write_text("not direct", encoding="utf-8")

            with patch.object(builtins, "open", side_effect=AssertionError("file opened")):
                names = list_csv_names(folder)

            self.assertEqual(names, ["sample2.CSV", "sample10.csv"])

    def test_lists_only_direct_child_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "folder10").mkdir()
            (folder / "folder2").mkdir()
            (folder / "a.csv").write_text("x", encoding="utf-8")
            self.assertEqual(
                [path.name for path in list_child_directories(folder)],
                ["folder2", "folder10"],
            )


if __name__ == "__main__":
    unittest.main()

