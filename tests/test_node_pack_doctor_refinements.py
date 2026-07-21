from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "node-pack-doctor.py"
spec = importlib.util.spec_from_file_location("node_pack_doctor_refinements", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class StaticScanRefinementTests(unittest.TestCase):
    def test_syntax_warnings_are_captured_not_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "warning-pack"
            root.mkdir()
            (root / "__init__.py").write_text("value = '\\('", encoding="utf-8")

            with warnings.catch_warnings(record=True) as escaped:
                warnings.simplefilter("always")
                scan = mod.scan_pack("warning-pack", str(root))

            self.assertEqual([], escaped)
            self.assertTrue(
                any(
                    finding["code"] == "PYTHON_SYNTAX_WARNING"
                    for finding in scan["findings"]
                )
            )

    def test_findings_are_split_by_failure_relevance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "alpha-pack"
            root.mkdir()
            (root / "__init__.py").write_text(
                "from AlphaNodes import NODE_CLASS_MAPPINGS\n", encoding="utf-8"
            )
            (root / "AlphaNodes.py").write_text(
                "NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8"
            )
            (root / "optional_client.py").write_text(
                "import requests\nrequests.get('http://127.0.0.1')\n",
                encoding="utf-8",
            )

            scan = mod.scan_pack("alpha-pack", str(root))
            failure = {
                "pack": "alpha-pack",
                "container_path": "/opt/ComfyUI/custom_nodes/alpha-pack",
                "summary": "No module named 'AlphaNodes'",
                "traceback": [
                    '  File "/opt/ComfyUI/custom_nodes/alpha-pack/__init__.py", line 1, in <module>',
                    "ModuleNotFoundError: No module named 'AlphaNodes'",
                ],
                "exception_type": "ModuleNotFoundError",
                "exception_message": "No module named 'AlphaNodes'",
            }

            relevant, pack_wide = mod.split_findings(failure, scan)
            self.assertTrue(
                any(item["code"] == "ABSOLUTE_SIBLING_IMPORT" for item in relevant)
            )
            self.assertTrue(
                any(item["code"] == "NETWORK_ACCESS" for item in pack_wide)
            )


class ManagerCorrelationTests(unittest.TestCase):
    def test_extraction_is_correlated_to_the_next_startup(self) -> None:
        lines = [
            "** ComfyUI startup time: 2026-07-20 08:00:00",
            "[INFO] Extracted zip file to /opt/ComfyUI/custom_nodes/example-pack",
            "Restarting... [Legacy Mode]",
            "** ComfyUI startup time: 2026-07-20 08:01:00",
            "[WARNING] Cannot import /opt/ComfyUI/custom_nodes/example-pack module for custom nodes: broken",
        ]
        sessions = mod.startup_sessions(lines)
        events = mod.manager_events(lines, sessions)

        self.assertEqual(1, len(events))
        self.assertEqual(2, events[0]["applies_to_startup"])
        self.assertTrue(events[0]["current"])


if __name__ == "__main__":
    unittest.main()
