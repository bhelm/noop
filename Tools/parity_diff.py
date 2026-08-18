#!/usr/bin/env python3
"""Generate parity inputs and compare Swift/Kotlin JSON Lines outputs.

The runners are deliberately not launched here. A complete run is strictly serial:

  python3 Tools/parity_diff.py generate --output /tmp/parity-input.jsonl
  PARITY_INPUT=/tmp/parity-input.jsonl PARITY_OUTPUT=/tmp/parity-swift.jsonl \
    PATH=/opt/swift/usr/bin:$PATH LD_LIBRARY_PATH=/opt/sqlite-snapshot \
    swift test --package-path Packages/StrandAnalytics --filter ParityRunner \
      -Xlinker -L/opt/sqlite-snapshot
  PARITY_INPUT=/tmp/parity-input.jsonl PARITY_OUTPUT=/tmp/parity-kotlin.jsonl \
    ./android/gradlew -p android testFullDebugUnitTest \
      --tests com.noop.analytics.ParityRunner
  python3 Tools/parity_diff.py compare --input /tmp/parity-input.jsonl \
    --swift /tmp/parity-swift.jsonl --kotlin /tmp/parity-kotlin.jsonl

For a negative-side proof, generate ``--suite negative`` and set
``PARITY_NEGATIVE_SIDE=swift`` (then ``kotlin``) on both runner commands. Only
the named runner mutates the declared ``trimpToStrain`` probe.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Iterable


GENERATOR_SEED = 0xD04F_5A17_9E37_79B9
ROLLING_DEFAULT_WINDOW_SEC = 300
ROLLING_DEFAULT_STEP_SEC = 0
ROLLING_DEFAULT_MIN_BEATS = 8
STRAIN_DEFAULT_DENOMINATOR = 7201.0
EPSILON = 1e-9
_MASK64 = (1 << 64) - 1
_BITS_RE = re.compile(r"^[0-9a-f]{16}$")


class ParityFormatError(ValueError):
    """The input/output contract is malformed or stale."""


class SplitMix64:
    """Small deterministic generator shared by every generated pilot input."""

    def __init__(self, seed: int):
        self.state = seed & _MASK64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
        return (value ^ (value >> 31)) & _MASK64

    def bounded(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper must be positive")
        return self.next_u64() % upper


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ParityFormatError(f"value is not finite canonical JSON: {exc}") from exc


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ParityFormatError(f"cannot read {label} file {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ParityFormatError(f"{label} line {line_number} is blank")
        try:
            record = json.loads(line, parse_constant=lambda token: (_raise_non_finite(token)))
        except (json.JSONDecodeError, ParityFormatError) as exc:
            raise ParityFormatError(f"invalid {label} JSON on line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ParityFormatError(f"{label} line {line_number} must be an object")
        records.append(record)
    if not records:
        raise ParityFormatError(f"{label} file is empty")
    return records


def _raise_non_finite(token: str) -> None:
    raise ParityFormatError(f"number must be finite, got {token}")


def _index(records: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        case_id = record.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ParityFormatError(f"{label} record has no non-empty string id")
        if case_id in indexed:
            raise ParityFormatError(f"duplicate {label} id: {case_id}")
        indexed[case_id] = record
    return indexed


def _format_id_set_mismatch(expected: set[str], actual: set[str], label: str) -> str:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return f"ID set mismatch for {label}: missing={missing} extra={extra}"


def _validate_record_contract(
    expected: dict[str, Any], actual: dict[str, Any], side: str
) -> None:
    case_id = expected["id"]
    if actual.get("nonce") != expected.get("nonce"):
        raise ParityFormatError(
            f"nonce mismatch for {side} id={case_id}: "
            f"expected={expected.get('nonce')!r} actual={actual.get('nonce')!r}"
        )
    for field in ("function", "comparison"):
        if actual.get(field) != expected.get(field):
            raise ParityFormatError(
                f"{field} mismatch for {side} id={case_id}: "
                f"expected={expected.get(field)!r} actual={actual.get(field)!r}"
            )
    negative_side = actual.get("negativeSide")
    if negative_side is not None and negative_side != side:
        raise ParityFormatError(
            f"negativeSide mismatch for {side} id={case_id}: {negative_side!r}"
        )


def _payload(record: dict[str, Any], comparison: str, side: str, case_id: str) -> tuple[str, Any]:
    has_error = "error" in record
    value_field = "valueBits" if comparison == "exact" else "value"
    has_value = value_field in record
    if has_error == has_value:
        raise ParityFormatError(
            f"{side} id={case_id} must contain exactly one of error/{value_field}"
        )
    if has_error:
        error = record["error"]
        if not isinstance(error, str) or not error:
            raise ParityFormatError(f"{side} id={case_id} error must be a non-empty string")
        return ("error", error)
    value = record[value_field]
    if comparison == "exact":
        if not isinstance(value, str) or _BITS_RE.fullmatch(value) is None:
            raise ParityFormatError(
                f"{side} id={case_id} valueBits must be 16 lowercase hex digits"
            )
    else:
        _validate_finite_tree(value, f"{side} id={case_id} value")
    return ("value", value)


def _validate_finite_tree(value: Any, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ParityFormatError(f"{path} number must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ParityFormatError(f"{path} object key must be a string")
            _validate_finite_tree(item, f"{path}.{key}")
        return
    raise ParityFormatError(f"{path} contains unsupported {type(value).__name__}")


def _epsilon_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=EPSILON, abs_tol=EPSILON)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _epsilon_equal(l_item, r_item) for l_item, r_item in zip(left, right)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _epsilon_equal(left[key], right[key]) for key in left
        )
    return left == right


def _render_payload(kind: str, value: Any, comparison: str) -> str:
    if kind == "error":
        return f"error:{_canonical_json(value)}"
    if comparison == "exact":
        return f"bits:{value}"
    return _canonical_json(value)


def compare_files(input_path: Path, swift_path: Path, kotlin_path: Path) -> list[str]:
    """Validate all contracts and return one stable diff line per differing case."""

    inputs = _index(_read_jsonl(Path(input_path), "input"), "input")
    swift = _index(_read_jsonl(Path(swift_path), "swift output"), "swift output")
    kotlin = _index(_read_jsonl(Path(kotlin_path), "kotlin output"), "kotlin output")
    expected_ids = set(inputs)
    if set(swift) != expected_ids:
        raise ParityFormatError(_format_id_set_mismatch(expected_ids, set(swift), "swift"))
    if set(kotlin) != expected_ids:
        raise ParityFormatError(_format_id_set_mismatch(expected_ids, set(kotlin), "kotlin"))

    diffs: list[str] = []
    for case_id in sorted(expected_ids):
        expected = inputs[case_id]
        nonce = expected.get("nonce")
        comparison = expected.get("comparison")
        function = expected.get("function")
        if not isinstance(nonce, str) or not nonce:
            raise ParityFormatError(f"input id={case_id} has no non-empty string nonce")
        if comparison not in {"exact", "epsilon"}:
            raise ParityFormatError(f"input id={case_id} has invalid comparison {comparison!r}")
        if not isinstance(function, str) or not function:
            raise ParityFormatError(f"input id={case_id} has no non-empty string function")
        _validate_record_contract(expected, swift[case_id], "swift")
        _validate_record_contract(expected, kotlin[case_id], "kotlin")
        swift_kind, swift_value = _payload(swift[case_id], comparison, "swift", case_id)
        kotlin_kind, kotlin_value = _payload(kotlin[case_id], comparison, "kotlin", case_id)
        equal = swift_kind == kotlin_kind
        if equal and swift_kind == "value" and comparison == "epsilon":
            equal = _epsilon_equal(swift_value, kotlin_value)
        elif equal:
            equal = swift_value == kotlin_value
        if equal:
            continue
        annotations = {
            value
            for value in (swift[case_id].get("negativeSide"), kotlin[case_id].get("negativeSide"))
            if value is not None
        }
        suffix = ""
        if annotations:
            if len(annotations) != 1:
                raise ParityFormatError(f"conflicting negativeSide annotations for id={case_id}")
            suffix = f" negative_side={next(iter(annotations))}"
        diffs.append(
            f"DIFF id={case_id} function={function} class={comparison}{suffix} "
            f"swift={_render_payload(swift_kind, swift_value, comparison)} "
            f"kotlin={_render_payload(kotlin_kind, kotlin_value, comparison)}"
        )
    return diffs


def _curated_cases() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent / "parity_cases"
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ParityFormatError(f"cannot load curated cases {path}: {exc}") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ParityFormatError(f"curated file {path} must contain an array of objects")
        for item in payload:
            record = dict(item)
            record["source"] = f"curated:{path.name}"
            records.append(record)
    if not records:
        raise ParityFormatError(f"no curated JSON cases found under {root}")
    return records


def _seeded_cases() -> list[dict[str, Any]]:
    rng = SplitMix64(GENERATOR_SEED)
    records: list[dict[str, Any]] = []
    for index, explicit_window in enumerate((False, True, False, True)):
        rr = []
        base = 10_000 + index * 1_000
        for beat in range(18 + index):
            rr.append({"rrMs": 790 + rng.bounded(21), "ts": base + beat})
        args: dict[str, Any] = {"rr": rr}
        if explicit_window:
            args["windowSec"] = 60
        records.append(
            {
                "args": args,
                "comparison": "epsilon",
                "function": "rollingRmssd",
                "id": f"seeded_rmssd_{'explicit' if explicit_window else 'default'}_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
    for index in range(4):
        trimp = float(1 + rng.bounded(7_000))
        args = {"trimp": trimp}
        if index % 2:
            args["denominator"] = STRAIN_DEFAULT_DENOMINATOR
        records.append(
            {
                "args": args,
                "comparison": "exact",
                "function": "trimpToStrain",
                "id": f"seeded_trimp_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
    return records


def _effective_args(record: dict[str, Any]) -> dict[str, Any]:
    args = record.get("args")
    if not isinstance(args, dict):
        raise ParityFormatError(f"case {record.get('id')!r} args must be an object")
    function = record.get("function")
    if function == "rollingRmssd":
        if not isinstance(args.get("rr"), list):
            raise ParityFormatError(f"case {record.get('id')!r} rollingRmssd requires args.rr")
        return {
            "minBeatsPerWindow": args.get("minBeatsPerWindow", ROLLING_DEFAULT_MIN_BEATS),
            "rr": args["rr"],
            "stepSec": args.get("stepSec", ROLLING_DEFAULT_STEP_SEC),
            "windowSec": args.get("windowSec", ROLLING_DEFAULT_WINDOW_SEC),
        }
    if function == "trimpToStrain":
        if not isinstance(args.get("trimp"), (int, float)):
            raise ParityFormatError(f"case {record.get('id')!r} trimpToStrain requires args.trimp")
        return {
            "denominator": args.get("denominator", STRAIN_DEFAULT_DENOMINATOR),
            "trimp": args["trimp"],
        }
    raise ParityFormatError(f"case {record.get('id')!r} has unsupported function {function!r}")


def generate_cases(suite: str, nonce: str) -> list[dict[str, Any]]:
    if not nonce:
        raise ParityFormatError("nonce must not be empty")
    if suite == "negative":
        raw = [
            {
                "args": {"trimp": 100.0},
                "comparison": "exact",
                "function": "trimpToStrain",
                "id": "trimp_negative_probe",
                "source": "negative-control",
            }
        ]
    else:
        raw = _curated_cases() + _seeded_cases()
    seen: set[str] = set()
    generated: list[dict[str, Any]] = []
    for item in raw:
        record = dict(item)
        case_id = record.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ParityFormatError("every generated case needs a non-empty string id")
        if case_id in seen:
            raise ParityFormatError(f"duplicate generated id: {case_id}")
        seen.add(case_id)
        record["effectiveArgs"] = _effective_args(record)
        record["nonce"] = nonce
        generated.append(record)
    return sorted(generated, key=lambda record: record["id"])


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    materialized = list(records)
    data = "".join(_canonical_json(record) + "\n" for record in materialized)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    except OSError as exc:
        raise ParityFormatError(f"cannot write input file {path}: {exc}") from exc
    return len(materialized)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="write nonce-stamped canonical input JSON Lines")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--suite", choices=("pilot", "negative"), default="pilot")
    generate.add_argument("--nonce", help="fixed nonce for tests; normally a random nonce is generated")
    compare = subparsers.add_parser("compare", help="validate and compare two runner output files")
    compare.add_argument("--input", type=Path, required=True)
    compare.add_argument("--swift", type=Path, required=True)
    compare.add_argument("--kotlin", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            nonce = args.nonce or secrets.token_hex(16)
            cases = generate_cases(args.suite, nonce)
            count = _write_jsonl(args.output, cases)
            print(f"GENERATED suite={args.suite} cases={count} nonce={nonce} output={args.output}")
            return 0
        diffs = compare_files(args.input, args.swift, args.kotlin)
        inputs = _index(_read_jsonl(args.input, "input"), "input")
        for line in diffs:
            print(line)
        by_function: dict[str, tuple[int, int]] = {}
        diff_ids = {line.split(" id=", 1)[1].split(" ", 1)[0] for line in diffs}
        for case_id, record in inputs.items():
            function = record["function"]
            total, different = by_function.get(function, (0, 0))
            by_function[function] = (total + 1, different + int(case_id in diff_ids))
        for function in sorted(by_function):
            total, different = by_function[function]
            status = "OK" if different == 0 else "RESULT"
            print(f"{status} function={function} cases={total} diffs={different}")
        print(f"SUMMARY cases={len(inputs)} diffs={len(diffs)}")
        return 1 if diffs else 0
    except ParityFormatError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
