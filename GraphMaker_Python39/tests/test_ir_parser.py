import tempfile
import unittest
from pathlib import Path

from tga_analyzer.model import IR
from tga_analyzer.parser import IrDataError, load_ir_csv


class IrParserTests(unittest.TestCase):
    def _write(self, folder: Path, text: str, encoding: str = "utf-8-sig") -> Path:
        path = folder / "ir.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_reads_fixed_demo_format_and_preserves_descending_wavenumbers(self):
        curve = load_ir_csv(Path("demo_data/IR/raw_data/IR_demo_01.csv"))
        self.assertEqual(curve.measurement_type, IR)
        self.assertEqual(curve.point_count, 1801)
        self.assertEqual(curve.wavenumbers_cm1[0], 4000.0)
        self.assertEqual(curve.wavenumbers_cm1[-1], 400.0)
        self.assertEqual(curve.plot_y, curve.absorbance)

    def test_reads_utf8_bom_and_cp932(self):
        text = "Record_ID,Wavenumber_cm-1,Absorbance\n1,4000,0.1\n2,3998,0.2\n"
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for encoding in ("utf-8-sig", "cp932"):
                curve = load_ir_csv(self._write(folder, text, encoding))
                self.assertEqual(curve.absorbance, (0.1, 0.2))

    def test_missing_or_unknown_headers_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write(
                Path(temporary),
                "Record_ID,Wavenumber,Signal\n1,4000,0.1\n2,3998,0.2\n",
            )
            with self.assertRaisesRegex(IrDataError, "Wavenumber_cm-1"):
                load_ir_csv(path)

    def test_invalid_number_reports_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write(
                Path(temporary),
                "Record_ID,Wavenumber_cm-1,Absorbance\n1,4000,0.1\n2,3998,bad\n",
            )
            with self.assertRaisesRegex(IrDataError, "3行目"):
                load_ir_csv(path)


if __name__ == "__main__":
    unittest.main()
