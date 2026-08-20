"""Sharp governance acceptance for the meta032 -> aa0037ac sync delta."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

import parity_ledger


class UpstreamSyncGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.twin_map = json.loads((ROOT / "Tools/parity_twin_map.json").read_text())
        self.baseline = json.loads((ROOT / "Tools/parity_ledger_baseline.json").read_text())

    def test_hrv_optional_ord_arity_refreshes_only_the_existing_inventory_identity(self) -> None:
        swift6 = "Packages/StrandAnalytics/Sources/StrandAnalytics/HRVAnalyzer.swift::densestSecondWindowSample/6#1"
        kotlin6 = "android/app/src/main/java/com/noop/analytics/HrvAnalyzer.kt::densestSecondWindowSample/6#1"
        encoded = json.dumps(self.twin_map)
        self.assertIn(swift6, self.twin_map["unpaired_functions"]["swift"])
        self.assertIn(kotlin6, self.twin_map["unpaired_functions"]["kotlin"])
        self.assertNotIn("densestSecondWindowSample/5#1", encoded)
        self.assertIn({"swift": swift6, "kotlin": kotlin6}, self.twin_map["name_only_suggestions"]["functions"])

    def test_chunk_clock_inventory_is_exactly_issue_bound_not_promoted(self) -> None:
        swift_file = "Packages/WhoopProtocol/Sources/WhoopProtocol/ChunkClockDiag.swift"
        kotlin_file = "android/app/src/main/java/com/noop/protocol/ChunkClockDiag.kt"
        identities = {
            f"unmapped-file|{swift_file}",
            f"unmapped-file|{kotlin_file}",
            *(f"unmapped-function|{swift_file}::{name}/{arity}#1" for name, arity in (("line", 4), ("signed", 1), ("fixed2", 2))),
            *(f"unmapped-function|{kotlin_file}::{name}/{arity}#1" for name, arity in (("line", 4), ("signed", 1), ("fixed2", 2))),
        }
        entries = {item["identity"]: item for item in self.baseline["findings"] if item["identity"] in identities}
        self.assertEqual(identities, set(entries))
        self.assertTrue(all(item.get("issue") == 98 for item in entries.values()))
        self.assertTrue(all("8124da33" in item.get("provenance", "") for item in entries.values()))
        mapped = json.dumps({"file_pairs": self.twin_map["file_pairs"], "function_pairs": self.twin_map["function_pairs"]})
        self.assertNotIn("ChunkClockDiag", mapped)

    def test_live_sync_tree_has_no_findings_beyond_the_exact_baseline(self) -> None:
        result = parity_ledger.scan(ROOT, self.twin_map)
        known = {item["identity"] for item in self.baseline["findings"]}
        self.assertEqual([], [item.identity for item in result.findings if item.identity not in known])


if __name__ == "__main__":
    unittest.main()
