import tempfile
import unittest
from pathlib import Path

from tga_analyzer.model import PARTICLE_SIZE
from tga_analyzer.parser import ParticleSizeDataError, load_particle_size_csv


ROOT = Path(__file__).resolve().parents[1]
DEMO_FOLDER = ROOT / "demo_data" / "ParticleSize" / "raw_data"


class ParticleSizeParserTests(unittest.TestCase):
    def test_loads_all_ten_demo_files_without_renormalizing(self):
        paths = sorted(DEMO_FOLDER.glob("ParticleSize_demo_*.csv"))
        self.assertEqual(len(paths), 10)
        for path in paths:
            curve = load_particle_size_csv(path)
            self.assertEqual(curve.measurement_type, PARTICLE_SIZE)
            self.assertEqual(curve.point_count, 161)
            self.assertAlmostEqual(curve.particle_diameter_um[0], 0.1, places=9)
            self.assertAlmostEqual(curve.particle_diameter_um[-1], 1000.0, places=6)
            self.assertAlmostEqual(sum(curve.volume_frequency_percent), 100.0, places=5)

    def test_accepts_explicit_alias_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "alias.csv"
            path.write_text(
                "Record_ID,ParticleSize_um,Volume_percent\n1,1,40\n2,2,60\n",
                encoding="utf-8",
            )
            curve = load_particle_size_csv(path)
        self.assertEqual(curve.particle_diameter_um, (1.0, 2.0))
        self.assertEqual(curve.volume_frequency_percent, (40.0, 60.0))
        self.assertEqual(curve.source_particle_diameter_header, "ParticleSize_um")
        self.assertEqual(curve.source_volume_frequency_header, "Volume_percent")

    def test_reads_utf8_bom_utf8_and_cp932(self):
        text = (
            "Record_ID,ParticleDiameter_um,VolumeFrequency_percent\n"
            "1,0.1,25\n2,1,75\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            for index, encoding in enumerate(("utf-8-sig", "utf-8", "cp932")):
                path = folder / f"sample_{index}.csv"
                path.write_text(text, encoding=encoding)
                self.assertEqual(load_particle_size_csv(path).point_count, 2)

    def test_rejects_nonpositive_duplicate_and_out_of_order_diameters(self):
        cases = (
            ("1,0,1\n2,1,2\n", "0より大きい"),
            ("1,1,1\n2,1,2\n", "重複"),
            ("1,2,1\n2,1,2\n", "厳密な昇順"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            for index, (rows, message) in enumerate(cases):
                path = folder / f"bad_{index}.csv"
                path.write_text(
                    "Record_ID,ParticleDiameter_um,VolumeFrequency_percent\n" + rows,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ParticleSizeDataError, message):
                    load_particle_size_csv(path)

    def test_reports_line_for_bad_number_and_missing_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            bad_number = folder / "bad_number.csv"
            bad_number.write_text(
                "Record_ID,ParticleDiameter_um,VolumeFrequency_percent\n"
                "1,1,20\n2,bad,80\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ParticleSizeDataError, "3行目"):
                load_particle_size_csv(bad_number)

            missing = folder / "missing.csv"
            missing.write_text("Record_ID,Diameter,Volume\n1,1,100\n", encoding="utf-8")
            with self.assertRaisesRegex(ParticleSizeDataError, "必須列"):
                load_particle_size_csv(missing)


if __name__ == "__main__":
    unittest.main()
