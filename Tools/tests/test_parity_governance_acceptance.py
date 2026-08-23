"""Acceptance tests for the product-free parity governance foundation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import issue_ref  # noqa: E402
import parity_ledger  # noqa: E402
import parity_ratchet  # noqa: E402


class IssueReferenceTests(unittest.TestCase):
    def test_current_metadata_requires_repository_qualified_references(self) -> None:
        reference = issue_ref.parse_current("bhelm/noop#17")
        self.assertEqual(("bhelm/noop", 17), (reference.repo, reference.number))
        for invalid in (17, True, "#17", " bhelm/noop#17", "bhelm/noop#0"):
            with self.subTest(invalid=invalid), self.assertRaises(issue_ref.IssueRefError):
                issue_ref.parse_current(invalid)

    def test_repository_is_part_of_issue_identity(self) -> None:
        self.assertNotEqual(
            issue_ref.parse_current("bhelm/noop#17"),
            issue_ref.parse_current("other/noop#17"),
        )


class RepositoryBaselineTests(unittest.TestCase):
    def test_tools_workflow_triggers_for_nested_governance_tests(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/tools-python.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(2, workflow.count("- 'Tools/tests/**'"))

    def test_checked_in_inventory_and_baseline_match_current_sources(self) -> None:
        twin_map = parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        baseline = parity_ledger._load_json(TOOLS / "parity_ledger_baseline.json", {})
        result = parity_ledger.scan(REPOSITORY, twin_map)
        self.assertEqual([], result.errors)
        self.assertEqual([], parity_ledger.compact_baseline_drift(result, baseline))
        self.assertEqual(result.counters, baseline["counters"])

    def test_checked_metadata_is_compact_v3_and_expands_losslessly(self) -> None:
        checked = parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        expanded, drift = parity_ledger.expand_twin_map(REPOSITORY, checked)
        self.assertEqual(3, checked["schema_version"])
        self.assertEqual([], drift)
        self.assertEqual(parity_ledger.build_twin_map(REPOSITORY), expanded)
        self.assertNotIn("unpaired_functions", checked)
        self.assertNotIn("constant_pairs", checked)

        baseline = parity_ledger._load_json(TOOLS / "parity_ledger_baseline.json", {})
        self.assertEqual(3, baseline["schema_version"])
        self.assertNotIn("findings", baseline)
        self.assertTrue(baseline["accepted_findings"])
        for group in baseline["accepted_findings"]:
            self.assertTrue(group["reason"].strip())
            self.assertTrue(group["provenance"].strip())
            self.assertGreater(group["count"], 0)
            self.assertRegex(group["identities_sha256"], r"^[0-9a-f]{64}$")

    def test_v3_authority_schema_is_exact_and_canonically_ordered(self) -> None:
        compact = parity_ledger.build_compact_twin_map(REPOSITORY)
        self.assertEqual(
            list(parity_ledger.SEMANTIC_AUTHORITY_SETS), list(compact["authority"])
        )
        parity_ratchet._validate_twin_map(compact, "fixture")

        malformed = json.loads(json.dumps(compact))
        malformed["authority"]["unknown"] = {"count": 0, "sha256": "0" * 64}
        with self.assertRaisesRegex(parity_ratchet.RatchetError, "every semantic set exactly"):
            parity_ratchet._validate_twin_map(malformed, "fixture")

        unknown = json.loads(json.dumps(compact))
        unknown["ignored_override"] = True
        with self.assertRaisesRegex(parity_ratchet.RatchetError, "top-level keys"):
            parity_ratchet._validate_twin_map(unknown, "fixture")

        misleading = json.loads(json.dumps(compact))
        misleading["scope"]["swift_roots"] = ["Elsewhere"]
        with self.assertRaisesRegex(parity_ratchet.RatchetError, "exact derivation roots"):
            parity_ratchet._validate_twin_map(misleading, "fixture")

        baseline = parity_ledger._load_json(TOOLS / "parity_ledger_baseline.json", {})
        tampered = json.loads(json.dumps(baseline))
        tampered["accepted_findings"][0]["reason"] = "arbitrary non-empty replacement"
        with self.assertRaisesRegex(parity_ratchet.RatchetError, "canonical reviewed reason"):
            parity_ratchet._validate_baseline(tampered, "fixture")

    def test_repository_metadata_uses_invariants_not_frozen_counts_or_commits(self) -> None:
        for relative in ("parity_twin_map.json", "parity_ledger_baseline.json"):
            value = json.loads((TOOLS / relative).read_text(encoding="utf-8"))
            issue_ref.validate_current_issue_fields(value, relative)
            self.assertNotIn("expected_count", json.dumps(value))
            self.assertNotIn("source_commit", json.dumps(value))

    def test_checked_function_pairs_equal_current_attached_source_claims(self) -> None:
        (
            swift_files,
            kotlin_files,
            swift_functions,
            kotlin_functions,
            _swift_properties,
            _kotlin_properties,
            _swift_constants,
            _kotlin_constants,
        ) = parity_ledger._inventory(REPOSITORY)
        references = (
            parity_ledger.parse_twin_references(
                REPOSITORY, swift_files, "swift", swift_functions
            )
            + parity_ledger.parse_twin_references(
                REPOSITORY, kotlin_files, "kotlin", kotlin_functions
            )
        )
        _reference_files, reference_declarations = parity_ledger._reference_declarations(REPOSITORY)
        repo_swift_functions = [
            item for item in reference_declarations
            if item.language == "swift" and item.kind == "function"
        ]
        repo_kotlin_functions = [
            item for item in reference_declarations
            if item.language == "kotlin" and item.kind == "function"
        ]
        declared = set(
            parity_ledger.resolved_attached_function_pairs(
                references, repo_swift_functions, repo_kotlin_functions
            )
        )
        twin_map, drift = parity_ledger.expand_twin_map(
            REPOSITORY, parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        )
        self.assertEqual([], drift)
        checked = {
            (item["swift"], item["kotlin"])
            for item in twin_map["function_pairs"]
        }
        self.assertEqual(declared, checked)
        declared_files = parity_ledger.resolved_file_pairs(
            declared, repo_swift_functions, repo_kotlin_functions
        )
        checked_files = {
            (item["swift"], item["kotlin"])
            for item in twin_map["file_pairs"]
        }
        self.assertEqual(declared_files, checked_files)

    def test_every_nonunique_attached_claim_has_one_explicit_finding(self) -> None:
        (
            swift_files,
            kotlin_files,
            swift_functions,
            kotlin_functions,
            _swift_properties,
            _kotlin_properties,
            _swift_constants,
            _kotlin_constants,
        ) = parity_ledger._inventory(REPOSITORY)
        references = (
            parity_ledger.parse_twin_references(
                REPOSITORY, swift_files, "swift", swift_functions
            )
            + parity_ledger.parse_twin_references(
                REPOSITORY, kotlin_files, "kotlin", kotlin_functions
            )
        )
        _reference_files, reference_declarations = parity_ledger._reference_declarations(REPOSITORY)
        repo_swift_functions = [
            item for item in reference_declarations
            if item.language == "swift" and item.kind == "function"
        ]
        repo_kotlin_functions = [
            item for item in reference_declarations
            if item.language == "kotlin" and item.kind == "function"
        ]
        resolutions = parity_ledger.attached_function_resolutions(
            references, repo_swift_functions, repo_kotlin_functions
        )
        unresolved_sites = {
            (reference.path, reference.line)
            for reference, candidates in resolutions.items()
            if len(candidates) != 1
        }
        twin_map, drift = parity_ledger.expand_twin_map(
            REPOSITORY, parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        )
        self.assertEqual([], drift)
        result = parity_ledger.scan(REPOSITORY, twin_map)
        finding_sites = {
            (finding.path, finding.line)
            for finding in result.findings
            if finding.rule in {
                "unresolved-attached-function-claim",
                "ambiguous-attached-function-claim",
            }
        }
        self.assertEqual(unresolved_sites, finding_sites)

    def test_repository_has_only_correct_collapse_pair_and_no_rank_waiver(self) -> None:
        twin_map, drift = parity_ledger.expand_twin_map(
            REPOSITORY, parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        )
        self.assertEqual([], drift)
        pairs = {
            (item["swift"], item["kotlin"])
            for item in twin_map["function_pairs"]
        }
        wrong = (
            "Packages/StrandAnalytics/Sources/StrandAnalytics/HRVAnalyzer.swift::collapseOverCount/4#1",
            "android/app/src/main/java/com/noop/analytics/HrvAnalyzer.kt::collapsedCoverage/3#1",
        )
        correct = (
            "Packages/StrandAnalytics/Sources/StrandAnalytics/HRVAnalyzer.swift::collapseOverCount/4#1",
            "android/app/src/main/java/com/noop/analytics/HrvAnalyzer.kt::collapseOverCount/4#1",
        )
        self.assertNotIn(wrong, pairs)
        self.assertIn(correct, pairs)
        kotlin_targets = [kotlin for _swift, kotlin in pairs]
        self.assertEqual(len(kotlin_targets), len(set(kotlin_targets)))

        baseline = parity_ledger._load_json(TOOLS / "parity_ledger_baseline.json", {})
        result = parity_ledger.scan(REPOSITORY, twin_map)
        self.assertFalse(any("TestCentreLayout.swift::rank/1#1" in item.identity for item in result.findings))

    def test_checked_constant_pairs_equal_current_dynamic_pairs(self) -> None:
        (
            _swift_files,
            _kotlin_files,
            _swift_functions,
            _kotlin_functions,
            _swift_properties,
            _kotlin_properties,
            swift_constants,
            kotlin_constants,
        ) = parity_ledger._inventory(REPOSITORY)
        twin_map, drift = parity_ledger.expand_twin_map(
            REPOSITORY, parity_ledger._load_json(TOOLS / "parity_twin_map.json", {})
        )
        self.assertEqual([], drift)
        file_pairs = {
            (item["swift"], item["kotlin"])
            for item in twin_map["file_pairs"]
        }
        dynamic, _ambiguous = parity_ledger._constant_pairing(
            swift_constants, kotlin_constants, file_pairs
        )
        resolved = {(swift.key, kotlin.key) for swift, kotlin in dynamic}
        checked = {
            (item["swift"], item["kotlin"])
            for item in twin_map["constant_pairs"]
        }
        self.assertEqual(resolved, checked)


class GovernanceRatchetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, value: object) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def commit(self) -> str:
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

    def test_unreadable_base_blob_is_not_treated_as_absent_bootstrap_metadata(self) -> None:
        self.write("Tools/parity_twin_map.json", {"schema_version": 3})
        base = self.commit()
        real_check_output = parity_ratchet.subprocess.check_output

        def fail_only_show(arguments, **kwargs):
            if arguments[:2] == ["git", "show"]:
                raise subprocess.CalledProcessError(128, arguments, output="missing blob")
            return real_check_output(arguments, **kwargs)

        with mock.patch.object(
            parity_ratchet.subprocess, "check_output", side_effect=fail_only_show
        ):
            with self.assertRaisesRegex(parity_ratchet.RatchetError, "cannot read base"):
                parity_ratchet._read_base(
                    self.root, base, "Tools/parity_twin_map.json"
                )

    def test_regenerating_compact_map_and_baseline_cannot_accept_new_unpaired_source(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine {}\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        swift.write_text("enum Engine { static func newlyUnpaired() {} }\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)

        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)

        self.assertTrue(
            any("derived inventory changed without an exact issue-bound authority change" in error for error in errors),
            errors,
        )

    def test_regenerating_compact_metadata_cannot_accept_one_sided_constant(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine {}\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        swift.write_text("enum Engine { static let swiftOnlyLimit = 7 }\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)

        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)
        self.assertTrue(
            any("add-unpaired-constant" in error and "swiftOnlyLimit" in error for error in errors),
            errors,
        )

    def test_exact_compact_exemption_allows_only_its_current_delta(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine {}\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        swift.write_text("enum Engine { static func newlyUnpaired() {} }\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        identity = next(
            item for item in parity_ledger.semantic_authority(self.root)["unpaired_functions"]
            if "newlyUnpaired" in item
        )
        compact["exemptions"] = [{
            "kind": "add-unpaired-function",
            "identity": identity,
            "identity_sha256": parity_ledger._canonical_sha256(identity),
            "issue": "bhelm/noop#78",
            "reason": "Exact synthetic unpaired function accepted for this focused ratchet test.",
        }]
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        self.assertEqual([], parity_ratchet.compare_metadata(self.root, base, offline=True))

        swift.write_text("enum Engine {}\n", encoding="utf-8")
        refreshed = parity_ledger.build_compact_twin_map(self.root)
        refreshed["exemptions"] = compact["exemptions"]
        self.write("Tools/parity_twin_map.json", refreshed)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, refreshed)),
        )
        warnings: list[str] = []
        errors = parity_ratchet.compare_metadata(
            self.root, base, offline=True, warnings=warnings
        )
        self.assertEqual([], errors)
        self.assertTrue(any("obsolete exemption" in warning for warning in warnings), warnings)
        with mock.patch.object(parity_ratchet, "_fetch_issue") as fetched:
            online_warnings: list[str] = []
            self.assertEqual(
                [],
                parity_ratchet.compare_metadata(
                    self.root, base, offline=False, warnings=online_warnings
                ),
            )
        fetched.assert_not_called()

    def test_debt_decrease_needs_no_metadata_rewrite_and_warns(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine { static func oldDebt() {} }\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        swift.write_text("enum Engine {}\n", encoding="utf-8")
        warnings: list[str] = []
        self.assertEqual(
            [],
            parity_ratchet.compare_metadata(
                self.root, base, offline=True, warnings=warnings
            ),
        )
        self.assertTrue(any("debt decreased" in warning for warning in warnings), warnings)

    def test_obsolete_inherited_exemption_cannot_authorize_reintroduction(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine {}\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        identity = "swift\0Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift::oldDebt/0#1"
        compact["exemptions"] = [{
            "kind": "add-unpaired-function",
            "identity": identity,
            "identity_sha256": parity_ledger._canonical_sha256(identity),
            "issue": "bhelm/noop#78",
            "reason": "Historical synthetic debt retained to prove stale authority cannot revive it.",
        }]
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        swift.write_text("enum Engine { static func oldDebt() {} }\n", encoding="utf-8")
        refreshed = parity_ledger.build_compact_twin_map(self.root)
        refreshed["exemptions"] = compact["exemptions"]
        self.write("Tools/parity_twin_map.json", refreshed)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, refreshed)),
        )
        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)
        self.assertTrue(any("add-unpaired-function" in error and "oldDebt" in error for error in errors), errors)

    def test_removing_a_real_twin_claim_is_not_treated_as_debt_reduction(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text(
            "enum Engine {\n    /// Kotlin twin: `Engine.score`.\n    static func score(_ value: Int) -> Int { value }\n}\n",
            encoding="utf-8",
        )
        kotlin.write_text(
            "object Engine { fun score(value: Int): Int = value }\n", encoding="utf-8"
        )
        compact = parity_ledger.build_compact_twin_map(self.root)
        self.write("Tools/parity_twin_map.json", compact)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact)),
        )
        base = self.commit()

        swift.write_text(
            "enum Engine { static func score(_ value: Int) -> Int { value } }\n",
            encoding="utf-8",
        )
        refreshed = parity_ledger.build_compact_twin_map(self.root)
        self.write("Tools/parity_twin_map.json", refreshed)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, refreshed)),
        )
        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)
        self.assertTrue(any("remove-function-pair" in error for error in errors), errors)

    def test_lower_debt_count_cannot_mask_replacement_identity_in_ratchet(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text(
            "enum Engine { static func oldOne() {}\n static func oldTwo() {} }\n",
            encoding="utf-8",
        )
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        self.write("Tools/parity_twin_map.json", compact)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact)),
        )
        base = self.commit()

        swift.write_text("enum Engine { static func replacement() {} }\n", encoding="utf-8")
        refreshed = parity_ledger.build_compact_twin_map(self.root)
        self.write("Tools/parity_twin_map.json", refreshed)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, refreshed)),
        )
        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)
        self.assertTrue(
            any("add-unpaired-function" in error and "replacement" in error for error in errors),
            errors,
        )

    def test_unchanged_inherited_exemption_stays_valid_without_refresh(self) -> None:
        swift = self.root / "Packages/StrandAnalytics/Sources/StrandAnalytics/Engine.swift"
        kotlin = self.root / "android/app/src/main/java/com/noop/analytics/Engine.kt"
        swift.parent.mkdir(parents=True, exist_ok=True)
        kotlin.parent.mkdir(parents=True, exist_ok=True)
        swift.write_text("enum Engine { static func inheritedDebt() {} }\n", encoding="utf-8")
        kotlin.write_text("object Engine {}\n", encoding="utf-8")
        compact = parity_ledger.build_compact_twin_map(self.root)
        identity = next(
            item for item in parity_ledger.semantic_authority(self.root)["unpaired_functions"]
            if "inheritedDebt" in item
        )
        compact["exemptions"] = [{
            "kind": "add-unpaired-function",
            "identity": identity,
            "identity_sha256": parity_ledger._canonical_sha256(identity),
            "issue": "bhelm/noop#78",
            "reason": "Exact inherited synthetic debt retained while its source identity still exists.",
        }]
        baseline = parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact))
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", baseline)
        base = self.commit()

        self.assertEqual([], parity_ratchet.compare_metadata(self.root, base, offline=True))
        payload = {
            "number": 78,
            "repository_url": "https://api.github.com/repos/bhelm/noop",
            "html_url": "https://github.com/bhelm/noop/issues/78",
        }
        with mock.patch.object(parity_ratchet, "_fetch_issue", return_value=payload) as fetched:
            self.assertEqual([], parity_ratchet.compare_metadata(self.root, base, offline=False))
        fetched.assert_called_once()

    def test_bootstrap_on_base_without_governance_files_is_allowed(self) -> None:
        marker = self.root / "README"
        marker.write_text("base\n", encoding="utf-8")
        base = self.commit()
        compact = parity_ledger.build_compact_twin_map(self.root)
        self.write("Tools/parity_twin_map.json", compact)
        self.write("Tools/parity_ledger_baseline.json", parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact)))
        self.assertEqual([], parity_ratchet.compare_metadata(self.root, base, offline=True))

    def test_bootstrap_cannot_introduce_exemptions(self) -> None:
        marker = self.root / "README"
        marker.write_text("base\n", encoding="utf-8")
        base = self.commit()
        compact = parity_ledger.build_compact_twin_map(self.root)
        identity = "swift\0Example.swift::invented/0#1"
        compact["exemptions"] = [{
            "kind": "bootstrap-unpaired-function",
            "identity": identity,
            "identity_sha256": parity_ledger._canonical_sha256(identity),
            "issue": "bhelm/noop#18",
            "reason": "Bootstrap must not accept pre-reviewed debt implicitly.",
        }]
        self.write("Tools/parity_twin_map.json", compact)
        self.write(
            "Tools/parity_ledger_baseline.json",
            parity_ledger.build_compact_baseline(parity_ledger.scan(self.root, compact)),
        )

        errors = parity_ratchet.compare_metadata(self.root, base, offline=True)

        self.assertTrue(any("bootstrap cannot introduce exemptions" in error for error in errors), errors)

    def test_same_issue_number_in_wrong_repository_fails_closed(self) -> None:
        ref = issue_ref.parse_current("bhelm/noop#77")
        response = subprocess.CompletedProcess(
            [], 0,
            json.dumps({
                "number": 77,
                "repository_url": "https://api.github.com/repos/other/noop",
                "html_url": "https://github.com/other/noop/issues/77",
            }),
            "",
        )
        with mock.patch.object(parity_ratchet.subprocess, "run", return_value=response):
            self.assertFalse(parity_ratchet.issue_exists(ref))

    def test_pull_request_and_ambiguous_response_fail_closed(self) -> None:
        ref = issue_ref.parse_current("bhelm/noop#77")
        for payload in (
            {"number": 77, "pull_request": {}, "html_url": "https://github.com/bhelm/noop/issues/77"},
            {"number": 77},
            {"number": True, "repository_url": "https://api.github.com/repos/bhelm/noop"},
        ):
            with self.subTest(payload=payload), mock.patch.object(
                parity_ratchet.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
            ):
                self.assertFalse(parity_ratchet.issue_exists(ref))

    def test_exemption_issue_must_be_fresh_and_bind_exact_identity_hash(self) -> None:
        ref = issue_ref.parse_current("bhelm/noop#78")
        payload = {
            "number": 78,
            "repository_url": "https://api.github.com/repos/bhelm/noop",
            "html_url": "https://github.com/bhelm/noop/issues/78",
            "created_at": "2026-08-21T12:00:00Z",
            "body": "parity-governance-identity-sha256: " + "a" * 64,
        }
        response = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with mock.patch.object(parity_ratchet.subprocess, "run", return_value=response):
            self.assertTrue(
                parity_ratchet.exemption_issue_is_bound(
                    ref, "a" * 64, "2026-08-21T11:00:00+00:00"
                )
            )
            self.assertFalse(
                parity_ratchet.exemption_issue_is_bound(
                    ref, "b" * 64, "2026-08-21T11:00:00+00:00"
                )
            )
            self.assertFalse(
                parity_ratchet.exemption_issue_is_bound(
                    ref, "a" * 64, "2026-08-21T13:00:00+00:00"
                )
            )

    def test_default_base_is_exact_current_origin_main_not_an_old_merge_base(self) -> None:
        marker = self.root / "marker"
        marker.write_text("common\n", encoding="utf-8")
        common = self.commit()
        subprocess.run(["git", "checkout", "-qb", "candidate"], cwd=self.root, check=True)
        marker.write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "candidate"], cwd=self.root, check=True)
        subprocess.run(["git", "checkout", "-qb", "upstream", common], cwd=self.root, check=True)
        marker.write_text("upstream\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "upstream"], cwd=self.root, check=True)
        upstream = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", upstream], cwd=self.root, check=True)
        subprocess.run(["git", "checkout", "-q", "candidate"], cwd=self.root, check=True)

        self.assertEqual(upstream, parity_ratchet.resolve_base(self.root, None))


if __name__ == "__main__":
    unittest.main()
