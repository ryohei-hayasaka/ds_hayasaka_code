import tempfile
import unittest
from pathlib import Path

from tga_analyzer.model import UV_VIS
from tga_analyzer.parser import UvVisDataError, load_uvvis_csv


VALID = """Record_ID,Wavelength_nm,Absorbance
1,200,0.123456
2,201,0.234567
3,202,0.345678
"""


class UvVisParserTests(unittest.TestCase):
    def _write(self, folder: Path, text: str, encoding: str = "utf-8-sig") -> Path:
        path = folder / "uvvis.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_reads_fixed_columns_without_changing_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_uvvis_csv(self._write(Path(temp_dir), VALID))
        self.assertEqual(curve.measurement_type, UV_VIS)
        self.assertEqual(curve.wavelengths_nm, (200.0, 201.0, 202.0))
        self.assertEqual(curve.uvvis_absorbance, (0.123456, 0.234567, 0.345678))
        self.assertEqual(curve.plot_x, curve.wavelengths_nm)
        self.assertEqual(curve.plot_y, curve.uvvis_absorbance)

    def test_reads_utf8_bom_utf8_and_cp932(self):
        for encoding in ("utf-8-sig", "utf-8", "cp932"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp_dir:
                curve = load_uvvis_csv(self._write(Path(temp_dir), VALID, encoding))
                self.assertEqual(curve.point_count, 3)

    def test_demo_has_601_points_and_200_to_800_nm(self):
        curve = load_uvvis_csv(Path("demo_data/UV-Vis/raw_data/UVVis_demo_01.csv"))
        self.assertEqual(curve.point_count, 601)
        self.assertEqual((curve.wavelengths_nm[0], curve.wavelengths_nm[-1]), (200.0, 800.0))

    def test_unknown_missing_invalid_and_nonfinite_values_are_rejected(self):
        cases = (
            (VALID.replace("Wavelength_nm", "Unknown_nm"), "必須列"),
            (VALID.replace("201,0.234567", "bad,0.234567"), "3行目"),
            (VALID.replace("0.234567", "nan"), "有限値"),
        )
        for text, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(UvVisDataError, message):
                    load_uvvis_csv(self._write(Path(temp_dir), text))

    def test_original_row_order_is_preserved_and_one_point_is_rejected(self):
        unordered = VALID.replace("200,0.123456\n2,201", "300,0.123456\n2,250")
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_uvvis_csv(self._write(Path(temp_dir), unordered))
        self.assertEqual(curve.wavelengths_nm, (300.0, 250.0, 202.0))
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(UvVisDataError, "不足"):
                load_uvvis_csv(self._write(Path(temp_dir), VALID.splitlines()[0] + "\n" + VALID.splitlines()[1]))


if __name__ == "__main__":
    unittest.main()
