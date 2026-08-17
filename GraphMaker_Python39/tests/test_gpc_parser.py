import tempfile
import unittest
from pathlib import Path

from tga_analyzer.model import GPC
from tga_analyzer.parser import GpcDataError, load_gpc_csv


VALID = """Record_ID,RetentionTime_min,RI_mV
1,0.00,0.688000
2,0.02,0.692114
3,0.04,0.695061
"""


class GpcParserTests(unittest.TestCase):
    def _write(self, folder: Path, text: str, encoding: str = "utf-8-sig") -> Path:
        path = folder / "gpc.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_reads_fixed_ri_columns_without_changing_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_gpc_csv(self._write(Path(temp_dir), VALID))
        self.assertEqual(curve.measurement_type, GPC)
        self.assertEqual(curve.retention_times_min, (0.0, 0.02, 0.04))
        self.assertEqual(curve.ri_signal_mv, (0.688, 0.692114, 0.695061))
        self.assertEqual(curve.plot_x, curve.retention_times_min)
        self.assertEqual(curve.plot_y, curve.ri_signal_mv)

    def test_reads_utf8_bom_utf8_and_cp932(self):
        for encoding in ("utf-8-sig", "utf-8", "cp932"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp_dir:
                self.assertEqual(load_gpc_csv(self._write(Path(temp_dir), VALID, encoding)).point_count, 3)

    def test_demo_has_1501_points_and_zero_to_30_minutes(self):
        curve = load_gpc_csv(Path("demo_data/GPC/raw_data/GPC_RI_demo_10.csv"))
        self.assertEqual(curve.point_count, 1501)
        self.assertEqual(
            (curve.retention_times_min[0], curve.retention_times_min[-1]),
            (0.0, 30.0),
        )

    def test_only_exact_ri_header_is_accepted_and_bad_values_report_line(self):
        cases = (
            (VALID.replace("RI_mV", "RIU"), "必須列"),
            (VALID.replace("2,0.02", "bad,0.02"), "3行目"),
            (VALID.replace("0.692114", "inf"), "有限値"),
        )
        for text, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(GpcDataError, message):
                    load_gpc_csv(self._write(Path(temp_dir), text))

    def test_additional_columns_are_ignored_and_one_point_is_rejected(self):
        extra = VALID.replace("RI_mV", "RI_mV,Extra").replace("0.688000", "0.688000,x").replace("0.692114", "0.692114,y").replace("0.695061", "0.695061,z")
        with tempfile.TemporaryDirectory() as temp_dir:
            curve = load_gpc_csv(self._write(Path(temp_dir), extra))
        self.assertEqual(curve.ri_signal_mv, (0.688, 0.692114, 0.695061))
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(GpcDataError, "不足"):
                load_gpc_csv(self._write(Path(temp_dir), VALID.splitlines()[0] + "\n" + VALID.splitlines()[1]))


if __name__ == "__main__":
    unittest.main()
