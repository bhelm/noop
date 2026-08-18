"""Acceptance tests for the cross-language parity ledger."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import parity_ledger  # noqa: E402


class ParityLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        self.kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        self.swift.parent.mkdir(parents=True)
        self.kotlin.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_clean_tree(self) -> None:
        self.swift.write_text(
            """public enum Engine {
    /// Kotlin twin: `Engine.score`.
    public static func score(_ value: Int) -> Int { value }
    public static let sampleLimit = 3
}
"""
        )
        self.kotlin.write_text(
            """object Engine {
    /** Swift twin: `Engine.score`. */
    fun score(value: Int): Int = value
    const val SAMPLE_LIMIT = 3
}
"""
        )

    def findings(self, twin_map: dict | None = None) -> list[parity_ledger.Finding]:
        if twin_map is None:
            twin_map = parity_ledger.build_twin_map(self.root)
        return parity_ledger.scan(self.root, twin_map).findings

    def run_cli(
        self,
        twin_map: dict,
        baseline: dict | None = None,
        *,
        no_baseline: bool = False,
    ) -> tuple[int, str]:
        map_path = self.root / "map.json"
        baseline_path = self.root / "baseline.json"
        map_path.write_text(json.dumps(twin_map))
        args = ["--root", str(self.root), "--map", str(map_path), "--baseline", str(baseline_path)]
        if baseline is not None:
            baseline_path.write_text(json.dumps(baseline))
        if no_baseline:
            args.append("--no-baseline")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = parity_ledger.main(args)
        return code, output.getvalue()

    def exit_code(self, twin_map: dict) -> int:
        return self.run_cli(twin_map, no_baseline=True)[0]

    def baseline_for(self, twin_map: dict) -> dict:
        return parity_ledger._baseline(parity_ledger.scan(self.root, twin_map))

    def test_ci_paths_cover_every_inventory_glob_for_pull_request_and_push(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/tools-python.yml").read_text()
        pull_request, push = workflow.split("  push:", 1)
        for pattern in parity_ledger.SWIFT_GLOBS + parity_ledger.KOTLIN_GLOBS:
            quoted = f"'{pattern}'"
            self.assertIn(quoted, pull_request)
            self.assertIn(quoted, push)

    def test_clean_synthetic_tree_has_no_findings(self) -> None:
        self.write_clean_tree()
        twin_map = parity_ledger.build_twin_map(self.root)
        self.assertEqual([], self.findings(twin_map))
        self.assertEqual(0, self.exit_code(twin_map))

    def test_protocol_and_oura_source_pairs_are_in_inventory_scope(self) -> None:
        self.assertIn("Packages/WhoopProtocol/Sources/**/*.swift", parity_ledger.SWIFT_GLOBS)
        self.assertIn("Packages/OuraProtocol/Sources/**/*.swift", parity_ledger.SWIFT_GLOBS)
        self.assertIn("android/app/src/main/java/com/noop/protocol/**/*.kt", parity_ledger.KOTLIN_GLOBS)
        self.assertIn("android/app/src/main/java/com/noop/oura/**/*.kt", parity_ledger.KOTLIN_GLOBS)
        self.write_clean_tree()
        scope = parity_ledger.build_twin_map(self.root)["scope"]
        self.assertIn("excluded_inventory_globs", scope)
        self.assertIn("Strand/**/*.swift", scope["excluded_inventory_globs"]["swift"])

    def test_repo_wide_line_comment_reference_is_checked_outside_inventory(self) -> None:
        self.write_clean_tree()
        strand = self.root / "Strand/Outside.swift"
        strand.parent.mkdir(parents=True)
        strand.write_text("// Kotlin twin: MissingOwner.missing\nfunc outside() {}\n")
        rules = {item.rule for item in self.findings() if item.path == "Strand/Outside.swift"}
        self.assertIn("dead-twin-reference", rules)

    def test_new_one_sided_function_is_rejected(self) -> None:
        self.write_clean_tree()
        twin_map = parity_ledger.build_twin_map(self.root)
        self.swift.write_text(self.swift.read_text() + "\npublic func newlyAddedOnlyOnSwift(_ value: Int) -> Int { value }\n")
        rules = {finding.rule for finding in self.findings(twin_map)}
        self.assertIn("unmapped-function", rules)
        self.assertEqual(1, self.exit_code(twin_map))

    def test_trailing_comma_does_not_add_a_parameter(self) -> None:
        self.kotlin.write_text("fun score(first: Int, second: Int,) = first + second\n")
        declarations = parity_ledger.parse_functions(self.root, self.kotlin, "kotlin")
        self.assertEqual([("score", 2)], [(item.name, item.arity) for item in declarations])

    def test_generic_kotlin_extension_receivers_are_inventoried(self) -> None:
        self.kotlin.write_text(
            """fun Map<String, String>.cell(vararg keys: String) = ""
fun Map<String, String>.double(vararg keys: String) = 0.0
fun Map<String, String>.bool(vararg keys: String) = false
"""
        )
        declarations = parity_ledger.parse_functions(self.root, self.kotlin, "kotlin")
        self.assertEqual(["cell", "double", "bool"], [item.name for item in declarations])

    def test_computed_swift_and_kotlin_properties_are_paired(self) -> None:
        self.swift.write_text(
            """enum SleepStageTotals { struct Minutes {
    var asleep: Double { 1 }
    var inBed: Double { asleep + 1 }
} }
"""
        )
        self.kotlin.write_text(
            """object SleepStageTotals { data class Minutes(val awake: Double) {
    val asleep: Double get() = 1.0
    val inBed: Double
        get() { return asleep + 1.0 }
} }
"""
        )
        twin_map = parity_ledger.build_twin_map(self.root)
        self.assertEqual(2, len(twin_map["property_pairs"]))
        self.assertFalse(any(item.rule == "unmapped-property" for item in self.findings(twin_map)))

    def test_dead_twin_reference_is_rejected(self) -> None:
        self.write_clean_tree()
        self.swift.write_text(self.swift.read_text() + "\n/// Kotlin twin: `Engine.missingTarget`.\npublic func claimsMissingTwin() {}\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        rules = {finding.rule for finding in self.findings(twin_map)}
        self.assertIn("dead-twin-reference", rules)
        self.assertEqual(1, self.exit_code(twin_map))

    def test_normal_block_comment_reference_is_checked(self) -> None:
        self.write_clean_tree()
        self.kotlin.write_text(self.kotlin.read_text() + "\n/* Swift twin: MissingOwner.nope */\nfun claimant() = 1\n")
        self.assertTrue(any(item.rule == "dead-twin-reference" for item in self.findings()))

    def test_qualified_reference_requires_the_actual_owner(self) -> None:
        self.swift.write_text("enum Bar { static func existingName() {} }\n")
        self.kotlin.write_text("// Swift twin: Foo.existingName\nfun claim() = 1\n")
        findings = self.findings()
        self.assertTrue(any(item.rule == "dead-twin-reference" for item in findings))

    def test_constant_expression_is_fully_evaluated(self) -> None:
        self.swift.write_text("enum Engine { static let hours = 48 * 3_600 }\n")
        self.kotlin.write_text("object Engine { const val HOURS = 48L * 3_600L }\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        self.assertFalse(any(item.rule.startswith("constant-") for item in self.findings(twin_map)))
        self.kotlin.write_text("object Engine { const val HOURS = 48L * 3_601L }\n")
        self.assertTrue(any(item.rule == "constant-value-mismatch" for item in self.findings(twin_map)))

    def test_numeric_expression_parser_consumes_every_supported_operator(self) -> None:
        parsed = parity_ledger._literal("-(2 - 50) * (7_200 / 2) + 0x10 - 0b1")
        self.assertEqual("number:172815", parsed[0])
        self.assertIsNone(parity_ledger._literal("48 * 3_600 trailing"))

    def test_unparseable_mapped_constant_is_reported(self) -> None:
        self.swift.write_text("enum Engine { static let limit = makeLimit() }\n")
        self.kotlin.write_text("object Engine { const val LIMIT = 3 }\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        self.assertTrue(any(item.rule == "constant-unverifiable" for item in self.findings(twin_map)))

    def test_stale_constant_pair_is_reported_after_one_sided_rename(self) -> None:
        self.write_clean_tree()
        twin_map = parity_ledger.build_twin_map(self.root)
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT", "RENAMED_LIMIT"))

        stale = [item for item in self.findings(twin_map) if item.rule == "stale-constant-pair"]

        self.assertEqual(1, len(stale))
        self.assertIn("SAMPLE_LIMIT", stale[0].text)

    def test_resolvable_constant_pair_has_no_stale_finding(self) -> None:
        self.write_clean_tree()
        twin_map = parity_ledger.build_twin_map(self.root)

        self.assertFalse(any(item.rule == "stale-constant-pair" for item in self.findings(twin_map)))

    def test_constant_owner_disambiguates_same_normalized_name(self) -> None:
        self.swift.write_text("enum SedentaryDetector { static let defaultSmoothWindowS = 240.0 }\n")
        self.kotlin = self.kotlin.with_name("SedentaryDetector.kt")
        self.kotlin.write_text("object SedentaryDetector { const val DEFAULT_SMOOTH_WINDOW_S = 240.0 }\n")
        self.kotlin.with_name("NapDetector.kt").write_text(
            "object NapDetector { const val DEFAULT_SMOOTH_WINDOW_S = 120.0 }\n"
        )
        twin_map = parity_ledger.build_twin_map(self.root)
        pairs = twin_map["constant_pairs"]
        self.assertEqual(1, len(pairs))
        self.assertIn("SedentaryDetector", pairs[0]["kotlin"])
        self.assertFalse(any(item.rule == "constant-ambiguous" for item in self.findings(twin_map)))

    def test_remaining_constant_ambiguity_is_reported(self) -> None:
        self.swift.write_text("enum Engine { static let limit = 3 }\n")
        self.kotlin.write_text("object First { const val LIMIT = 3 }\n")
        self.kotlin.with_name("Second.kt").write_text("object Second { const val LIMIT = 3 }\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        self.assertTrue(any(item.rule == "constant-ambiguous" for item in self.findings(twin_map)))

    def test_constant_value_mismatch_is_rejected(self) -> None:
        self.write_clean_tree()
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 3", "SAMPLE_LIMIT = 4"))
        twin_map = parity_ledger.build_twin_map(self.root)
        rules = {finding.rule for finding in self.findings(twin_map)}
        self.assertIn("constant-value-mismatch", rules)
        self.assertEqual(1, self.exit_code(twin_map))

    def test_test_only_wiring_is_rejected(self) -> None:
        self.write_clean_tree()
        self.swift.write_text(self.swift.read_text() + "\npublic func testOnlyHelper(_ value: Int) -> Int { value }\n")
        test_path = self.root / "Packages/StrandAnalytics/Tests/StrandAnalyticsTests/EngineTests.swift"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("func testHelper() { _ = testOnlyHelper(1) }\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        rules = {finding.rule for finding in self.findings(twin_map)}
        self.assertIn("test-only-callsite", rules)
        self.assertEqual(1, self.exit_code(twin_map))

    def test_test_only_calls_use_owner_and_arity_and_ignore_extension_declaration(self) -> None:
        self.kotlin.write_text(
            """object Alpha { fun collide(first: Int, second: Int) = first + second }
object Beta { fun collide(value: Int) = value }
fun Map<String, String>.extensionOnly(value: Int) = value
fun production() = Alpha.collide(1, 2)
"""
        )
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/EngineTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Beta.collide(1); Map.extensionOnly(1) }\n")
        findings = [item for item in self.findings() if item.rule == "test-only-callsite"]
        texts = "\n".join(item.text for item in findings)
        self.assertIn("Beta.collide/1", texts)
        self.assertIn("Map.extensionOnly/1", texts)
        self.assertNotIn("Alpha.collide/1", texts)

    def test_call_omitting_defaults_counts_as_production_callsite(self) -> None:
        self.kotlin.write_text(
            """object Roller { fun roll(rr: Int, windowSec: Int = 90, stepSec: Int = 0) = rr + windowSec + stepSec }
fun production() = Roller.roll(1)
"""
        )
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/RollTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Roller.roll(1, 2, 3) }\n")
        texts = "\n".join(
            item.text for item in self.findings() if item.rule == "test-only-callsite"
        )
        self.assertNotIn("Roller.roll/3", texts)

    def test_unqualified_same_file_call_counts_despite_sibling_owner(self) -> None:
        self.kotlin.write_text(
            """object Verdict { fun verdict(value: Int) = value
    fun production() = verdict(1) }
"""
        )
        sibling = self.kotlin.parent / "Sibling.kt"
        sibling.write_text("object Sibling { fun verdict(first: Int, second: Int) = first + second }\n")
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/VerdictTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Verdict.verdict(1); Sibling.verdict(1, 2) }\n")
        texts = "\n".join(
            item.text for item in self.findings() if item.rule == "test-only-callsite"
        )
        self.assertNotIn("Verdict.verdict/1", texts)
        self.assertIn("Sibling.verdict/2", texts)

    def test_exact_arity_match_wins_over_relaxed_overload(self) -> None:
        self.kotlin.write_text(
            """object Over { fun pick(value: Int) = value
    fun pick(value: Int, extra: Int = 0) = value + extra }
fun production() = Over.pick(1)
"""
        )
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/OverTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Over.pick(1, 2) }\n")
        texts = "\n".join(
            item.text for item in self.findings() if item.rule == "test-only-callsite"
        )
        self.assertIn("Over.pick/2", texts)

    def test_lowercase_instance_receiver_resolves_like_unqualified(self) -> None:
        self.kotlin.write_text(
            """object Burst { fun codesWithTimes(first: Int, second: Int, extra: Int = 0) = first + second + extra }
object Assembler { val burst = Burst
    fun production(): Int { return burst.codesWithTimes(1, 2) } }
"""
        )
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/BurstTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Burst.codesWithTimes(1, 2, 3) }\n")
        texts = "\n".join(
            item.text for item in self.findings() if item.rule == "test-only-callsite"
        )
        self.assertNotIn("Burst.codesWithTimes/3", texts)

    def test_same_file_resolution_prefers_the_lexical_owner(self) -> None:
        self.kotlin.write_text(
            """object First { fun add(value: Int) = value
    fun production() = add(1) }
object Second { fun add(value: Int) = value }
"""
        )
        test_path = self.root / "android/app/src/test/java/com/noop/analytics/AddTest.kt"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("fun testIt() { Second.add(1) }\n")
        texts = "\n".join(
            item.text for item in self.findings() if item.rule == "test-only-callsite"
        )
        self.assertNotIn("First.add/1", texts)
        self.assertIn("Second.add/1", texts)

    def test_artificial_duplicate_is_rejected(self) -> None:
        self.write_clean_tree()
        extra = self.swift.with_name("Other.swift")
        extra.write_text("public func dayString(_ value: Int) -> String { \"x\" }\n")
        self.swift.write_text(self.swift.read_text() + "\npublic func dayString(_ value: Int, offset: Int) -> String { \"x\" }\n")
        twin_map = parity_ledger.build_twin_map(self.root)
        rules = {finding.rule for finding in self.findings(twin_map)}
        self.assertIn("duplicate-implementation", rules)
        self.assertEqual(1, self.exit_code(twin_map))

    def test_baseline_suppresses_known_finding_but_rejects_new_finding(self) -> None:
        self.write_clean_tree()
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 3", "SAMPLE_LIMIT = 4"))
        twin_map = parity_ledger.build_twin_map(self.root)
        baseline = self.baseline_for(twin_map)
        self.assertEqual(0, self.run_cli(twin_map, baseline)[0])
        self.swift.write_text(self.swift.read_text() + "\nfunc newRegression() {}\n")
        self.assertEqual(1, self.run_cli(twin_map, baseline)[0])

    def test_disappeared_baseline_finding_is_tolerated_and_reported(self) -> None:
        self.write_clean_tree()
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 3", "SAMPLE_LIMIT = 4"))
        twin_map = parity_ledger.build_twin_map(self.root)
        baseline = self.baseline_for(twin_map)
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 4", "SAMPLE_LIMIT = 3"))
        code, output = self.run_cli(twin_map, baseline)
        self.assertEqual(0, code)
        self.assertIn("IMPROVED 1", output)

    def test_counter_increase_beyond_baseline_is_rejected(self) -> None:
        self.write_clean_tree()
        old_map = parity_ledger.build_twin_map(self.root)
        baseline = self.baseline_for(old_map)
        self.swift.write_text(self.swift.read_text() + "\nfunc dayString(_ value: Int) -> String { \"x\" }\n")
        new_map = parity_ledger.build_twin_map(self.root)
        code, output = self.run_cli(new_map, baseline)
        self.assertEqual(1, code)
        self.assertIn("duplicate-counter", output)

    def test_changed_value_of_baselined_mismatch_is_a_new_finding(self) -> None:
        self.write_clean_tree()
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 3", "SAMPLE_LIMIT = 4"))
        twin_map = parity_ledger.build_twin_map(self.root)
        baseline = self.baseline_for(twin_map)
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 4", "SAMPLE_LIMIT = 5"))
        code, output = self.run_cli(twin_map, baseline)
        self.assertEqual(1, code)
        self.assertIn("constant-value-mismatch", output)

    def test_write_baseline_preserves_issue_and_additional_fields_by_identity(self) -> None:
        self.write_clean_tree()
        twin_map = parity_ledger.build_twin_map(self.root)
        self.kotlin.write_text(self.kotlin.read_text().replace("SAMPLE_LIMIT = 3", "SAMPLE_LIMIT = 4"))
        map_path = self.root / "map.json"
        baseline_path = self.root / "baseline.json"
        map_path.write_text(json.dumps(twin_map))
        generated = self.baseline_for(twin_map)
        generated["findings"][0]["issue"] = 123
        generated["findings"][0]["reviewed_by"] = "fixture"
        baseline_path.write_text(json.dumps(generated))

        with contextlib.redirect_stdout(io.StringIO()):
            code = parity_ledger.main(
                [
                    "--root", str(self.root),
                    "--map", str(map_path),
                    "--baseline", str(baseline_path),
                    "--write-baseline",
                ]
            )
        rewritten = json.loads(baseline_path.read_text())

        self.assertEqual(0, code)
        self.assertEqual(123, rewritten["findings"][0]["issue"])
        self.assertEqual("fixture", rewritten["findings"][0]["reviewed_by"])


if __name__ == "__main__":
    unittest.main()
