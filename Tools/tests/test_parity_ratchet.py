"""Acceptance tests for the declaration-level parity coverage ratchet."""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import parity_ratchet  # noqa: E402


class ParityRatchetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_lexer_keys_include_defaults_and_keep_overloads_distinct(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            """struct Engine {
    func score(_ value: Int, scale: Double = 1) -> Double { Double(value) * scale }
    func score(_ value: Double, scale: Double = 1) -> Double { value * scale }
    var doubled: Int { 2 }
    let seed: Int = 7
}
""",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            """class Engine {
    fun score(value: Int, scale: Double = 1.0): Double = value * scale
    val doubled: Int get() = 2
    val seed: Int = 7
    companion object Factory { init { require(true) } }
}
""",
        )

        swift_declarations = parity_ratchet.lex_file(self.root, swift, "swift")
        kotlin_declarations = parity_ratchet.lex_file(self.root, kotlin, "kotlin")
        score_keys = [item.key for item in swift_declarations if item.name == "score"]

        self.assertEqual(2, len(score_keys))
        self.assertEqual(2, len(set(score_keys)))
        self.assertTrue(all("defaults=2" in key for key in score_keys))
        self.assertEqual(
            {"function", "computed-property", "property-initializer"},
            {item.kind for item in swift_declarations},
        )
        self.assertIn("companion-init", {item.kind for item in kotlin_declarations})

    def test_lexer_fails_closed_on_unterminated_declaration(self) -> None:
        source = self.write("Sources/Pilot/Broken.swift", "struct Broken { func nope(_ value: Int { }\n")
        with self.assertRaisesRegex(parity_ratchet.LexError, "unclosed|unsupported"):
            parity_ratchet.lex_file(self.root, source, "swift")

        source = self.write("src/main/java/pilot/Broken.kt", "class Broken { fun nope(value: Int { }\n")
        with self.assertRaisesRegex(parity_ratchet.LexError, "unclosed|unsupported"):
            parity_ratchet.lex_file(self.root, source, "kotlin")

    def test_lexer_inventories_all_initializers_and_declaration_tokens_fail_closed(self) -> None:
        kotlin = self.write(
            "src/main/java/pilot/Initializers.kt",
            """class Initializers(val value: Int) {
    init { require(value >= 0) }
    constructor() : this(0)
    operator fun plus(other: Initializers) = Initializers(value + other.value)
    companion object { init { require(true) } }
}
""",
        )
        swift = self.write(
            "Sources/Pilot/Initializers.swift",
            """struct Initializers {
    init(value: Int) {}
    convenience init() { self.init(value: 0) }
}
""",
        )

        kotlin_declarations = parity_ratchet.lex_file(self.root, kotlin, "kotlin")
        swift_declarations = parity_ratchet.lex_file(self.root, swift, "swift")

        self.assertIn(("init", "initializer", 0), {
            (item.name, item.kind, item.arity) for item in kotlin_declarations
        })
        self.assertIn(("constructor", "initializer", 0), {
            (item.name, item.kind, item.arity) for item in kotlin_declarations
        })
        self.assertIn("plus", {item.name for item in kotlin_declarations})
        self.assertIn("companion-init", {item.kind for item in kotlin_declarations})
        self.assertEqual(2, len([item for item in swift_declarations if item.name == "init"]))

        for token, body in (
            ("getter", "get() = 1"),
            ("constructor", "constructor value: Int"),
            ("init", "init()"),
            ("operator", "operator fun plus = 1"),
        ):
            with self.subTest(token=token):
                malformed_kotlin = self.write(
                    f"src/main/java/pilot/Unknown{token}.kt",
                    f"class Unknown {{ {body} }}\n",
                )
                with self.assertRaisesRegex(parity_ratchet.LexError, "unsupported"):
                    parity_ratchet.lex_file(self.root, malformed_kotlin, "kotlin")

        malformed_swift = self.write(
            "Sources/Pilot/Unknown.swift", "struct Unknown { init {} }\n"
        )
        with self.assertRaisesRegex(parity_ratchet.LexError, "unsupported Swift init"):
            parity_ratchet.lex_file(self.root, malformed_swift, "swift")

    def test_property_lexer_handles_multiline_bodies_and_initializers(self) -> None:
        swift = self.write(
            "Sources/Pilot/Properties.swift",
            """enum Properties {
    static var computed: Int
    {
        2
    }
    static let disclaimer: String =
        "first "
        + "second"
    /// Documentation for the following declaration is not initializer code.
    static let next = 3
}
""",
        )
        kotlin = self.write(
            "src/main/java/pilot/Properties.kt",
            """object Properties {
    val computed: Int
        get() {
            return 2
        }
    const val disclaimer: String =
        "first " +
            "second"
    /** Documentation for the following declaration is not initializer code. */
    const val next = 3
}
""",
        )

        swift_items = {item.name: item for item in parity_ratchet.lex_file(self.root, swift, "swift")}
        kotlin_items = {item.name: item for item in parity_ratchet.lex_file(self.root, kotlin, "kotlin")}

        self.assertEqual(("computed-property", 5), (swift_items["computed"].kind, swift_items["computed"].end_line))
        self.assertEqual(("property-initializer", 8), (swift_items["disclaimer"].kind, swift_items["disclaimer"].end_line))
        self.assertEqual(("computed-property", 5), (kotlin_items["computed"].kind, kotlin_items["computed"].end_line))
        self.assertEqual(("property-initializer", 8), (kotlin_items["disclaimer"].kind, kotlin_items["disclaimer"].end_line))

    def test_bodyless_kotlin_type_does_not_turn_next_function_locals_into_members(self) -> None:
        source = self.write(
            "src/main/java/pilot/Bodyless.kt",
            """data class Result(val value: Int)
fun compute(): Int {
    val local = 1
    return local
}
""",
        )

        declarations = parity_ratchet.lex_file(self.root, source, "kotlin")

        self.assertEqual(["compute"], [item.name for item in declarations])

    def test_exempt_without_reason_is_rejected(self) -> None:
        shard = self.write(
            "Sources/Pilot/parity-exempt.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "module": "Pilot",
                    "exempt": [{"key": "Engine.swift::score/1[defaults=-]#1", "issue": 17}],
                    "platform-test": [],
                }
            ),
        )
        with self.assertRaisesRegex(parity_ratchet.RatchetError, "reason"):
            parity_ratchet.load_shard(shard)

    def test_every_existing_shard_entry_requires_an_issue(self) -> None:
        for category, metadata in (
            ("exempt", {"reason": "legacy"}),
            ("platform-test", {"test": "PilotTests.testLegacy"}),
        ):
            raw = {
                "schema_version": 1,
                "module": "Pilot",
                "exempt": [],
                "platform-test": [],
            }
            raw[category] = [{"key": "legacy", **metadata}]
            shard = self.write(f"{category}/parity-exempt.json", json.dumps(raw))
            with self.assertRaisesRegex(parity_ratchet.RatchetError, "issue"):
                parity_ratchet.load_shard(shard)

    def test_registered_function_with_uncovered_line_is_rejected(self) -> None:
        swift_source = self.write(
            "Sources/Pilot/Engine.swift",
            """enum Engine {
    static func score(_ value: Int) -> Int {
        if value > 0 { return value }
        return 0
    }
}
""",
        )
        kotlin_source = self.write(
            "src/main/java/pilot/Engine.kt",
            """object Engine {
    fun score(value: Int): Int {
        if (value > 0) return value
        return 0
    }
}
""",
        )
        swift_lcov = self.write(
            "swift.lcov",
            f"SF:{swift_source}\nDA:3,1\nDA:4,0\nend_of_record\n",
        )
        jacoco = self.write(
            "jacoco.xml",
            """<?xml version="1.0"?>
<report name="pilot"><package name="pilot"><sourcefile name="Engine.kt">
<line nr="3" mi="0" ci="2"/><line nr="4" mi="1" ci="0"/>
</sourcefile></package></report>
""",
        )

        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift_source, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin_source, "kotlin"),
        }
        errors = parity_ratchet.coverage_errors(
            declarations,
            {"score"},
            swift_lcov=swift_lcov,
            kotlin_jacoco=jacoco,
        )

        self.assertTrue(any("Engine.swift:4" in error for error in errors), errors)
        self.assertTrue(any("Engine.kt:4" in error for error in errors), errors)

    def test_function_counters_override_positive_same_line_coverage(self) -> None:
        swift_source = self.write(
            "Sources/Pilot/Functions.swift",
            "enum Functions { static func target() -> Int { 1 }; static func neighbor() -> Int { 2 } }\n",
        )
        kotlin_source = self.write(
            "src/main/java/pilot/Functions.kt",
            "object Functions { fun target(): Int = 1; fun neighbor(): Int = 2 }\n",
        )
        swift_lcov = self.write(
            "functions.lcov",
            f"SF:{swift_source}\nFN:1,target\nFN:1,neighbor\nFNDA:0,target\nFNDA:7,neighbor\nDA:1,7\nend_of_record\n",
        )
        jacoco = self.write(
            "functions.xml",
            """<?xml version="1.0"?>
<report name="pilot"><package name="pilot">
<class name="pilot/Functions" sourcefilename="Functions.kt">
<method name="target" desc="()I" line="1"><counter type="METHOD" missed="1" covered="0"/></method>
<method name="neighbor" desc="()I" line="1"><counter type="METHOD" missed="0" covered="1"/></method>
</class>
<sourcefile name="Functions.kt"><line nr="1" mi="0" ci="7"/></sourcefile>
</package></report>
""",
        )
        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift_source, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin_source, "kotlin"),
        }

        errors = parity_ratchet.coverage_errors(
            declarations,
            {"target"},
            swift_lcov=swift_lcov,
            kotlin_jacoco=jacoco,
        )

        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all("function was not executed" in error for error in errors), errors)

    def test_line_coverage_fallback_is_announced(self) -> None:
        swift_source = self.write("Sources/Pilot/Fallback.swift", "func target() -> Int { 1 }\n")
        kotlin_source = self.write("src/main/java/pilot/Fallback.kt", "fun target(): Int = 1\n")
        swift_lcov = self.write("fallback.lcov", f"SF:{swift_source}\nDA:1,1\nend_of_record\n")
        jacoco = self.write(
            "fallback.xml",
            "<report><package name=\"pilot\"><sourcefile name=\"Fallback.kt\"><line nr=\"1\" mi=\"0\" ci=\"1\"/></sourcefile></package></report>",
        )
        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift_source, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin_source, "kotlin"),
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            errors = parity_ratchet.coverage_errors(
                declarations, {"target"}, swift_lcov=swift_lcov, kotlin_jacoco=jacoco
            )
        self.assertEqual([], errors)
        self.assertEqual(2, output.getvalue().count("NOTICE: line coverage fallback"))

    def test_method_capable_report_never_falls_back_for_an_unmapped_function(self) -> None:
        swift_source = self.write("Sources/Pilot/Target.swift", "func target() -> Int { 1 }\n")
        kotlin_source = self.write("src/main/java/pilot/Target.kt", "fun target(): Int = 1\n")
        swift_lcov = self.write(
            "method-capable.lcov",
            f"SF:{swift_source}\nDA:1,1\nend_of_record\n"
            "SF:/tmp/Other.swift\nFN:1,other\nFNDA:1,other\nDA:1,1\nend_of_record\n",
        )
        jacoco = self.write(
            "method-capable.xml",
            """<report><package name="pilot">
<class name="pilot/Other" sourcefilename="Other.kt"><method name="other" desc="()I" line="1"><counter type="METHOD" missed="0" covered="1"/></method></class>
<sourcefile name="Target.kt"><line nr="1" mi="0" ci="1"/></sourcefile>
<sourcefile name="Other.kt"><line nr="1" mi="0" ci="1"/></sourcefile>
</package></report>""",
        )
        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift_source, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin_source, "kotlin"),
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            errors = parity_ratchet.coverage_errors(
                declarations, {"target"}, swift_lcov=swift_lcov, kotlin_jacoco=jacoco
            )
        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all("no " in error and "function coverage record" in error for error in errors))
        self.assertNotIn("fallback", output.getvalue())

    def init_git(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "ratchet@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Ratchet Test"], cwd=self.root, check=True)

    def commit_all(self, message: str) -> str:
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

    def shard(self, keys: list[dict[str, object]]) -> dict[str, object]:
        return {"schema_version": 1, "module": "Pilot", "exempt": keys, "platform-test": []}

    def test_ratchet_accepts_issue_backed_growth_and_decrease(self) -> None:
        self.init_git()
        path = self.write(
            "Sources/Pilot/parity-exempt.json",
            json.dumps(self.shard([{"key": "old", "reason": "legacy", "issue": 7}])),
        )
        baseline = self.write(
            "Tools/parity_ledger_baseline.json",
            json.dumps({"schema_version": 2, "findings": [], "counters": {"paths": 2}}),
        )
        base = self.commit_all("base")

        path.write_text(
            json.dumps(
                self.shard(
                    [
                        {"key": "old", "reason": "legacy", "issue": 7},
                        {"key": "new", "reason": "regression", "issue": 8},
                    ]
                )
            )
        )
        self.assertEqual([], parity_ratchet.compare_ratchet(self.root, base, offline=True))

        path.write_text(json.dumps(self.shard([])))
        baseline.write_text(json.dumps({"schema_version": 2, "findings": [], "counters": {"paths": 1}}))
        self.assertEqual([], parity_ratchet.compare_ratchet(self.root, base, offline=True))

    def test_ratchet_rejects_removing_a_merge_base_shard_as_disarmed(self) -> None:
        self.init_git()
        swift = self.write(
            "Sources/Pilot/parity-exempt.json",
            json.dumps(self.shard([{"key": "old", "reason": "legacy", "issue": 7}])),
        )
        self.write(
            "src/main/java/pilot/parity-exempt.json",
            json.dumps(self.shard([{"key": "old", "reason": "legacy", "issue": 7}])),
        )
        self.write(
            "Tools/parity_ledger_baseline.json",
            json.dumps({"schema_version": 2, "findings": [], "counters": {}}),
        )
        base = self.commit_all("base")
        swift.unlink()

        errors = parity_ratchet.compare_ratchet(self.root, base, offline=True)

        self.assertTrue(any("disarmed" in error and "Sources/Pilot" in error for error in errors), errors)

    def test_shardless_pair_is_not_a_notice_when_merge_base_had_shards(self) -> None:
        swift_dir = self.root / "Sources/Pilot"
        kotlin_dir = self.root / "src/main/java/pilot"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            errors, _counts = parity_ratchet.audit_module_pair(
                self.root,
                parity_ratchet.ModulePair("Pilot", swift_dir, kotlin_dir),
                base_shards={
                    "Sources/Pilot/parity-exempt.json",
                    "src/main/java/pilot/parity-exempt.json",
                },
            )
        self.assertTrue(any("disarmed" in error for error in errors), errors)
        self.assertNotIn("NOTICE", output.getvalue())

    def test_explicit_base_ref_is_resolved_to_the_merge_base(self) -> None:
        self.init_git()
        marker = self.write("marker.txt", "base\n")
        common = self.commit_all("common")
        feature_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=self.root, text=True
        ).strip()
        subprocess.run(["git", "branch", "upstream"], cwd=self.root, check=True)
        marker.write_text("feature\n")
        self.commit_all("feature")
        subprocess.run(["git", "checkout", "-q", "upstream"], cwd=self.root, check=True)
        marker.write_text("upstream\n")
        self.commit_all("upstream")
        subprocess.run(["git", "checkout", "-q", feature_branch], cwd=self.root, check=True)

        self.assertEqual(common, parity_ratchet.resolve_base(self.root, "upstream"))

    def test_module_without_shard_is_notice_not_failure(self) -> None:
        swift_dir = self.root / "Sources/Pilot"
        kotlin_dir = self.root / "src/main/java/pilot"
        self.write("Sources/Pilot/Engine.swift", "struct Engine {}\n")
        self.write("src/main/java/pilot/Engine.kt", "class Engine\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            errors, counts = parity_ratchet.audit_module_pair(
                self.root,
                parity_ratchet.ModulePair("Pilot", swift_dir, kotlin_dir),
            )
        self.assertEqual([], errors)
        self.assertEqual({"differential": 0, "platform-test": 0, "exempt": 0}, counts)
        self.assertIn("NOTICE", output.getvalue())

    def test_verify_requires_both_report_arguments(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = parity_ratchet.main(["--root", str(self.root), "verify"])
        self.assertEqual(2, code)
        self.assertIn("--swift-lcov", stderr.getvalue())
        self.assertIn("--kotlin-jacoco", stderr.getvalue())

    def test_ci_rejects_offline_ratchet_mode(self) -> None:
        stderr = io.StringIO()
        with mock.patch.dict("os.environ", {"CI": "true"}, clear=False), contextlib.redirect_stderr(stderr):
            code = parity_ratchet.main(["--root", str(self.root), "ratchet", "--offline"])
        self.assertEqual(2, code)
        self.assertIn("--offline", stderr.getvalue())

    def test_repository_registry_is_runtime_derived_and_shards_hold_no_differential_list(self) -> None:
        registered, errors = parity_ratchet.registered_differential(REPOSITORY)
        self.assertEqual([], errors)
        self.assertEqual({"rollingRmssd", "trimpToStrain"}, registered)
        for path in (
            REPOSITORY / "Packages/StrandAnalytics/Sources/StrandAnalytics/parity-exempt.json",
            REPOSITORY / "android/app/src/main/java/com/noop/analytics/parity-exempt.json",
        ):
            raw = json.loads(path.read_text())
            self.assertNotIn("differential", raw)

    def test_parity_workflow_has_inverted_trigger_and_pinned_actions(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/parity-ratchet.yml").read_text()
        trigger = workflow.split("concurrency:", 1)[0]
        self.assertNotIn("paths:", trigger)
        self.assertIn('git diff --name-only "$MERGE_BASE"', workflow)
        self.assertIn("no-op:", workflow)
        uses = re.findall(r"uses:\s*[^@\s]+@([^\s]+)", workflow)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in uses), uses)

    def test_coverage_workflow_treats_harness_and_tooling_changes_as_touched(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/parity-ratchet.yml").read_text()
        for pattern in (
            "Packages/*/Tests/**/*ParityRunner*",
            "android/app/src/test/**",
            "Tools/parity_*.py",
            "Tools/parity_cases/**",
            "Tools/tests/**",
            "**/parity-exempt.json",
            "android/app/build.gradle.kts",
            "android/gradle/verification-metadata.xml",
            ".github/workflows/parity-ratchet.yml",
        ):
            self.assertIn(pattern, workflow)


class JacocoOverloadDisambiguationTests(unittest.TestCase):
    def test_executed_overload_cannot_vouch_for_registered_same_line_sibling(self) -> None:
        import parity_ratchet

        self.assertEqual(2, parity_ratchet._jvm_descriptor_arity("(II)I"))
        self.assertEqual(1, parity_ratchet._jvm_descriptor_arity("(Ljava/util/List;)V"))
        self.assertEqual(3, parity_ratchet._jvm_descriptor_arity("([IJLjava/lang/String;)V"))
        executed = parity_ratchet.CoverageMethod("score", 2, 5, arity=1)
        idle = parity_ratchet.CoverageMethod("score", 2, 0, arity=2)
        source = parity_ratchet.CoverageSource({2: 5}, [executed, idle])
        declaration = parity_ratchet.Declaration(
            language="kotlin",
            path="src/main/java/pilot/Engine.kt",
            owner="Engine",
            name="score",
            kind="function",
            arity=2,
            defaults=(),
            ordinal=1,
            line=2,
            end_line=4,
        )
        status, executions = parity_ratchet._function_executions(source, declaration, "kotlin")
        self.assertEqual(("found", 0), (status, executions))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
