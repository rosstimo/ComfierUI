from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "node-pack-doctor.py"
spec = importlib.util.spec_from_file_location("node_pack_doctor_more_refinements", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class AdditionalCorrelationTests(unittest.TestCase):
    def test_current_failed_pack_correlation_is_high_confidence(self) -> None:
        events = [
            {
                "kind": "package_extracted",
                "pack": "example-pack",
                "detail": "/opt/ComfyUI/custom_nodes/example-pack",
                "line": 10,
                "observed_startup": 1,
                "observed_started": "2026-07-20 08:00:00",
                "applies_to_startup": 2,
                "applies_to_started": "2026-07-20 08:01:00",
                "current": True,
                "pending_restart": False,
            }
        ]
        failures = [
            {
                "pack": "example-pack",
                "container_path": "/opt/ComfyUI/custom_nodes/example-pack",
            }
        ]

        correlated = mod.correlate_events(events, failures, 2)
        self.assertEqual("current failed pack", correlated[0]["correlation"])
        self.assertEqual("HIGH", correlated[0]["confidence"])

    def test_finding_summary_counts_pack_wide_capabilities(self) -> None:
        summary = mod.finding_summary(
            [
                {"code": "NETWORK_ACCESS"},
                {"code": "NETWORK_ACCESS"},
                {"code": "FILESYSTEM_CHANGE"},
            ]
        )
        self.assertEqual("FILESYSTEM_CHANGE=1, NETWORK_ACCESS=2", summary)


if __name__ == "__main__":
    unittest.main()
