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

    def test_multiline_swift_return_and_where_clauses_have_executable_bodies(self) -> None:
        source = self.write(
            "Sources/Pilot/Multiline.swift",
            """func arrow()
    -> Int {
    1
}
func constrained<T>()
    where T: Equatable {
    2
}
""",
        )

        declarations = parity_ratchet.lex_file(self.root, source, "swift")

        self.assertEqual(["arrow", "constrained"], [item.name for item in declarations])
        self.assertTrue(all(item.coverable for item in declarations))
        self.assertEqual([4, 8], [item.end_line for item in declarations])

    def test_bodyless_swift_requirement_does_not_capture_following_property_body(self) -> None:
        source = self.write(
            "Sources/Pilot/Requirements.swift",
            """protocol Requirements {
    func requirement()
    var value: Int { get }
}
""",
        )

        declarations = parity_ratchet.lex_file(self.root, source, "swift")
        requirement = next(item for item in declarations if item.name == "requirement")

        self.assertFalse(requirement.coverable)
        self.assertEqual(2, requirement.end_line)

    def test_bodyless_kotlin_function_does_not_capture_following_nested_type(self) -> None:
        source = self.write(
            "src/main/java/pilot/Requirements.kt",
            """interface Requirements {
    fun requirement()
    class Nested {
        fun nested() = 1
    }
}
""",
        )

        declarations = parity_ratchet.lex_file(self.root, source, "kotlin")
        requirement = next(item for item in declarations if item.name == "requirement")

        self.assertFalse(requirement.coverable)
        self.assertEqual(2, requirement.end_line)

    def test_explicit_kotlin_primary_constructor_is_not_a_body_constructor(self) -> None:
        source = self.write(
            "src/main/java/pilot/Primary.kt",
            """class Primary constructor(val value: Int) {
    fun doubled() = value * 2
}
""",
        )

        declarations = parity_ratchet.lex_file(self.root, source, "kotlin")

        self.assertEqual(["doubled"], [item.name for item in declarations])

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

    def kotlin_expression_body_errors(
        self, method_records: list[tuple[int, int]]
    ) -> list[str]:
        swift_source = self.write(
            "Sources/Pilot/Expressions.swift", "func target(_ value: Int) -> Int { value + 1 }\n"
        )
        kotlin_source = self.write(
            "src/main/java/pilot/Expressions.kt",
            """fun target(value: Int): Int =
    value + 1
""",
        )
        swift_lcov = self.write(
            "expressions.lcov",
            f"SF:{swift_source}\nFN:1,target\nFNDA:1,target\nDA:1,1\nend_of_record\n",
        )
        methods = "".join(
            f'<method name="target" desc="(I)I" line="{line}">'
            f'<counter type="METHOD" missed="{int(covered == 0)}" covered="{covered}"/>'
            "</method>"
            for line, covered in method_records
        )
        jacoco = self.write(
            "expressions.xml",
            f"""<report><package name="pilot">
<class name="pilot/Expressions" sourcefilename="Expressions.kt">{methods}</class>
<sourcefile name="Expressions.kt"><line nr="2" mi="0" ci="1"/></sourcefile>
</package></report>""",
        )
        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift_source, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin_source, "kotlin"),
        }
        self.assertEqual(
            (1, 1),
            (declarations["kotlin"][0].line, declarations["kotlin"][0].end_line),
        )
        return parity_ratchet.coverage_errors(
            declarations,
            {"target"},
            swift_lcov=swift_lcov,
            kotlin_jacoco=jacoco,
        )

    def test_kotlin_expression_body_accepts_unique_method_on_following_line(self) -> None:
        self.assertEqual([], self.kotlin_expression_body_errors([(2, 1)]))

    def test_kotlin_expression_body_rejects_unexecuted_method_on_following_line(self) -> None:
        errors = self.kotlin_expression_body_errors([(2, 0)])

        self.assertEqual(1, len(errors), errors)
        self.assertIn("registered function was not executed", errors[0])

    def test_kotlin_expression_body_rejects_ambiguous_methods_outside_extent(self) -> None:
        errors = self.kotlin_expression_body_errors([(2, 1), (3, 1)])

        self.assertEqual(1, len(errors), errors)
        self.assertIn("no kotlin function coverage record", errors[0])

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

    def test_malformed_registry_key_is_an_explicit_error_not_a_silent_drop(self) -> None:
        self.write(
            parity_ratchet.SWIFT_RUNNER,
            'switch name {\ncase "score/1":\ncase "broken/":\ndefault: break\n}\n',
        )
        self.write(
            parity_ratchet.KOTLIN_RUNNER,
            'when (name) {\n"score/1" -> run()\n"broken/" -> run()\n}\n',
        )

        registered, errors = parity_ratchet.registered_differential(self.root)

        self.assertEqual({"score/1"}, registered)
        self.assertIn("malformed swift differential registry key: 'broken/'", errors)
        self.assertIn("malformed kotlin differential registry key: 'broken/'", errors)

    def test_malformed_translation_registry_key_is_an_explicit_error(self) -> None:
        self.write(
            parity_ratchet.SWIFT_RUNNER,
            'switch name {\ncase "score/1=raw/1": break\ncase "score/1=broken/": break\n}\n',
        )
        self.write(
            parity_ratchet.KOTLIN_RUNNER,
            'when (name) {\n"score/1=raw/1" -> Unit\n"score/1=broken/" -> Unit\n}\n',
        )

        registered, errors = parity_ratchet.registered_differential(self.root)

        self.assertEqual({"score/1=raw/1"}, registered)
        self.assertIn(
            "malformed swift differential registry key: 'score/1=broken/'", errors
        )
        self.assertIn(
            "malformed kotlin differential registry key: 'score/1=broken/'", errors
        )

    def test_cross_name_registry_resolves_each_language_side(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "struct Engine { func score(_ value: Int) -> Int { value } }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "class Engine { fun scoreRaw(value: Int): Int = value }\n",
        )
        inventories = {
            "swift": parity_ratchet.lex_file(self.root, swift, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }
        key = "Engine.score/1=Engine.scoreRaw/1"

        self.assertEqual({key}, parity_ratchet._resolved_differential(inventories, {key}))

    def test_cross_name_dispatchers_must_use_the_same_canonical_label(self) -> None:
        swift_key = "Engine.score/1=Engine.scoreRaw/1"
        kotlin_key = "Engine.score/1=Engine.other/1"
        self.write(
            parity_ratchet.SWIFT_RUNNER,
            f'switch name {{ case "{swift_key}": break; default: break }}\n',
        )
        self.write(
            parity_ratchet.KOTLIN_RUNNER,
            f'when (name) {{ "{kotlin_key}" -> Unit else -> Unit }}\n',
        )

        registered, errors = parity_ratchet.registered_differential(self.root)

        self.assertEqual(set(), registered)
        self.assertTrue(any("registrations disagree" in error for error in errors), errors)
        self.assertTrue(any(swift_key in error and kotlin_key in error for error in errors), errors)

    def test_alias_literal_in_dispatch_normalization_is_not_registered(self) -> None:
        canonical = "Engine.score/1"
        self.write(
            parity_ratchet.SWIFT_RUNNER,
            'let dispatchFunction = function == "legacyScore" ? "Engine.score/1" : function\n'
            'switch dispatchFunction { case "Engine.score/1": break; default: break }\n',
        )
        self.write(
            parity_ratchet.KOTLIN_RUNNER,
            'val dispatchFunction = if (function == "legacyScore") "Engine.score/1" else function\n'
            'when (dispatchFunction) { "Engine.score/1" -> Unit else -> Unit }\n',
        )

        registered, errors = parity_ratchet.registered_differential(self.root)

        self.assertEqual([], errors)
        self.assertEqual({canonical}, registered)
        self.assertNotIn("legacyScore", registered)

    def test_cross_name_registry_fails_closed_when_one_side_does_not_resolve(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "struct Engine { func score(_ value: Int) -> Int { value } }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "class Engine { fun other(value: Int): Int = value }\n",
        )
        inventories = {
            "swift": parity_ratchet.lex_file(self.root, swift, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }
        key = "Engine.score/1=Engine.scoreRaw/1"

        self.assertEqual(set(), parity_ratchet._resolved_differential(inventories, {key}))
        self.assertEqual(
            {"swift": 1, "kotlin": 0},
            parity_ratchet._differential_match_counts(inventories, key),
        )
        for relative, language in (
            ("Sources/Pilot/parity-exempt.json", "swift"),
            ("src/main/java/pilot/parity-exempt.json", "kotlin"),
        ):
            self.write(
                relative,
                json.dumps(
                    {
                        "schema_version": 1,
                        "module": "Pilot",
                        "exempt": [
                            {"key": item.key, "reason": "fixture", "issue": 17}
                            for item in inventories[language]
                        ],
                        "platform-test": [],
                    }
                ),
            )
        errors, _counts, _resolved = parity_ratchet._audit_module_pair(
            self.root,
            parity_ratchet.ModulePair(
                "Pilot", self.root / "Sources/Pilot", self.root / "src/main/java/pilot"
            ),
            registered={key},
        )
        self.assertTrue(any("swift=1 kotlin=0" in error for error in errors), errors)

    def test_owner_qualified_registry_resolves_name_arity_collision(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "struct First { func median(_ values: [Double]) -> Double { 1 } }\n"
            "struct Second { func median(_ values: [Double]) -> Double { 2 } }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "class First { fun median(values: List<Double>): Double = 1.0 }\n"
            "class Second { fun median(values: List<Double>): Double = 2.0 }\n",
        )
        inventories = {
            "swift": parity_ratchet.lex_file(self.root, swift, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }

        self.assertEqual(
            {"First.median/1"},
            parity_ratchet._resolved_differential(inventories, {"First.median/1"}),
        )
        self.assertEqual(set(), parity_ratchet._resolved_differential(inventories, {"median/1"}))

    def test_owner_qualified_registry_fails_closed_when_owner_is_ambiguous(self) -> None:
        swift_one = self.write(
            "Sources/Pilot/One.swift",
            "struct Engine { func score(_ value: Int) -> Int { value } }\n",
        )
        swift_two = self.write(
            "Sources/Pilot/Two.swift",
            "struct Engine { func score(_ value: Int) -> Int { value } }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "class Engine { fun score(value: Int): Int = value }\n",
        )
        inventories = {
            "swift": [
                *parity_ratchet.lex_file(self.root, swift_one, "swift"),
                *parity_ratchet.lex_file(self.root, swift_two, "swift"),
            ],
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }

        self.assertEqual(
            {"swift": 2, "kotlin": 1},
            parity_ratchet._differential_match_counts(inventories, "Engine.score/1"),
        )
        self.assertEqual(
            set(),
            parity_ratchet._resolved_differential(inventories, {"Engine.score/1"}),
        )
        for relative, language in (
            ("Sources/Pilot/parity-exempt.json", "swift"),
            ("src/main/java/pilot/parity-exempt.json", "kotlin"),
        ):
            self.write(
                relative,
                json.dumps(
                    {
                        "schema_version": 1,
                        "module": "Pilot",
                        "exempt": [
                            {"key": item.key, "reason": "fixture", "issue": 17}
                            for item in inventories[language]
                        ],
                        "platform-test": [],
                    }
                ),
            )
        errors, _counts, _resolved = parity_ratchet._audit_module_pair(
            self.root,
            parity_ratchet.ModulePair(
                "Pilot", self.root / "Sources/Pilot", self.root / "src/main/java/pilot"
            ),
            registered={"Engine.score/1"},
        )
        self.assertTrue(any("swift=2 kotlin=1" in error for error in errors), errors)

    def test_arity_registry_resolves_overloads_with_different_arities(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "func score(_ value: Int) -> Int { value }\n"
            "func score(_ left: Int, _ right: Int) -> Int { left + right }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "fun score(value: Int): Int = value\n"
            "fun score(left: Int, right: Int): Int = left + right\n",
        )
        inventories = {
            "swift": parity_ratchet.lex_file(self.root, swift, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }

        self.assertEqual({"score/1"}, parity_ratchet._resolved_differential(inventories, {"score/1"}))
        self.assertEqual(set(), parity_ratchet._resolved_differential(inventories, {"score"}))
        for relative, language in (
            ("Sources/Pilot/parity-exempt.json", "swift"),
            ("src/main/java/pilot/parity-exempt.json", "kotlin"),
        ):
            exempt = [
                {"key": item.key, "reason": "unregistered overload", "issue": 17}
                for item in inventories[language]
                if item.arity == 2
            ]
            self.write(
                relative,
                json.dumps(
                    {"schema_version": 1, "module": "Pilot", "exempt": exempt, "platform-test": []}
                ),
            )

        errors, counts, resolved = parity_ratchet._audit_module_pair(
            self.root,
            parity_ratchet.ModulePair(
                "Pilot", self.root / "Sources/Pilot", self.root / "src/main/java/pilot"
            ),
            registered={"score/1"},
        )

        self.assertEqual([], errors)
        self.assertEqual({"differential": 1, "platform-test": 0, "exempt": 2}, counts)
        self.assertEqual({"score/1"}, resolved)

    def test_arity_registry_fails_closed_for_same_arity_in_two_types(self) -> None:
        swift_dir = self.root / "Sources/Pilot"
        kotlin_dir = self.root / "src/main/java/pilot"
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "struct First { func score(_ value: Int) -> Int { value } }\n"
            "struct Second { func score(_ value: Int) -> Int { value } }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "class First { fun score(value: Int): Int = value }\n"
            "class Second { fun score(value: Int): Int = value }\n",
        )
        for directory, source, language in (
            (swift_dir, swift, "swift"),
            (kotlin_dir, kotlin, "kotlin"),
        ):
            exempt = [
                {"key": item.key, "reason": "ambiguous fixture", "issue": 17}
                for item in parity_ratchet.lex_file(self.root, source, language)
            ]
            self.write(
                str(directory.relative_to(self.root) / parity_ratchet.SHARD_NAME),
                json.dumps(
                    {"schema_version": 1, "module": "Pilot", "exempt": exempt, "platform-test": []}
                ),
            )
        self.write(parity_ratchet.SWIFT_RUNNER, 'switch name { case "score/1": break; default: break }\n')
        self.write(parity_ratchet.KOTLIN_RUNNER, 'when (name) { "score/1" -> Unit else -> Unit }\n')
        pair = parity_ratchet.ModulePair("Pilot", swift_dir, kotlin_dir)

        with mock.patch.object(parity_ratchet, "module_pairs", return_value=[pair]):
            errors, _counts = parity_ratchet.audit_inventory(self.root)

        self.assertTrue(any("score/1 resolves to swift=2 kotlin=2" in error for error in errors), errors)

    def test_coverage_uses_the_same_arity_qualified_registry_key(self) -> None:
        swift = self.write(
            "Sources/Pilot/Engine.swift",
            "func score(_ value: Int) -> Int { value }\n"
            "func score(_ left: Int, _ right: Int) -> Int { left + right }\n",
        )
        kotlin = self.write(
            "src/main/java/pilot/Engine.kt",
            "fun score(value: Int): Int = value\n"
            "fun score(left: Int, right: Int): Int = left + right\n",
        )
        swift_lcov = self.write(
            "arity.lcov",
            f"SF:{swift}\nDA:1,1\nDA:2,0\nend_of_record\n",
        )
        jacoco = self.write(
            "arity.xml",
            "<report><package name=\"pilot\"><sourcefile name=\"Engine.kt\">"
            "<line nr=\"1\" mi=\"0\" ci=\"1\"/><line nr=\"2\" mi=\"1\" ci=\"0\"/>"
            "</sourcefile></package></report>",
        )
        declarations = {
            "swift": parity_ratchet.lex_file(self.root, swift, "swift"),
            "kotlin": parity_ratchet.lex_file(self.root, kotlin, "kotlin"),
        }
        with contextlib.redirect_stdout(io.StringIO()):
            errors = parity_ratchet.coverage_errors(
                declarations,
                {"score/2"},
                swift_lcov=swift_lcov,
                kotlin_jacoco=jacoco,
            )

        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all(":2: registered declaration line was not executed" in error for error in errors))

    def write_two_pair_fixture(
        self,
        registered: str = "shared",
        *,
        declaring_pairs: tuple[str, ...] = ("First", "Second"),
        covered_pairs: tuple[str, ...] = ("First", "Second"),
        owner: str | None = None,
    ) -> tuple[list[parity_ratchet.ModulePair], Path, Path]:
        pairs = []
        swift_records = []
        kotlin_packages = []
        for ordinal, name in enumerate(("First", "Second"), 1):
            swift_dir = self.root / f"Sources/{name}"
            kotlin_dir = self.root / f"src/main/java/{name.lower()}"
            swift = self.write(
                f"Sources/{name}/{name}.swift",
                (
                    f"struct {owner} {{ func shared() -> Int {{ {ordinal} }} }}\n"
                    if owner is not None
                    else f"func shared() -> Int {{ {ordinal} }}\n"
                )
                if name in declaring_pairs
                else "",
            )
            self.write(
                f"src/main/java/{name.lower()}/{name}.kt",
                (
                    f"class {owner} {{ fun shared(): Int = {ordinal} }}\n"
                    if owner is not None
                    else f"fun shared(): Int = {ordinal}\n"
                )
                if name in declaring_pairs
                else "",
            )
            shard = json.dumps(
                {"schema_version": 1, "module": name, "exempt": [], "platform-test": []}
            )
            self.write(f"Sources/{name}/parity-exempt.json", shard)
            self.write(f"src/main/java/{name.lower()}/parity-exempt.json", shard)
            pairs.append(parity_ratchet.ModulePair(name, swift_dir, kotlin_dir))
            if name in covered_pairs:
                swift_records.append(
                    f"SF:{swift}\nFN:1,shared\nFNDA:1,shared\nDA:1,1\nend_of_record\n"
                )
                kotlin_packages.append(
                    f'<package name="{name.lower()}"><class name="{name.lower()}/{name}" sourcefilename="{name}.kt">'
                    '<method name="shared" desc="()I" line="1"><counter type="METHOD" missed="0" covered="1"/></method>'
                    f'</class><sourcefile name="{name}.kt"><line nr="1" mi="0" ci="1"/></sourcefile></package>'
                )
        self.write(
            parity_ratchet.SWIFT_RUNNER,
            f'switch name {{ case "{registered}": break; default: break }}\n',
        )
        self.write(
            parity_ratchet.KOTLIN_RUNNER,
            f'when (name) {{ "{registered}" -> Unit else -> Unit }}\n',
        )
        swift_lcov = self.write("swift.lcov", "".join(swift_records))
        jacoco = self.write("jacoco.xml", f'<report name="fixture">{"".join(kotlin_packages)}</report>')
        return pairs, swift_lcov, jacoco

    def test_ratchet_and_verify_accept_two_sharp_pairs_with_same_registered_name(self) -> None:
        pairs, swift_lcov, jacoco = self.write_two_pair_fixture()
        output = io.StringIO()
        with (
            mock.patch.object(parity_ratchet, "module_pairs", return_value=pairs),
            mock.patch.object(parity_ratchet, "resolve_base", return_value="base"),
            mock.patch.object(parity_ratchet, "_base_shards", return_value=set()),
            mock.patch.object(parity_ratchet, "compare_ratchet", return_value=[]),
            mock.patch.dict("os.environ", {"CI": ""}, clear=False),
            contextlib.redirect_stdout(output),
        ):
            ratchet_code = parity_ratchet.main(["--root", str(self.root), "ratchet", "--offline"])
            verify_code = parity_ratchet.main(
                [
                    "--root", str(self.root),
                    "verify",
                    "--swift-lcov", str(swift_lcov),
                    "--kotlin-jacoco", str(jacoco),
                ]
            )

        self.assertEqual(0, ratchet_code)
        self.assertEqual(0, verify_code)
        self.assertEqual(2, output.getvalue().count("differential=1"), output.getvalue())

    def test_owner_qualified_registry_rejects_resolution_in_two_sharp_pairs(self) -> None:
        key = "Engine.shared/0"
        pairs, _swift_lcov, _jacoco = self.write_two_pair_fixture(
            registered=key,
            owner="Engine",
        )
        with mock.patch.object(parity_ratchet, "module_pairs", return_value=pairs):
            errors, counts = parity_ratchet.audit_inventory(self.root)

        self.assertEqual(1, counts["differential"])
        self.assertIn(
            f"owner-qualified swift side {key} resolves to 2 declarations across sharp module pairs",
            errors,
        )
        self.assertIn(
            f"owner-qualified kotlin side {key} resolves to 2 declarations across sharp module pairs",
            errors,
        )

    def test_registered_name_resolving_only_in_one_pair_does_not_fail_other_pair(self) -> None:
        pairs, swift_lcov, jacoco = self.write_two_pair_fixture(
            declaring_pairs=("First",),
            covered_pairs=("First",),
        )
        with mock.patch.object(parity_ratchet, "module_pairs", return_value=pairs):
            errors, _counts = parity_ratchet.verify_coverage(self.root, swift_lcov, jacoco)

        self.assertEqual([], errors)

    def test_verify_requires_coverage_for_every_pair_resolving_registered_name(self) -> None:
        pairs, swift_lcov, jacoco = self.write_two_pair_fixture(covered_pairs=("First",))
        with mock.patch.object(parity_ratchet, "module_pairs", return_value=pairs):
            errors, _counts = parity_ratchet.verify_coverage(self.root, swift_lcov, jacoco)

        self.assertTrue(any("Sources/Second/Second.swift" in error for error in errors), errors)
        self.assertTrue(any("src/main/java/second/Second.kt" in error for error in errors), errors)

    def test_registered_name_missing_from_every_sharp_pair_is_rejected_once(self) -> None:
        pairs, _swift_lcov, _jacoco = self.write_two_pair_fixture(registered="missing")
        with mock.patch.object(parity_ratchet, "module_pairs", return_value=pairs):
            errors, counts = parity_ratchet.audit_inventory(self.root)

        unresolved = [error for error in errors if "missing" in error and "sharp module pair" in error]
        self.assertEqual(1, len(unresolved), errors)
        self.assertEqual(1, counts["differential"])

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
        existing_name_only = {
                "beatAccurateFraction",
                "beatSpreadIsTrustworthy",
                "beatValuesAreTrustworthy",
                "classifyCoverage",
                "cleanRR",
                "cleanRRGapAware",
                "collapseOverCount",
                "collapsedCoverage",
                "densestSecondWindowSample",
                "duplicateBeatCount",
                "pnn50GapAware",
                "rangeFilter",
                "rejectEctopic",
                "rmssdGapAware",
                "rmssdRaw",
                "rollingRmssd",
                "rrCoverage",
                "sdnnRaw",
            }
        self.assertEqual(existing_name_only, {key for key in registered if "/" not in key})
        legacy = existing_name_only | {"analyze/3"}
        added = {
            "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2",
            "HRVAnalyzer.median/1=HrvAnalyzer.median/1",
        }
        qualified_strain = {"StrainScorer.trimpToStrain/2"}
        strain = {
            "StrainScorer.banisterTRIMP/5",
            "StrainScorer.defaultMaxHR/1",
            "StrainScorer.edwardsTRIMP/4",
            "StrainScorer.effectiveEffort/2",
            "StrainScorer.estimateHRmax/2",
            "StrainScorer.fitStrainDenominator/1",
            "StrainScorer.pctHRR/3",
            "StrainScorer.percentile/2",
            "StrainScorer.sampleDurationMinutes/1",
            "StrainScorer.sampleDurationsMinutes/1",
            "StrainScorer.strain/6",
            "StrainScorer.tanakaHRmax/1",
            "StrainScorer.zoneWeight/3",
        }
        expected = legacy | added | qualified_strain | strain
        self.assertEqual(19, len(legacy))
        self.assertEqual(21, len(legacy | added))
        self.assertEqual(35, len(expected))
        self.assertEqual(expected, registered)
        self.assertIn("StrainScorer.trimpToStrain/2", registered)
        self.assertNotIn("trimpToStrain", registered)
        pair = next(
            pair for pair in parity_ratchet.module_pairs(REPOSITORY)
            if pair.name == "StrandAnalytics<->analytics"
        )
        inventories = {
            "swift": parity_ratchet.lex_tree(REPOSITORY, pair.swift_dir, "swift"),
            "kotlin": parity_ratchet.lex_tree(REPOSITORY, pair.kotlin_dir, "kotlin"),
        }
        for key in registered:
            self.assertEqual({"swift": 1, "kotlin": 1}, parity_ratchet._differential_match_counts(inventories, key), key)
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
            "Tools/parity_*.json",
            "Tools/parity_cases/**",
            "Tools/tests/**",
            "**/parity-exempt.json",
            "android/app/build.gradle.kts",
            "android/gradle/verification-metadata.xml",
            ".github/workflows/parity-ratchet.yml",
        ):
            self.assertIn(pattern, workflow)

    def test_coverage_workflow_derives_llvm_cov_from_swift_toolchain(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/parity-ratchet.yml").read_text()
        self.assertNotIn("/opt/", workflow)
        self.assertIn('swift_bin="$(readlink -f "$(command -v swift)")"', workflow)
        self.assertIn('test -x "$(dirname "$swift_bin")/llvm-cov"', workflow)
        self.assertIn('"$(dirname "$swift_bin")/llvm-cov" export', workflow)

    def test_claude_linux_wall_documents_strand_analytics_snapshot_support(self) -> None:
        guidance = (REPOSITORY / "CLAUDE.md").read_text()
        self.assertIn("`StrandAnalytics` builds and its full test suite runs", guidance)
        self.assertIn("[`docs/LINUX.md`](docs/LINUX.md)", guidance)
        self.assertNotIn("`StrandAnalytics` (via `WhoopStore`)", guidance)

    def test_new_baseline_finding_without_issue_remains_rejected(self) -> None:
        baseline = self.write(
            parity_ratchet.LEDGER_BASELINE,
            json.dumps(
                {
                    "schema_version": 2,
                    "findings": [{"identity": "new-finding"}],
                    "counters": {},
                }
            ),
        )
        self.assertTrue(baseline.exists())
        with (
            mock.patch.object(parity_ratchet, "_base_shards", return_value=set()),
            mock.patch.object(
                parity_ratchet,
                "_base_json",
                return_value={"schema_version": 2, "findings": [], "counters": {}},
            ),
        ):
            errors = parity_ratchet.compare_ratchet(self.root, "base", offline=True)

        self.assertEqual(
            ["Tools/parity_ledger_baseline.json: new finding new-finding needs issue"],
            errors,
        )


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
