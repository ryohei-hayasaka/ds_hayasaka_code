import ast
import importlib
import pkgutil
import unittest
from pathlib import Path

import tga_analyzer
from tga_analyzer.compat import strict_zip


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "tga_analyzer"


def iter_annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                yield node.returns
            arguments = list(node.args.posonlyargs)
            arguments += list(node.args.args)
            arguments += list(node.args.kwonlyargs)
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                if argument.annotation is not None:
                    yield argument.annotation


class Python39CompatibilityTests(unittest.TestCase):
    def test_all_python_files_use_python39_grammar(self):
        paths = list((PROJECT_ROOT / "src").rglob("*.py"))
        paths += list((PROJECT_ROOT / "tests").rglob("*.py"))
        paths += list((PROJECT_ROOT / "scripts").rglob("*.py"))
        for path in sorted(paths):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path), feature_version=(3, 9))
        launcher = (PROJECT_ROOT / "GraphMaker_Python39.pyw").read_text(encoding="utf-8")
        ast.parse(launcher, filename="GraphMaker_Python39.pyw", feature_version=(3, 9))

    def test_dataclass_slots_are_absent(self):
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            with self.subTest(path=path.name):
                self.assertNotIn("slots=True", path.read_text(encoding="utf-8"))

    def test_builtin_zip_strict_keyword_is_absent(self):
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "zip":
                        self.assertFalse(
                            any(keyword.arg == "strict" for keyword in node.keywords),
                            f"strict zip remains in {path.name}:{node.lineno}",
                        )

    def test_pep604_union_is_absent_from_annotations(self):
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for annotation in iter_annotations(tree):
                self.assertFalse(
                    any(
                        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                        for node in ast.walk(annotation)
                    ),
                    f"PEP 604 union remains in {path.name}:{annotation.lineno}",
                )

    def test_strict_zip_checks_lengths_before_processing(self):
        self.assertEqual(list(strict_zip((1, 2), (3, 4), context="test")), [(1, 3), (2, 4)])
        with self.assertRaisesRegex(ValueError, r"test: sequence length mismatch \(2, 1\)"):
            list(strict_zip((1, 2), (3,), context="test"))

    def test_all_package_modules_import(self):
        imported = []
        for module in pkgutil.iter_modules(tga_analyzer.__path__):
            imported.append(importlib.import_module(f"tga_analyzer.{module.name}").__name__)
        self.assertIn("tga_analyzer.gui", imported)
        self.assertIn("tga_analyzer.excel_export", imported)

    def test_python39_launcher_has_required_version_guard(self):
        launcher = (PROJECT_ROOT / "GraphMaker_Python39.pyw").read_text(encoding="utf-8")
        compile(launcher, "GraphMaker_Python39.pyw", "exec")
        self.assertIn("sys.version_info < (3, 9)", launcher)
        self.assertNotIn("sys.version_info < (3, 12)", launcher)
        module_launcher = (SOURCE_ROOT / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("sys.version_info < (3, 9)", module_launcher)
        self.assertNotIn("sys.version_info < (3, 12)", module_launcher)

    def test_project_metadata_requires_python39(self):
        metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.9"', metadata)


if __name__ == "__main__":
    unittest.main()
