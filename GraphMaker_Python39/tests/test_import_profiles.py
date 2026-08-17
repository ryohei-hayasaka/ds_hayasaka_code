from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tga_analyzer.filesystem import list_csv_names
from tga_analyzer.import_profiles import (
    AmbiguousProfileError,
    ColumnMapping,
    ImportProfile,
    ImportProfileError,
    ProfileStore,
    ProfiledCurveLoader,
    detect_profile,
    load_curve_with_profile,
    preview_csv,
    test_import as run_test_import,
)
from tga_analyzer.model import DSC, GPC, IR, PARTICLE_SIZE, TGA, UV_VIS


PROJECT = Path(__file__).resolve().parents[1]
BUILT_INS = PROJECT / "profiles"


def tga_profile(**changes) -> ImportProfile:
    values = dict(
        profile_id="test-tga",
        name="Test TGA",
        measurement_type=TGA,
        encoding="auto",
        delimiter="comma",
        header_mode="auto",
        start_mode="header_next",
        columns={
            "x": ColumnMapping(header="Temperature"),
            "y": ColumnMapping(header="Mass"),
        },
        units={"x": "°C", "y": "mg"},
        end_mode="eof",
    )
    values.update(changes)
    return ImportProfile(**values)


class ImportProfileStoreTests(unittest.TestCase):
    def test_json_save_reload_duplicate_delete_and_built_in_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user"
            store = ProfileStore(BUILT_INS, user)
            self.assertGreaterEqual(len(store.all()), 9)
            self.assertFalse(store.errors)
            profile = replace(tga_profile(), profile_id="user-roundtrip", name="Round trip")
            saved = store.save(profile)
            reloaded = ProfileStore(BUILT_INS, user).get(saved.profile_id)
            self.assertEqual(reloaded, saved)

            duplicate = store.duplicate("builtin-tga-fixed")
            self.assertFalse(duplicate.built_in)
            duplicate = store.save(duplicate)
            store.delete(duplicate.profile_id)
            self.assertIsNone(store.get(duplicate.profile_id))
            with self.assertRaisesRegex(ImportProfileError, "組み込み"):
                store.delete("builtin-tga-fixed")

    def test_bad_json_and_built_in_id_collision_disable_only_bad_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp)
            (user / "broken.json").write_text("{oops", encoding="utf-8")
            collision = tga_profile().to_dict()
            collision["profile_id"] = "builtin-tga-fixed"
            (user / "collision.json").write_text(json.dumps(collision), encoding="utf-8")
            valid = replace(tga_profile(), profile_id="valid-user").to_dict()
            (user / "valid.json").write_text(json.dumps(valid), encoding="utf-8")
            store = ProfileStore(BUILT_INS, user)
            self.assertIsNotNone(store.get("builtin-tga-fixed"))
            self.assertIsNotNone(store.get("valid-user"))
            self.assertEqual(len(store.errors), 2)
            self.assertTrue(any("broken.json" in error for error in store.errors))

    def test_profiles_are_filtered_by_measurement_mode(self):
        store = ProfileStore(BUILT_INS, Path("__missing_user_profiles__"))
        self.assertTrue(store.all(TGA))
        self.assertTrue(all(profile.measurement_type == TGA for profile in store.all(TGA)))
        self.assertTrue(all(profile.measurement_type == IR for profile in store.all(IR)))


class ProfiledReadTests(unittest.TestCase):
    def write(self, folder: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = folder / name
        path.write_text(text, encoding=encoding)
        return path

    def test_auto_header_after_ten_preamble_rows_and_reordered_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            preamble = "".join(f"metadata {index}\n" for index in range(10))
            path = self.write(
                folder,
                "device.csv",
                preamble + "Mass,Unused,Temperature\n10,x,20\n9,x,30\n8,x,40\n7,x,50\n",
            )
            result = run_test_import(path, tga_profile())
            self.assertEqual(result.header_row, 11)
            self.assertEqual((result.data_start_row, result.data_end_row), (12, 15))
            self.assertEqual(result.curve.temperatures, (20.0, 30.0, 40.0, 50.0))
            self.assertEqual(result.curve.weight_percent[-1], 70.0)
            self.assertEqual(result.curve.import_provenance.x_column, "C列（3）: Temperature")

    def test_absolute_start_and_headerless_column_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), "headerless.csv", "note\nmore\n1,20,10\n2,30,9\n3,40,8\n")
            profile = tga_profile(
                header_mode="none",
                start_mode="absolute",
                start_row=3,
                columns={"x": ColumnMapping(column=2), "y": ColumnMapping(column=3)},
            )
            curve = load_curve_with_profile(path, profile)
            self.assertEqual(curve.temperatures, (20.0, 30.0, 40.0))
            self.assertEqual(curve.import_provenance.data_start_row, 3)

    def test_keyword_relative_start_and_missing_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), "keyword.csv", "title\n[Data]\nignore\n20,10\n30,9\n40,8\n")
            profile = tga_profile(
                header_mode="none",
                start_mode="keyword_offset",
                start_keyword="[Data]",
                start_offset=2,
                columns={"x": ColumnMapping(column=1), "y": ColumnMapping(column=2)},
            )
            result = run_test_import(path, profile)
            self.assertEqual(result.data_start_row, 4)
            with self.assertRaisesRegex(ImportProfileError, "開始キーワード"):
                run_test_import(path, replace(profile, start_keyword="[Missing]"))

    def test_header_keyword_and_header_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(
                Path(tmp), "offset.csv", "title\nTemperature,Mass\nunit C,unit mg\n20,10\n30,9\n40,8\n"
            )
            profile = tga_profile(
                header_mode="keyword",
                header_keyword="Temperature",
                start_mode="header_offset",
                start_offset=2,
            )
            result = run_test_import(path, profile)
            self.assertEqual((result.header_row, result.data_start_row), (2, 4))

    def test_start_outside_file_and_duplicate_xy_have_specific_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), "short.csv", "1,2\n3,4\n")
            outside = tga_profile(
                header_mode="none", start_mode="absolute", start_row=9,
                columns={"x": ColumnMapping(column=1), "y": ColumnMapping(column=2)},
            )
            with self.assertRaisesRegex(ImportProfileError, "ファイル範囲外"):
                run_test_import(path, outside)
            with self.assertRaisesRegex(ImportProfileError, "同じ列"):
                replace(outside, start_row=1, columns={"x": ColumnMapping(column=1), "y": ColumnMapping(column=1)})

    def test_all_end_conditions_and_invalid_middle_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base = tga_profile(
                header_mode="row", header_row=1, start_mode="header_next",
                columns={"x": ColumnMapping(header="Temperature"), "y": ColumnMapping(header="Mass")},
            )
            eof = self.write(folder, "eof.csv", "Temperature,Mass\n20,10\n30,9\n40,8\n")
            self.assertEqual(run_test_import(eof, base).point_count, 3)

            blank = self.write(folder, "blank.csv", "Temperature,Mass\n20,10\n30,9\n,\nFooter,Footer\n")
            self.assertEqual(run_test_import(blank, replace(base, end_mode="mapped_blank")).data_end_row, 3)

            absolute = self.write(folder, "absolute.csv", "Temperature,Mass\n20,10\n30,9\n40,8\n50,7\n")
            self.assertEqual(run_test_import(absolute, replace(base, end_mode="absolute", end_row=3)).point_count, 2)

            keyword = self.write(folder, "endkeyword.csv", "Temperature,Mass\n20,10\n30,9\n[End]\nfooter\n")
            self.assertEqual(run_test_import(keyword, replace(base, end_mode="before_keyword", end_keyword="[End]")).data_end_row, 3)

            nonnumeric = self.write(folder, "nonnumeric.csv", "Temperature,Mass\n20,10\n30,9\nfooter,x\n")
            self.assertEqual(run_test_import(nonnumeric, replace(base, end_mode="first_non_numeric")).point_count, 2)

            run = self.write(folder, "run.csv", "Temperature,Mass\n20,10\n30,9\nbad,x\nbad,x\nfooter,x\n")
            self.assertEqual(run_test_import(run, replace(base, end_mode="non_numeric_run", non_numeric_count=2)).point_count, 2)

            middle = self.write(folder, "middle.csv", "Temperature,Mass\n20,10\nbad,x\n30,9\n40,8\n")
            with self.assertRaisesRegex(ImportProfileError, "数値データが再開"):
                run_test_import(middle, replace(base, end_mode="non_numeric_run", non_numeric_count=2))
            with self.assertRaisesRegex(ImportProfileError, "3行目"):
                run_test_import(middle, base)

    def test_utf8_bom_cp932_and_semicolon_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cp932 = self.write(
                folder, "cp932.csv", "装置情報\nTemperature;Mass\n20;10\n30;9\n40;8\n", "cp932"
            )
            profile = replace(tga_profile(), delimiter="semicolon")
            result = run_test_import(cp932, profile)
            self.assertEqual(result.encoding, "cp932")
            self.assertEqual(result.delimiter, ";")
            bom = folder / "bom.csv"
            bom.write_text("Temperature\tMass\n20\t10\n30\t9\n40\t8\n", encoding="utf-8-sig")
            result = run_test_import(bom, replace(profile, delimiter="tab"))
            self.assertEqual(result.encoding, "utf-8-sig")
            self.assertEqual(result.delimiter, "\t")

    def test_numeric_error_contains_real_line_column_and_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), "bad.csv", "meta\nTemperature,Mass\n20,10\n30,oops\n40,8\n")
            with self.assertRaises(ImportProfileError) as caught:
                run_test_import(path, replace(tga_profile(), header_mode="row", header_row=2))
            message = str(caught.exception)
            self.assertIn("4行目", message)
            self.assertIn("B列／2列目", message)
            self.assertIn("'oops'", message)

    def test_preview_reads_at_most_300_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), "long.csv", "\n".join(f"{i},{i}" for i in range(500)) + "\n")
            preview = preview_csv(path)
            self.assertEqual(len(preview.rows), 300)
            self.assertTrue(preview.truncated)

    def test_file_name_listing_still_does_not_open_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.write(folder, "sample.csv", "Temperature,Mass\n20,10\n")
            with mock.patch("pathlib.Path.open", side_effect=AssertionError("contents opened")):
                self.assertEqual(list_csv_names(folder), ["sample.csv"])


class ProfileDetectionAndCacheTests(unittest.TestCase):
    def test_unique_and_ambiguous_profile_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device_a.csv"
            path.write_text("Temperature,Mass\n20,10\n30,9\n40,8\n", encoding="utf-8")
            generic = tga_profile()
            specific = replace(generic, profile_id="specific", name="Specific", file_patterns=("device_a*.csv",))
            self.assertEqual(detect_profile(path, TGA, (generic, specific,)).profile_id, "specific")
            same = replace(generic, profile_id="same", name="Same")
            with self.assertRaises(AmbiguousProfileError):
                detect_profile(path, TGA, (generic, same))

    def test_auto_header_requires_three_numeric_rows_and_unknown_unit_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            short = folder / "short.csv"
            short.write_text("Temperature,Mass\n20,10\n30,9\n", encoding="utf-8")
            with self.assertRaisesRegex(ImportProfileError, "適用できる"):
                detect_profile(short, TGA, (tga_profile(),))
            valid = folder / "valid.csv"
            valid.write_text("Temperature,Mass\n20,10\n30,9\n40,8\n", encoding="utf-8")
            with self.assertRaisesRegex(ImportProfileError, "Y単位"):
                load_curve_with_profile(valid, replace(tga_profile(), units={"x": "°C", "y": "mystery"}))

    def test_cache_reuses_same_profile_and_invalidates_changed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data.csv"
            path.write_text("Temperature,Mass\n20,10\n30,9\n40,8\n50,7\n", encoding="utf-8")
            store = ProfileStore(BUILT_INS, root / "user")
            loader = ProfiledCurveLoader(store)
            profile = tga_profile()
            first = loader.load(path, TGA, profile)
            second = loader.load(path, TGA, profile)
            self.assertEqual(loader.cache_hits, 1)
            self.assertEqual(first.temperatures, second.temperatures)
            changed = replace(profile, start_mode="absolute", start_row=3, header_mode="row", header_row=1)
            third = loader.load(path, TGA, changed)
            self.assertEqual(third.temperatures, (30.0, 40.0, 50.0))
            self.assertEqual(loader.cache_hits, 1)


class AllModeProfileTests(unittest.TestCase):
    def test_built_in_profiles_auto_load_existing_demo_files(self):
        store = ProfileStore(BUILT_INS, Path("__missing_user_profiles__"))
        loader = ProfiledCurveLoader(store)
        cases = (
            (TGA, PROJECT / "demo_data" / "TGA" / "raw_data" / "TGA_demo_raw_data.csv"),
            (DSC, PROJECT / "demo_data" / "DSC" / "raw_data" / "DSC_demo_01.csv"),
            (IR, PROJECT / "demo_data" / "IR" / "raw_data" / "IR_demo_01.csv"),
            (UV_VIS, PROJECT / "demo_data" / "UV-Vis" / "raw_data" / "UVVis_demo_01.csv"),
            (GPC, PROJECT / "demo_data" / "GPC" / "raw_data" / "GPC_RI_demo_01.csv"),
            (PARTICLE_SIZE, PROJECT / "demo_data" / "ParticleSize" / "raw_data" / "ParticleSize_demo_01.csv"),
        )
        for mode, path in cases:
            with self.subTest(mode=mode):
                curve = loader.load(path, mode)
                self.assertEqual(curve.measurement_type, mode)
                self.assertGreater(curve.point_count, 2)
                self.assertTrue(curve.import_provenance.profile_id.startswith("builtin-"))

    def test_each_mode_reads_preamble_with_explicit_mapping(self):
        cases = (
            (TGA, "Temp,Mass\n20,10\n30,9\n40,8\n", "°C", "mg", "weight_percent"),
            (DSC, "Temp,Flow\n20,1\n30,2\n40,3\n", "°C", "W/g", "heat_flow_mw"),
            (IR, "Wave,Abs\n4000,0.1\n3990,0.2\n3980,0.3\n", "cm-1", "Absorbance", "absorbance"),
            (UV_VIS, "Wave,Abs\n200,0.1\n201,0.2\n202,0.3\n", "nm", "Absorbance", "uvvis_absorbance"),
            (GPC, "Time,RI\n0,0.1\n1,0.2\n2,0.3\n", "min", "mV", "ri_signal_mv"),
            (PARTICLE_SIZE, "Size,Volume\n1,10\n2,20\n3,30\n", "um", "%", "volume_frequency_percent"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index, (mode, body, x_unit, y_unit, attribute) in enumerate(cases):
                with self.subTest(mode=mode):
                    path = folder / f"mode_{index}.csv"
                    path.write_text("metadata\nmore metadata\n" + body, encoding="utf-8")
                    header = body.splitlines()[0].split(",")
                    profile = ImportProfile(
                        profile_id=f"mode-{index}", name=mode, measurement_type=mode,
                        delimiter="comma", header_mode="auto", start_mode="header_next",
                        columns={"x": ColumnMapping(header=header[0]), "y": ColumnMapping(header=header[1])},
                        units={"x": x_unit, "y": y_unit},
                    )
                    result = run_test_import(path, profile)
                    self.assertEqual(result.header_row, 3)
                    self.assertEqual(len(getattr(result.curve, attribute)), 3)

    def test_dsc_optional_metadata_columns_and_unknown_heat_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dsc.csv"
            path.write_text(
                "Temp,Flow,Mass,Rate\n20,1,0.012,10\n30,2,0.012,10\n40,3,0.012,10\n",
                encoding="utf-8",
            )
            profile = ImportProfile(
                profile_id="dsc-meta", name="DSC meta", measurement_type=DSC,
                delimiter="comma", header_mode="auto", start_mode="header_next",
                columns={
                    "x": ColumnMapping(header="Temp"), "y": ColumnMapping(header="Flow"),
                    "sample_mass": ColumnMapping(header="Mass"),
                    "heating_rate": ColumnMapping(header="Rate"),
                },
                units={"x": "°C", "y": "unknown", "sample_mass": "g", "heating_rate": "°C/min"},
            )
            result = run_test_import(path, profile)
            self.assertIsNone(result.curve.heat_flow_unit)
            self.assertAlmostEqual(result.curve.sample_mass_mg, 12.0)
            self.assertEqual(result.curve.heating_rate_c_min, 10.0)
            self.assertIn("エンタルピー", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
