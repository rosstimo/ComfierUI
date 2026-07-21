from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "node-pack-doctor.py"
spec = importlib.util.spec_from_file_location("node_pack_doctor", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class LogParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "multiple-import-failures.log"
        self.lines = fixture.read_text(encoding="utf-8").splitlines()

    def test_latest_startup_finds_every_current_failure(self) -> None:
        starts = mod.sessions(self.lines)
        self.assertEqual(2, len(starts))
        current = mod.failures(starts[-1])
        self.assertEqual(
            ["alpha-pack", "beta-pack", "gamma-pack"],
            [failure["pack"] for failure in current],
        )

    def test_historical_failure_is_not_current(self) -> None:
        starts = mod.sessions(self.lines)
        historical = mod.failures(starts[0])
        current = mod.failures(starts[-1])
        self.assertEqual(["old-pack"], [failure["pack"] for failure in historical])
        self.assertNotIn("old-pack", [failure["pack"] for failure in current])

    def test_successful_import_count_excludes_failed_summary_lines(self) -> None:
        current = mod.sessions(self.lines)[-1]
        self.assertEqual({"working-pack"}, mod.successful_imports(current))


class ClassificationTests(unittest.TestCase):
    def test_local_missing_module_is_package_defect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "alpha-pack"
            root.mkdir()
            (root / "__init__.py").write_text(
                "from AlphaNodes import NODE_CLASS_MAPPINGS\n", encoding="utf-8"
            )
            (root / "AlphaNodes.py").write_text(
                "NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8"
            )
            scan = mod.scan_pack("alpha-pack", str(root))
            failure = {
                "pack": "alpha-pack",
                "container_path": "/opt/ComfyUI/custom_nodes/alpha-pack",
                "summary": "No module named 'AlphaNodes'",
                "traceback": ["ModuleNotFoundError: No module named 'AlphaNodes'"],
                "exception_type": "ModuleNotFoundError",
                "exception_message": "No module named 'AlphaNodes'",
            }
            diagnosis = mod.diagnose(failure, scan)
            self.assertEqual("PACKAGE DEFECT", diagnosis["category"])
            self.assertEqual("HIGH", diagnosis["confidence"])
            self.assertTrue(
                any(item["code"] == "ABSOLUTE_SIBLING_IMPORT" for item in scan["findings"])
            )

    def test_permission_error_outside_pack_is_filesystem_incompatibility(self) -> None:
        scan = {"pack": "beta-pack", "host_path": None, "exists": False,
                "local_modules": [], "findings": [], "requirements": []}
        failure = {
            "pack": "beta-pack",
            "container_path": "/opt/ComfyUI/custom_nodes/beta-pack",
            "summary": "[Errno 13] Permission denied: '/opt/ComfyUI/web'",
            "traceback": ["PermissionError: [Errno 13] Permission denied: '/opt/ComfyUI/web'"],
            "exception_type": "PermissionError",
            "exception_message": "[Errno 13] Permission denied: '/opt/ComfyUI/web'",
        }
        diagnosis = mod.diagnose(failure, scan)
        self.assertEqual("FILESYSTEM/PERMISSION INCOMPATIBILITY", diagnosis["category"])
        self.assertIn("outside its own package", diagnosis["explanation"])

    def test_unknown_failure_stays_unknown(self) -> None:
        scan = {"pack": "gamma-pack", "host_path": None, "exists": False,
                "local_modules": [], "findings": [], "requirements": []}
        failure = {
            "pack": "gamma-pack",
            "container_path": "/opt/ComfyUI/custom_nodes/gamma-pack",
            "summary": "something unexpected happened",
            "traceback": ["RuntimeError: something unexpected happened"],
            "exception_type": "RuntimeError",
            "exception_message": "something unexpected happened",
        }
        diagnosis = mod.diagnose(failure, scan)
        self.assertEqual("UNKNOWN", diagnosis["category"])
        self.assertEqual("LOW", diagnosis["confidence"])


if __name__ == "__main__":
    unittest.main()
