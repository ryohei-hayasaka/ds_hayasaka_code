import tempfile
import unittest
from pathlib import Path

from tga_analyzer.model import DSC
from tga_analyzer.parser import DscDataError, load_dsc_csv


VALID_DSC = """Record_ID,Time_min,Temperature_C,HeatFlow_mW
1,0.0,25,-0.10
2,0.1,26,0.25
3,0.2,27,1.40
"""


class DscParserTests(unittest.TestCase):
    def _write(self, folder: Path, text: str, encoding: str = "utf-8") -> Path:
        path = folder / "dsc_sample.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_reads_temperature_heat_flow_and_time_without_sign_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_dsc_csv(self._write(Path(temp_dir), VALID_DSC))
        self.assertEqual(curve.measurement_type, DSC)
        self.assertEqual(curve.temperatures, (25.0, 26.0, 27.0))
        self.assertEqual(curve.heat_flow_mw, (-0.1, 0.25, 1.4))
        self.assertEqual(curve.time_min, (0.0, 0.1, 0.2))
        self.assertEqual(curve.plot_y, curve.heat_flow_mw)
        self.assertEqual(curve.heat_flow_unit, "mW")
        self.assertEqual(curve.source_heat_flow_header, "HeatFlow_mW")

    def test_reads_utf8_bom_and_cp932(self):
        for encoding in ("utf-8-sig", "cp932"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp_dir:
                curve = load_dsc_csv(self._write(Path(temp_dir), VALID_DSC, encoding))
                self.assertEqual(curve.point_count, 3)

    def test_missing_heat_flow_header_is_rejected(self):
        text = VALID_DSC.replace("HeatFlow_mW", "Signal_mW")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), text)
            with self.assertRaisesRegex(DscDataError, "HeatFlow_mW"):
                load_dsc_csv(path)

    def test_invalid_heat_flow_reports_line_number(self):
        text = VALID_DSC.replace("2,0.1,26,0.25", "2,0.1,26,bad")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), text)
            with self.assertRaisesRegex(DscDataError, "3行目"):
                load_dsc_csv(path)

    def test_empty_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), "")
            with self.assertRaisesRegex(DscDataError, "空のCSV"):
                load_dsc_csv(path)

    def test_accepts_exact_known_specific_heat_flow_units(self):
        for header, unit in (("HeatFlow_W_g", "W/g"), ("HeatFlow_mW_mg", "mW/mg")):
            text = VALID_DSC.replace("HeatFlow_mW", header)
            with self.subTest(header=header), tempfile.TemporaryDirectory() as temp_dir:
                curve = load_dsc_csv(self._write(Path(temp_dir), text))
                self.assertEqual(curve.heat_flow_unit, unit)

    def test_time_column_is_optional_for_dsc(self):
        text = "Temperature_C,HeatFlow_mW\n25,0.1\n26,0.2\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_dsc_csv(self._write(Path(temp_dir), text))
        self.assertEqual(curve.time_min, ())

    def test_unknown_heat_flow_header_is_not_guessed(self):
        text = VALID_DSC.replace("HeatFlow_mW", "Signal")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write(Path(temp_dir), text)
            with self.assertRaisesRegex(DscDataError, "対応列名"):
                load_dsc_csv(path)


if __name__ == "__main__":
    unittest.main()
