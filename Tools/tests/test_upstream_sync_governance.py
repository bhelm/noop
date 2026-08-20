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
        self.swift_shard = json.loads(
            (ROOT / "Packages/StrandAnalytics/Sources/StrandAnalytics/parity-exempt.json").read_text()
        )
        self.kotlin_shard = json.loads(
            (ROOT / "android/app/src/main/java/com/noop/analytics/parity-exempt.json").read_text()
        )

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

    def test_59c95712_twin_inventory_is_the_independently_audited_delta(self) -> None:
        swift_analytics = "Packages/StrandAnalytics/Sources/StrandAnalytics/AnalyticsEngine.swift"
        kotlin_analytics = "android/app/src/main/java/com/noop/analytics/AnalyticsEngine.kt"
        pairs = {(item["swift"], item["kotlin"]) for item in self.twin_map["function_pairs"]}
        self.assertIn((f"{swift_analytics}::isWorn/1#1", f"{kotlin_analytics}::isWorn/1#1"), pairs)
        self.assertIn((
            "Packages/StrandAnalytics/Sources/StrandAnalytics/StepsEstimateEngine.swift::isUsableCalibrationPoint/1#1",
            "android/app/src/main/java/com/noop/analytics/StepsEstimateEngine.kt::isUsableCalibrationPoint/1#1",
        ), pairs)

        swift_unpaired = set(self.twin_map["unpaired_functions"]["swift"])
        kotlin_unpaired = set(self.twin_map["unpaired_functions"]["kotlin"])
        self.assertTrue({
            f"{swift_analytics}::analyzeDay/32#1",
            f"{swift_analytics}::wornNightlySkinTempC/7#1",
            f"{swift_analytics}::skinTempFunnel/7#1",
            "Packages/WhoopProtocol/Sources/WhoopProtocol/HistoricalStreams.swift::extractHistoricalStreams/7#1",
        }.issubset(swift_unpaired))
        self.assertTrue({
            f"{kotlin_analytics}::analyzeDay/32#1",
            f"{kotlin_analytics}::wornNightlySkinTempC/7#1",
            f"{kotlin_analytics}::skinTempFunnel/7#1",
            "android/app/src/main/java/com/noop/analytics/IntelligenceEngine.kt::skinTempWornToleranceSec/1#1",
            "android/app/src/main/java/com/noop/analytics/RegistryDayOwnerSource.kt::skinTempWornToleranceSec/1#1",
            "android/app/src/main/java/com/noop/data/BackupProvenance.kt::jsonString/1#1",
            "android/app/src/main/java/com/noop/data/WhoopRepository.kt::advanceReadoutDataRevisions/2#1",
            "android/app/src/main/java/com/noop/data/WhoopRepository.kt::publishReadoutRevisions/1#1",
            "android/app/src/main/java/com/noop/ingest/RouteExport.kt::roundNearestTiesAwayFromZero/1#1",
            "android/app/src/main/java/com/noop/oura/Decoders.kt::strictUtf8/1#1",
            "android/app/src/main/java/com/noop/protocol/Streams.kt::longOrNull/1#1",
        }.issubset(kotlin_unpaired))
        encoded = json.dumps(self.twin_map)
        for stale in ("analyzeDay/31#1", "wornNightlySkinTempC/6#1", "skinTempFunnel/6#1",
                      "HistoricalStreams.swift::extractHistoricalStreams/6#1"):
            self.assertNotIn(stale, encoded)

        constant_pairs = {(item["swift"], item["kotlin"]) for item in self.twin_map["constant_pairs"]}
        self.assertIn((
            f"{swift_analytics}::defaultOuraWornToleranceSec",
            f"{kotlin_analytics}::DEFAULT_OURA_WORN_TOLERANCE_SEC",
        ), constant_pairs)

    def test_59c95712_ratchet_entries_pin_issue_and_upstream_provenance(self) -> None:
        expected = {
            "swift": {
                "exempt": {
                    "Packages/StrandAnalytics/Sources/StrandAnalytics/AnalyticsEngine.swift::analyzeDay/32[defaults=2,3,4,5,6,7,8,9,10,11,12,13,14,15,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32]#1": "c5ea975a",
                    "Packages/StrandAnalytics/Sources/StrandAnalytics/AnalyticsEngine.swift::wornNightlySkinTempC/7[defaults=4,5,6,7]#1": "cec9c80d",
                    "Packages/StrandAnalytics/Sources/StrandAnalytics/AnalyticsEngine.swift::skinTempFunnel/7[defaults=4,5,6,7]#1": "cec9c80d",
                    "Packages/StrandAnalytics/Sources/StrandAnalytics/AnalyticsEngine.swift::defaultOuraWornToleranceSec@property-initializer#1": "cec9c80d",
                },
                "platform-test": {
                    "Packages/StrandAnalytics/Sources/StrandAnalytics/StepsEstimateEngine.swift::isUsableCalibrationPoint/1[defaults=-]#1": "3e3bff81",
                },
            },
            "kotlin": {
                "exempt": {
                    "android/app/src/main/java/com/noop/analytics/AnalyticsEngine.kt::analyzeDay/32[defaults=2,3,4,5,6,7,8,9,10,11,12,13,14,15,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32]#1": "c5ea975a",
                    "android/app/src/main/java/com/noop/analytics/AnalyticsEngine.kt::wornNightlySkinTempC/7[defaults=4,5,6,7]#1": "cec9c80d",
                    "android/app/src/main/java/com/noop/analytics/AnalyticsEngine.kt::skinTempFunnel/7[defaults=4,5,6,7]#1": "cec9c80d",
                    "android/app/src/main/java/com/noop/analytics/AnalyticsEngine.kt::DEFAULT_OURA_WORN_TOLERANCE_SEC@property-initializer#1": "cec9c80d",
                    "android/app/src/main/java/com/noop/analytics/IntelligenceEngine.kt::skinTempWornToleranceSec/1[defaults=-]#1": "cec9c80d",
                    "android/app/src/main/java/com/noop/analytics/RegistryDayOwnerSource.kt::skinTempWornToleranceSec/1[defaults=-]#1": "cec9c80d",
                },
                "platform-test": {
                    "android/app/src/main/java/com/noop/analytics/StepsEstimateEngine.kt::isUsableCalibrationPoint/1[defaults=-]#1": "3e3bff81",
                },
            },
        }
        for platform, shard in (("swift", self.swift_shard), ("kotlin", self.kotlin_shard)):
            for category, entries in expected[platform].items():
                actual = {item["key"]: item for item in shard[category]}
                for key, oid in entries.items():
                    self.assertEqual(17, actual[key]["issue"])
                    evidence = actual[key].get("reason", actual[key].get("test", ""))
                    self.assertIn(oid, evidence)

    def test_1468_removes_exactly_the_thirteen_improved_baseline_identities(self) -> None:
        removed = {
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/AutoWorkoutDetectorTrace.kt::lastSessionSummary/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/ConnectionReadout.kt::lastOffloadResult/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/ConnectionReadout.kt::reconnectCount/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/ConnectionReadout.kt::uptimeLabel/2#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/DisplayTrace.kt::deviceMetricsNow/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/ImportTrace.kt::lastImportSummary/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/SleepReadout.kt::gravityCoverageFraction/2#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/SleepReadout.kt::hrDensityPerMinute/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/SleepReadout.kt::lastChargeBreakdown/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/SleepReadout.kt::lastGateFired/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/SleepReadout.kt::lastHrvComputation/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/StepsEstimateEngineTrace.kt::calibrationState/1#1",
            "test-only-callsite|android/app/src/main/java/com/noop/analytics/StepsEstimateEngineTrace.kt::stepsToday/1#1",
        }
        identities = {item["identity"] for item in self.baseline["findings"]}
        self.assertEqual(13, len(removed))
        self.assertEqual(272, len(self.baseline["findings"]),
                         "only the thirteen reviewed #1468 improvements may be removed")
        self.assertTrue(identities.isdisjoint(removed))

    def test_1466_kotlin_test_comment_resolves_the_exact_swift_test_spelling(self) -> None:
        source = (ROOT / "android/app/src/test/java/com/noop/ble/TimeoutSyncErrorTest.kt").read_text()
        self.assertIn("Swift `testBankedIffSomeCounterMoved`", source)
        self.assertNotIn("Swift `bankedIffSomeCounterMoved`", source)


if __name__ == "__main__":
    unittest.main()
