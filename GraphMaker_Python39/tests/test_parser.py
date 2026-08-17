import tempfile
import unittest
from pathlib import Path

from tga_analyzer.parser import TgaDataError, load_tga_csv


VALID_CSV = """Record_ID,Time_min,Temperature_C,Mass_mg
1,0.0,25,10.0
2,0.1,26,9.5
3,0.2,27,9.0
"""


class ParserTests(unittest.TestCase):
    def _write(self, folder: Path, text: str, encoding: str = "utf-8") -> Path:
        path = folder / "sample.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_normalizes_first_mass_to_100_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_tga_csv(self._write(Path(temp_dir), VALID_CSV))
        self.assertEqual(curve.temperatures, (25.0, 26.0, 27.0))
        self.assertEqual(curve.mass_mg, (10.0, 9.5, 9.0))
        self.assertEqual(curve.weight_percent, (100.0, 95.0, 90.0))

    def test_reads_utf8_bom_and_cp932(self):
        for encoding in ("utf-8-sig", "cp932"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp_dir:
                curve = load_tga_csv(self._write(Path(temp_dir), VALID_CSV, encoding))
                self.assertEqual(curve.point_count, 3)

    def test_allows_extra_columns(self):
        text = VALID_CSV.replace("Mass_mg", "Mass_mg,Comment").replace("10.0\n", "10.0,a\n")
        text = text.replace("9.5\n", "9.5,b\n").replace("9.0\n", "9.0,c\n")
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_tga_csv(self._write(Path(temp_dir), text))
        self.assertEqual(curve.point_count, 3)

    def test_missing_header_reports_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), "Temperature_C,Mass_mg\n25,10\n")
            with self.assertRaisesRegex(TgaDataError, "Record_ID"):
                load_tga_csv(path)

    def test_invalid_numeric_value_reports_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(
                Path(temp_dir),
                "Record_ID,Time_min,Temperature_C,Mass_mg\n1,0,25,10\n2,0.1,bad,9\n",
            )
            with self.assertRaisesRegex(TgaDataError, "3行目"):
                load_tga_csv(path)

    def test_zero_initial_mass_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(
                Path(temp_dir), "Record_ID,Time_min,Temperature_C,Mass_mg\n1,0,25,0\n"
            )
            with self.assertRaisesRegex(TgaDataError, "0より大きい"):
                load_tga_csv(path)

    def test_empty_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), "")
            with self.assertRaisesRegex(TgaDataError, "空のCSV"):
                load_tga_csv(path)


if __name__ == "__main__":
    unittest.main()

