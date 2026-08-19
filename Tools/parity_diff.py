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
STRAIN_DEFAULT_AGE = 30
STRAIN_DEFAULT_MAX_HR = 190.0
STRAIN_DEFAULT_RESTING_HR = 60.0
COLLAPSE_DEFAULT_RR_TOL_MS = 40.0
COLLAPSE_DEFAULT_WINDOW_SEC = 0
COLLAPSED_COVERAGE_DEFAULT_RR_TOL_MS = 30.0
DENSEST_DEFAULT_HALF_WINDOW_SEC = 3
DENSEST_DEFAULT_MAX_ROWS_PER_SECOND = 24
RECOVERY_DEFAULT_HRV_BASELINE_USABLE = True
RECOVERY_TRACE_KEY = (
    "RecoveryScorer.recoveryTrace/8=RecoveryScorerTrace.recoveryTrace/8"
)
RAW_ANALYZE_KEY = "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2"
HRV_MEDIAN_KEY = "HRVAnalyzer.median/1=HrvAnalyzer.median/1"
EPSILON = 1e-9
_MASK64 = (1 << 64) - 1
_BITS_RE = re.compile(r"^[0-9a-f]{16}$")
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1


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
        _validate_exact_tree(value, f"{side} id={case_id} valueBits")
    else:
        _validate_finite_tree(value, f"{side} id={case_id} value")
    return ("value", value)


def _validate_exact_tree(value: Any, path: str) -> None:
    """Validate an exact payload: literals plus IEEE-754 bit strings for doubles."""

    if isinstance(value, bool) or value is None or isinstance(value, int):
        return
    if isinstance(value, str):
        if _BITS_RE.fullmatch(value) is None:
            raise ParityFormatError(f"{path} must be 16 lowercase hex digits")
        return
    if isinstance(value, float):
        raise ParityFormatError(f"{path} floating value must be encoded as 16 lowercase hex digits")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_exact_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        if value.keys() == {"text"}:
            if not isinstance(value["text"], str):
                raise ParityFormatError(f"{path}.text must be a string")
            return
        for key, item in value.items():
            if not isinstance(key, str):
                raise ParityFormatError(f"{path} object key must be a string")
            _validate_exact_tree(item, f"{path}.{key}")
        return
    raise ParityFormatError(f"{path} contains unsupported {type(value).__name__}")


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


def _hrv_curated_cases() -> list[dict[str, Any]]:
    """Contract edges for every uniquely dispatchable pure HRV twin."""

    cases: list[dict[str, Any]] = []

    def add(case_id: str, function: str, comparison: str, args: dict[str, Any]) -> None:
        cases.append(
            {
                "args": args,
                "comparison": comparison,
                "function": function,
                "id": case_id,
                "source": "curated:hrv",
            }
        )

    for function in ("rmssdRaw", "sdnnRaw"):
        add(f"hrv_{function}_empty", function, "epsilon", {"nn": []})
        add(f"hrv_{function}_singleton", function, "epsilon", {"nn": [800.0]})
        add(f"hrv_{function}_pair", function, "epsilon", {"nn": [800.0, 850.0]})

    for count in (19, 20):
        add(
            f"hrv_analyze_min_beats_{count}",
            "analyze/3",
            "epsilon",
            {
                "rr": [
                    {"rrMs": 800 + (beat % 2) * 10, "ts": 1_000 + beat}
                    for beat in range(count)
                ]
            },
        )

    add("hrv_analyze_raw_empty", RAW_ANALYZE_KEY, "epsilon", {"rawRR": []})
    add(
        "hrv_analyze_raw_under_min",
        RAW_ANALYZE_KEY,
        "epsilon",
        {"rawRR": [800.0 + (beat % 2) * 10.0 for beat in range(19)]},
    )
    add(
        "hrv_analyze_raw_clean",
        RAW_ANALYZE_KEY,
        "epsilon",
        {"rawRR": [790.0 + (beat % 3) * 10.0 for beat in range(20)]},
    )

    for label, values in (
        ("empty", []),
        ("singleton", [812.0]),
        ("even", [900.0, 700.0, 800.0, 600.0]),
        ("odd", [900.0, 700.0, 800.0]),
        ("duplicates", [850.0, 700.0, 850.0, 900.0, 850.0]),
    ):
        add(f"hrv_median_{label}", HRV_MEDIAN_KEY, "exact", {"values": values})

    for function in ("rangeFilter", "rejectEctopic", "cleanRR"):
        add(f"hrv_{function}_empty", function, "exact", {"values": []})
    add("hrv_rangeFilter_bounds", "rangeFilter", "exact", {"values": [299.0, 300.0, 2000.0, 2001.0]})
    add("hrv_rejectEctopic_singleton", "rejectEctopic", "exact", {"values": [800.0]})
    add("hrv_rejectEctopic_threshold", "rejectEctopic", "exact", {"values": [800.0, 960.0, 800.0]})
    add("hrv_cleanRR_range_and_ectopic", "cleanRR", "exact", {"values": [299.0, 800.0, 1200.0, 800.0, 2001.0]})

    add("hrv_cleanRRGapAware_empty", "cleanRRGapAware", "exact", {"values": []})
    add("hrv_cleanRRGapAware_singleton", "cleanRRGapAware", "exact", {"values": [800.0]})
    add("hrv_cleanRRGapAware_splice", "cleanRRGapAware", "exact", {"values": [800.0, 250.0, 820.0]})

    for function in ("rmssdGapAware", "pnn50GapAware"):
        add(f"hrv_{function}_empty", function, "epsilon", {"contiguous": [], "nn": []})
        add(
            f"hrv_{function}_no_pairs",
            function,
            "epsilon",
            {"contiguous": [False, False], "nn": [800.0, 900.0]},
        )
        add(
            f"hrv_{function}_mixed_pairs",
            function,
            "epsilon",
            {"contiguous": [False, True, False, True], "nn": [800.0, 850.0, 700.0, 751.0]},
        )

    for verdict in (
        "plausible",
        "underCovered",
        "sameSecondOverCount",
        "crossSecondOverCount",
        "unmeasurable",
    ):
        add(f"hrv_beatSpread_{verdict}", "beatSpreadIsTrustworthy", "exact", {"verdict": verdict})

    add("hrv_beatAccurate_empty", "beatAccurateFraction", "epsilon", {"rrMs": [], "tsSec": []})
    add("hrv_beatAccurate_mismatched", "beatAccurateFraction", "epsilon", {"rrMs": [800.0], "tsSec": [0, 1]})
    add("hrv_beatAccurate_duplicate_ts", "beatAccurateFraction", "epsilon", {"rrMs": [800.0, 800.0], "tsSec": [5, 5]})
    add("hrv_beatAccurate_boundary", "beatAccurateFraction", "epsilon", {"rrMs": [500.0, 500.0], "tsSec": [0, 1]})

    for label, fraction in (("below", 0.499), ("boundary", 0.5), ("above", 1.0)):
        add(f"hrv_beatValues_{label}", "beatValuesAreTrustworthy", "exact", {"fraction": fraction})

    for label, coverage, collapsed in (
        ("zero", 0.0, 0.0),
        ("under", 0.8, 0.8),
        ("floor", 0.9, 0.9),
        ("ceiling", 1.1, 1.1),
        ("same_second", 1.2, 1.0),
        ("cross_second", 1.2, 1.2),
    ):
        add(
            f"hrv_classify_{label}",
            "classifyCoverage",
            "exact",
            {"collapsed": collapsed, "coverage": coverage},
        )

    for function in ("rrCoverage", "duplicateBeatCount"):
        comparison = "epsilon" if function == "rrCoverage" else "exact"
        add(f"hrv_{function}_empty", function, comparison, {"rrMs": [], "tsSec": []})
        add(f"hrv_{function}_singleton", function, comparison, {"rrMs": [800.0], "tsSec": [10]})
        add(
            f"hrv_{function}_duplicate_ts",
            function,
            comparison,
            {"rrMs": [800.0, 800.0, 810.0], "tsSec": [10, 10, 11]},
        )

    add("hrv_collapse_empty", "collapseOverCount", "exact", {"rrMs": [], "tsSec": []})
    add(
        "hrv_collapse_defaults",
        "collapseOverCount",
        "exact",
        {"rrMs": [800.0, 820.0, 900.0], "tsSec": [10, 10, 11]},
    )
    add(
        "hrv_collapse_cross_second",
        "collapseOverCount",
        "exact",
        {"rrMs": [800.0, 820.0, 900.0], "rrTolMs": 40.0, "tsSec": [10, 11, 12], "windowSec": 1},
    )

    add("hrv_collapsedCoverage_empty", "collapsedCoverage", "epsilon", {"rrMs": [], "tsSec": []})
    add(
        "hrv_collapsedCoverage_defaults",
        "collapsedCoverage",
        "epsilon",
        {"rrMs": [800.0, 820.0, 900.0], "tsSec": [10, 10, 11]},
    )
    add(
        "hrv_collapsedCoverage_tolerance",
        "collapsedCoverage",
        "epsilon",
        {"rrMs": [800.0, 840.0, 900.0], "rrTolMs": 50.0, "tsSec": [10, 10, 11]},
    )

    add("hrv_densest_empty", "densestSecondWindowSample", "exact", {"rrMs": [], "srcCodes": [], "tsSec": []})
    add(
        "hrv_densest_singleton",
        "densestSecondWindowSample",
        "exact",
        {"rrMs": [800.0], "srcCodes": [None], "tsSec": [10]},
    )
    add(
        "hrv_densest_duplicate_timestamps",
        "densestSecondWindowSample",
        "exact",
        {"rrMs": [820.0, 800.0, 810.0], "srcCodes": [2, None, 1], "tsSec": [10, 10, 11]},
    )
    add(
        "hrv_densest_truncation",
        "densestSecondWindowSample",
        "exact",
        {"halfWindowSec": 0, "maxRowsPerSecond": 2, "rrMs": [800.0, 810.0, 820.0], "srcCodes": [], "tsSec": [10, 10, 10]},
    )
    return cases


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
    return records + _hrv_curated_cases()


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
                "function": "StrainScorer.trimpToStrain/2",
                "id": f"seeded_trimp_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
    for round_index in range(2):
        percentile_values = sorted(80.0 + rng.bounded(101) for _ in range(6 + round_index))
        base = 100 + round_index * 1_000
        strain_a = {
            "series": {"count": 20, "startTs": base, "stepSec": 32, "bpm": 120 + round_index * 10},
            "useDefaults": False,
            "maxHR": 190.0,
            "restingHR": 60.0,
            "method": "edwards",
            "sex": "male",
            "denominator": 7201.0,
        }
        strain_b = dict(strain_a)
        strain_b["method"] = "banister"
        strain_b["sex"] = "female"
        strain_cases = [
            ("StrainScorer.tanakaHRmax/1", "epsilon", {"age": 18.0 + rng.bounded(63)}),
            ("StrainScorer.defaultMaxHR/1", "exact", {} if round_index == 0 else {"ageInt": 18 + rng.bounded(63)}),
            ("StrainScorer.percentile/2", "epsilon", {"values": percentile_values, "pct": float(rng.bounded(101))}),
            ("StrainScorer.estimateHRmax/2", "epsilon", {"history": {"count": 599 + round_index, "low": 150.0, "high": 180.0 + rng.bounded(31)}, "age": 25.0 + rng.bounded(36)}),
            ("StrainScorer.pctHRR/3", "epsilon", {"bpm": 50.0 + rng.bounded(151), "restingHR": 60.0, "hrReserve": 120.0}),
            ("StrainScorer.zoneWeight/3", "exact", {"bpm": 50.0 + rng.bounded(151), "restingHR": 60.0, "hrReserve": 120.0}),
            ("StrainScorer.effectiveEffort/2", "exact", {"live": rng.bounded(10_001) / 100.0, "stored": rng.bounded(10_001) / 100.0}),
            ("StrainScorer.sampleDurationMinutes/1", "epsilon", {"hr": [{"ts": base, "bpm": 100}, {"ts": base + 1 + rng.bounded(180), "bpm": 110}]}),
            ("StrainScorer.sampleDurationsMinutes/1", "epsilon", {"hr": [{"ts": base, "bpm": 100}, {"ts": base + 1, "bpm": 110}, {"ts": base + 2 + rng.bounded(180), "bpm": 120}]}),
            ("StrainScorer.edwardsTRIMP/4", "epsilon", {"hr": [{"ts": 0, "bpm": 90 + rng.bounded(91)}, {"ts": 30, "bpm": 90 + rng.bounded(91)}], "restingHR": 60.0, "hrReserve": 120.0, "durations": [0.25 + round_index * 0.25, 0.5]}),
            ("StrainScorer.banisterTRIMP/5", "epsilon", {"hr": [{"ts": 0, "bpm": 90 + rng.bounded(91)}, {"ts": 30, "bpm": 90 + rng.bounded(91)}], "restingHR": 60.0, "hrReserve": 120.0, "durations": [0.25 + round_index * 0.25, 0.5], "b": 1.67 if round_index == 0 else 1.92}),
            ("StrainScorer.fitStrainDenominator/1", "epsilon", {"pairs": [[10.0, 20.0 + rng.bounded(10)], [100.0, 45.0 + rng.bounded(10)], [500.0, 65.0 + rng.bounded(10)]]}),
            (
                "StrainScorer.strain/6",
                "exact",
                {
                    "replayFirstAtEnd": round_index == 0,
                    "strainCalls": [strain_a, strain_b, strain_a] if round_index == 0 else [
                        {"series": {"count": 600, "startTs": base, "stepSec": 1, "bpm": 125, "alternateBpm": 145}, "useDefaults": True}
                    ],
                },
            ),
        ]
        for index, (function, comparison, args) in enumerate(strain_cases):
            record = {
                "args": args,
                "comparison": comparison,
                "function": function,
                "id": f"seeded_strain_{round_index}_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
            if function == "StrainScorer.sampleDurationsMinutes/1":
                record["knownBehaviorIssue"] = "bhelm/noop#12"
            records.append(record)
    verdicts = ["plausible", "underCovered", "sameSecondOverCount"]
    for index in range(2):
        raw = [float(760 + rng.bounded(81)) for _ in range(20 + index)]
        records.append(
            {
                "args": {"rawRR": raw},
                "comparison": "epsilon",
                "function": RAW_ANALYZE_KEY,
                "id": f"seeded_hrv_analyze_raw_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
        records.append(
            {
                "args": {"values": raw[: 5 + index]},
                "comparison": "exact",
                "function": HRV_MEDIAN_KEY,
                "id": f"seeded_hrv_median_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
        records.append(
            {
                "args": {
                    "rr": [
                        {
                            "rrMs": 780 + rng.bounded(41),
                            "ts": 30_000 + index * 100 + beat,
                        }
                        for beat in range(21 + index)
                    ]
                },
                "comparison": "epsilon",
                "function": "analyze/3",
                "id": f"seeded_hrv_analyze_3_{index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
    for index in range(3):
        nn = [float(700 + rng.bounded(301)) for _ in range(4 + index)]
        rr = nn[:]
        rr[index] = [299.0, 300.0, 2001.0][index]
        contiguous = [False] + [rng.bounded(2) == 1 for _ in range(len(nn) - 1)]
        ts = [20_000 + index + beat // 2 for beat in range(len(nn))]
        src = [None if rng.bounded(3) == 0 else int(rng.bounded(4)) for _ in nn]

        def add(function: str, comparison: str, args: dict[str, Any]) -> None:
            records.append(
                {
                    "args": args,
                    "comparison": comparison,
                    "function": function,
                    "id": f"seeded_hrv_{function}_{index:02d}",
                    "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
                }
            )

        add("rmssdRaw", "epsilon", {"nn": nn})
        add("sdnnRaw", "epsilon", {"nn": nn})
        add("rangeFilter", "exact", {"values": rr})
        add("rejectEctopic", "exact", {"values": nn})
        add("cleanRR", "exact", {"values": rr})
        add("cleanRRGapAware", "exact", {"values": rr})
        add("rmssdGapAware", "epsilon", {"contiguous": contiguous, "nn": nn})
        add("pnn50GapAware", "epsilon", {"contiguous": contiguous, "nn": nn})
        add("beatSpreadIsTrustworthy", "exact", {"verdict": verdicts[index]})
        add("beatAccurateFraction", "epsilon", {"rrMs": nn, "tsSec": ts})
        add("beatValuesAreTrustworthy", "exact", {"fraction": index / 2.0})
        add("classifyCoverage", "exact", {"collapsed": 0.9 + index * 0.2, "coverage": 0.8 + index * 0.2})
        add("rrCoverage", "epsilon", {"rrMs": nn, "tsSec": ts})
        add("duplicateBeatCount", "exact", {"rrMs": nn, "tsSec": ts})
        collapse_args: dict[str, Any] = {"rrMs": nn, "tsSec": ts}
        collapsed_args: dict[str, Any] = {"rrMs": nn, "tsSec": ts}
        densest_args: dict[str, Any] = {"rrMs": nn, "srcCodes": src, "tsSec": ts}
        if index:
            collapse_args.update({"rrTolMs": 35.0 + index, "windowSec": index - 1})
            collapsed_args["rrTolMs"] = 25.0 + index
            densest_args.update({"halfWindowSec": index, "maxRowsPerSecond": 2 + index})
        add("collapseOverCount", "exact", collapse_args)
        add("collapsedCoverage", "epsilon", collapsed_args)
        add("densestSecondWindowSample", "exact", densest_args)
    recovery_functions = (
        ("RecoveryScorer.parasympatheticSaturation/2", "epsilon"),
        ("RecoveryScorer.restingHR/3", "exact"),
        ("RecoveryScorer.recoveryIndexSlope/3", "epsilon"),
        ("RecoveryScorer.band/1", "exact"),
        ("RecoveryScorer.zScore/3", "epsilon"),
        ("RecoveryScorer.recovery/12", "exact"),
        ("RecoveryScorer.logisticScore/1", "epsilon"),
        ("RecoveryScorer.recovery/11", "exact"),
    )
    for round_index in range(2):
        base = 50_000 + round_index * 10_000
        hr = [
            {"ts": base + bin_index * 300 + sample, "bpm": 72 - bin_index + round_index}
            for bin_index in range(6)
            for sample in range(5)
        ]
        driver = {"mean": 50.0 + round_index, "spread": 4.0 + round_index}
        state = {
            "baseline": 50.0 + round_index,
            "spread": 4.0 + round_index,
            "nValid": 4 + round_index,
            "nightsSinceUpdate": 0,
            "status": "provisional",
        }
        arguments = (
            {"hrvZ": -0.75 - round_index * 0.5, "rhrZ": 0.8 + round_index * 0.4},
            {"hr": hr, "start": base, "end": base + 1800},
            {"hr": hr, "start": base, "end": base + 1800},
            {"score": 20.0 + rng.bounded(70)},
            {"value": 40.0 + rng.bounded(30), "mean": 50.0, "spread": 2.0 + round_index},
            {
                "hrv": 48.0 + rng.bounded(20), "rhr": 50.0 + rng.bounded(20),
                "hrvBaseline": driver, "sleepPerf": 0.75 + round_index * 0.1,
                "useDefaults": round_index == 0,
                **({} if round_index == 0 else {"hrvBaselineUsable": True}),
            },
            {"compositeZ": -1.0 + round_index * 2.0},
            {
                "hrv": 48.0 + rng.bounded(20), "rhr": 50.0 + rng.bounded(20),
                "hrvBaseline": state, "sleepPerf": 0.75 + round_index * 0.1,
                "useDefaults": round_index == 0,
            },
        )
        for index, ((function, comparison), args) in enumerate(zip(recovery_functions, arguments)):
            records.append(
                {
                    "args": args,
                    "comparison": comparison,
                    "function": function,
                    "id": f"seeded_recovery_{round_index}_{index:02d}",
                    "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
                }
            )
    for round_index in range(2):
        baseline = {
            "baseline": 48.0 + round_index * 3.0,
            "nValid": 8 + round_index * 8,
            "nightsSinceUpdate": round_index,
            "spread": 4.0 + round_index,
            "status": "provisional" if round_index == 0 else "trusted",
        }
        records.append(
            {
                "args": {
                    "hrv": 44.0 + rng.bounded(17),
                    "hrvBaseline": baseline,
                    "resp": 13.0 + rng.bounded(5) / 2.0,
                    "respBaseline": {
                        "baseline": 15.0,
                        "nValid": 12,
                        "nightsSinceUpdate": round_index,
                        "spread": 1.5,
                        "status": "trusted",
                    },
                    "rhr": 48.0 + rng.bounded(17),
                    "rhrBaseline": {
                        "baseline": 58.0,
                        "nValid": 12,
                        "nightsSinceUpdate": round_index,
                        "spread": 4.0,
                        "status": "trusted",
                    },
                    "skinTempDev": 0.2 + round_index * 0.15,
                    "sleepPerf": 0.72 + round_index * 0.18,
                    "useDefaults": False,
                },
                "comparison": "exact",
                "function": RECOVERY_TRACE_KEY,
                "id": f"seeded_recovery_trace_{round_index:02d}",
                "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
            }
        )
    return records


def _is_signed_integer(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _checked_integer(value: int, record: dict[str, Any], operation: str) -> int:
    if not INT64_MIN <= value <= INT64_MAX:
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} {operation} overflows signed Int64"
        )
    return value


def _checked_add(left: int, right: int, record: dict[str, Any], operation: str) -> int:
    return _checked_integer(left + right, record, operation)


def _checked_subtract(left: int, right: int, record: dict[str, Any], operation: str) -> int:
    return _checked_integer(left - right, record, operation)


def _checked_multiply(left: int, right: int, record: dict[str, Any], operation: str) -> int:
    return _checked_integer(left * right, record, operation)


def _validate_hr_samples(args: dict[str, Any], record: dict[str, Any]) -> None:
    rows = args.get("hr")
    valid = isinstance(rows, list) and all(
        isinstance(row, dict)
        and _is_signed_integer(row.get("ts"), INT64_MIN, INT64_MAX)
        and _is_signed_integer(row.get("bpm"), INT32_MIN, INT32_MAX)
        for row in rows
    )
    if not valid:
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} requires integer hr ts/bpm rows"
        )
    for left, right in zip(rows, rows[1:]):
        _checked_subtract(right["ts"], left["ts"], record, "adjacent timestamp difference")
    if len(rows) >= 2:
        _checked_subtract(rows[-1]["ts"], rows[0]["ts"], record, "total timestamp span")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_recovery_window(args: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    _validate_hr_samples(args, record)
    start = args.get("start")
    end = args.get("end")
    if not _is_signed_integer(start, INT64_MIN, INT64_MAX) or not _is_signed_integer(
        end, INT64_MIN, INT64_MAX
    ):
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} start/end must fit signed Int64"
        )
    if start > end:
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} requires start <= end"
        )
    _checked_subtract(end, start, record, "window timestamp span")
    if end > INT64_MAX - 300:
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} bin-end addition overflows signed Int64"
        )
    return dict(args)


def _validate_driver_baseline(value: Any, record: dict[str, Any], label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not all(_is_number(value.get(key)) for key in ("mean", "spread")):
        raise ParityFormatError(f"case {record.get('id')!r} {label} must be a driver baseline")
    if value["spread"] <= 0:
        raise ParityFormatError(f"case {record.get('id')!r} {label}.spread must be positive")


def _validate_baseline_state(value: Any, record: dict[str, Any], label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not all(
        _is_number(value.get(key)) for key in ("baseline", "spread")
    ):
        raise ParityFormatError(f"case {record.get('id')!r} {label} must be a baseline state")
    if value["spread"] <= 0:
        raise ParityFormatError(f"case {record.get('id')!r} {label}.spread must be positive")
    for key in ("nValid", "nightsSinceUpdate"):
        if not _is_signed_integer(value.get(key), 0, INT32_MAX):
            raise ParityFormatError(f"case {record.get('id')!r} {label}.{key} must fit non-negative Int32")
    if value.get("status") not in {"calibrating", "provisional", "trusted", "stale"}:
        raise ParityFormatError(f"case {record.get('id')!r} {label}.status is not canonical")


def _effective_recovery_call(
    args: dict[str, Any], record: dict[str, Any], baseline_kind: str
) -> dict[str, Any]:
    function = record.get("function")
    if not all(_is_number(args.get(key)) for key in ("hrv", "rhr")):
        raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric hrv/rhr")
    for key in ("resp", "sleepPerf", "skinTempDev", "recoveryIndexSlope", "priorDayEffort"):
        if key in args and args[key] is not None and not _is_number(args[key]):
            raise ParityFormatError(f"case {record.get('id')!r} {function} {key} must be numeric or null")
    use_defaults = args.get("useDefaults")
    if not isinstance(use_defaults, bool):
        raise ParityFormatError(f"case {record.get('id')!r} {function} requires boolean useDefaults")
    baseline_keys = ("hrvBaseline", "rhrBaseline", "respBaseline", "effortBaseline")
    validator = _validate_driver_baseline if baseline_kind == "driver" else _validate_baseline_state
    for key in baseline_keys:
        validator(args.get(key), record, key)
    effective = dict(args)
    effective.update(
        {
            "resp": args.get("resp"),
            "rhrBaseline": args.get("rhrBaseline"),
            "respBaseline": args.get("respBaseline"),
            "sleepPerf": args.get("sleepPerf"),
            "skinTempDev": args.get("skinTempDev"),
            "recoveryIndexSlope": args.get("recoveryIndexSlope"),
            "effortBaseline": args.get("effortBaseline"),
            "priorDayEffort": args.get("priorDayEffort"),
        }
    )
    if baseline_kind == "driver":
        usable = args.get("hrvBaselineUsable", RECOVERY_DEFAULT_HRV_BASELINE_USABLE)
        if not isinstance(usable, bool):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} hrvBaselineUsable must be boolean"
            )
        effective["hrvBaselineUsable"] = usable
        defaulted = {
            "skinTempDev", "hrvBaselineUsable", "recoveryIndexSlope",
            "effortBaseline", "priorDayEffort",
        }
        if use_defaults and any(key in args for key in defaulted):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} bare call cannot override defaults"
            )
    elif use_defaults and any(
        key in args for key in ("skinTempDev", "recoveryIndexSlope", "effortBaseline", "priorDayEffort")
    ):
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} bare call cannot override defaults"
        )
    return effective


def _validate_recovery_trace_args(args: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Validate the public trace adapter inside a finite physiological envelope.

    The bound is deliberately much tighter than the public Double domain. Kotlin's current
    two-decimal formatter passes through a Long and therefore differs from Swift for very large
    finite values and for negative values that round to zero (bhelm/noop#47). This differential
    slice fails closed before either native runner for those unresolved domains.
    """

    function = RECOVERY_TRACE_KEY
    if not isinstance(args, dict):
        raise ParityFormatError(f"case {record.get('id')!r} {function} args must be an object")
    allowed = {
        "hrv", "rhr", "resp", "hrvBaseline", "rhrBaseline", "respBaseline",
        "sleepPerf", "skinTempDev", "useDefaults",
    }
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} has unknown fields {unknown}"
        )
    use_defaults = args.get("useDefaults")
    if not isinstance(use_defaults, bool):
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} useDefaults must be boolean"
        )
    if use_defaults and "skinTempDev" in args:
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} bare call cannot override arg8"
        )

    def scalar(key: str, minimum: float, maximum: float, optional: bool = False) -> float | None:
        value = args.get(key)
        if optional and value is None:
            return None
        if not _is_number(value) or not minimum <= value <= maximum:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} is outside [{minimum}, {maximum}]; "
                "large finite trace inputs remain tracked by bhelm/noop#47"
            )
        if value == 0.0 and math.copysign(1.0, value) < 0:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} excludes signed zero pending bhelm/noop#47"
            )
        return float(value)

    hrv = scalar("hrv", 1.0, 1_000.0)
    rhr = scalar("rhr", 20.0, 250.0)
    resp = scalar("resp", 1.0, 100.0, optional=True)
    sleep = scalar("sleepPerf", 0.0, 1.0, optional=True)
    skin = scalar("skinTempDev", -20.0, 20.0, optional=True)

    statuses = {"calibrating", "provisional", "trusted", "stale"}

    def baseline(key: str, minimum: float, maximum: float, required: bool) -> dict[str, Any] | None:
        value = args.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, dict) or set(value) != {
            "baseline", "spread", "nValid", "nightsSinceUpdate", "status"
        }:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} must be a complete baseline state"
            )
        center = value.get("baseline")
        spread = value.get("spread")
        if not _is_number(center) or not minimum <= center <= maximum:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key}.baseline is out of bounds"
            )
        if not _is_number(spread) or not 0.001 <= spread <= 1_000.0:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key}.spread is out of bounds"
            )
        for count_key in ("nValid", "nightsSinceUpdate"):
            if not _is_signed_integer(value.get(count_key), 0, 1_000_000):
                raise ParityFormatError(
                    f"case {record.get('id')!r} {function} {key}.{count_key} is invalid"
                )
        if value.get("status") not in statuses:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key}.status is invalid"
            )
        return value

    hrv_base = baseline("hrvBaseline", 1.0, 1_000.0, required=True)
    rhr_base = baseline("rhrBaseline", 20.0, 250.0, required=False)
    resp_base = baseline("respBaseline", 1.0, 100.0, required=False)
    assert hrv is not None and rhr is not None and hrv_base is not None

    # Reject every derived negative value in the signed-zero rounding interval. Merely checking
    # direct inputs is insufficient: z-scores and penalties can enter it after arithmetic.
    hrv_z = (hrv - hrv_base["baseline"]) / hrv_base["spread"]
    derived = [hrv_z]
    terms = [(hrv_z, 0.55)]
    if rhr_base is not None:
        rhr_z = (rhr_base["baseline"] - rhr) / rhr_base["spread"]
        derived.append(rhr_z)
        terms.append((rhr_z, 0.20))
    if resp is not None and resp_base is not None:
        resp_z = (resp_base["baseline"] - resp) / resp_base["spread"]
        derived.append(resp_z)
        terms.append((resp_z, 0.05))
    if sleep is not None:
        sleep_z = (sleep - 0.85) / 0.12
        derived.append(sleep_z)
        terms.append((sleep_z, 0.15))
    if skin is not None:
        skin_z = -abs(skin)
        derived.extend((skin_z, skin))
        terms.append((skin_z, 0.05))
    if hrv_base["status"] in {"provisional", "trusted"}:
        total_weight = sum(weight for _z, weight in terms)
        derived.append(sum(z * weight for z, weight in terms) / total_weight)
    if any(-0.005 < value < 0.0 or (value == 0.0 and math.copysign(1.0, value) < 0) for value in derived):
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} enters the signed-zero trace domain tracked by bhelm/noop#47"
        )

    effective = dict(args)
    effective.update({"resp": resp, "rhrBaseline": rhr_base, "respBaseline": resp_base,
                      "sleepPerf": sleep, "skinTempDev": skin})
    return effective


def _effective_strain_call(call: Any, record: dict[str, Any]) -> dict[str, Any]:
    function = "StrainScorer.strain/6"
    if not isinstance(call, dict):
        raise ParityFormatError(f"case {record.get('id')!r} {function} calls must be objects")
    series = call.get("series")
    if not isinstance(series, dict):
        raise ParityFormatError(f"case {record.get('id')!r} {function} requires call.series")
    if not _is_signed_integer(series.get("count"), 0, INT32_MAX):
        raise ParityFormatError(f"case {record.get('id')!r} {function} count must fit non-negative Int32")
    if not _is_signed_integer(series.get("startTs"), INT64_MIN, INT64_MAX):
        raise ParityFormatError(f"case {record.get('id')!r} {function} startTs must fit signed Int64")
    if not _is_signed_integer(series.get("stepSec"), INT64_MIN, INT64_MAX):
        raise ParityFormatError(f"case {record.get('id')!r} {function} stepSec must fit signed Int64")
    if not _is_signed_integer(series.get("bpm"), INT32_MIN, INT32_MAX):
        raise ParityFormatError(f"case {record.get('id')!r} {function} series fields must be integers")
    if series["count"] < 0 or series["stepSec"] <= 0 or not 30 <= series["bpm"] <= 220:
        raise ParityFormatError(f"case {record.get('id')!r} {function} series is outside the normative domain")
    alternate = series.get("alternateBpm")
    if alternate is not None and (
        not _is_signed_integer(alternate, INT32_MIN, INT32_MAX) or not 30 <= alternate <= 220
    ):
        raise ParityFormatError(f"case {record.get('id')!r} {function} alternateBpm is invalid")
    count = series["count"]
    start = series["startTs"]
    step = series["stepSec"]
    generated_end = start
    if count > 0:
        last_offset = _checked_multiply(count - 1, step, record, "series index multiplication")
        generated_end = _checked_add(start, last_offset, record, "series timestamp addition")
    final_ts = series.get("finalTs")
    if final_ts is not None:
        if not _is_signed_integer(final_ts, INT64_MIN, INT64_MAX) or count < 2:
            raise ParityFormatError(f"case {record.get('id')!r} {function} finalTs is invalid")
        neighbor_offset = _checked_multiply(count - 2, step, record, "finalTs neighbor multiplication")
        neighbor = _checked_add(start, neighbor_offset, record, "finalTs neighbor addition")
        if final_ts <= neighbor:
            raise ParityFormatError(f"case {record.get('id')!r} {function} finalTs is invalid")
        _checked_subtract(final_ts, neighbor, record, "finalTs neighbor difference")
        generated_end = final_ts
    if count >= 2:
        _checked_subtract(generated_end, start, record, "series total span")
    use_defaults = call.get("useDefaults", False)
    if not isinstance(use_defaults, bool):
        raise ParityFormatError(f"case {record.get('id')!r} {function} useDefaults must be boolean")
    controls = ("maxHR", "restingHR", "method", "sex", "denominator")
    if use_defaults and any(key in call for key in controls):
        raise ParityFormatError(f"case {record.get('id')!r} {function} bare calls cannot override defaults")
    if not use_defaults and not all(key in call for key in controls):
        raise ParityFormatError(f"case {record.get('id')!r} {function} explicit calls require every control")
    max_hr = call.get("maxHR", STRAIN_DEFAULT_MAX_HR)
    resting = call.get("restingHR", STRAIN_DEFAULT_RESTING_HR)
    denominator = call.get("denominator", STRAIN_DEFAULT_DENOMINATOR)
    if not all(_is_number(value) for value in (max_hr, resting, denominator)):
        raise ParityFormatError(f"case {record.get('id')!r} {function} controls must be numeric")
    if denominator <= 1:
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} denominator must be > 1; tracked by bhelm/noop#36"
        )
    method = call.get("method", "edwards")
    sex = call.get("sex", "male")
    if method not in {"edwards", "banister"} or not isinstance(sex, str):
        raise ParityFormatError(f"case {record.get('id')!r} {function} method/sex is invalid")
    return {
        "denominator": denominator,
        "maxHR": max_hr,
        "method": method,
        "restingHR": resting,
        "series": dict(series),
        "sex": sex,
        "useDefaults": use_defaults,
    }


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
    if function in {"trimpToStrain", "StrainScorer.trimpToStrain/2"}:
        if not _is_number(args.get("trimp")):
            raise ParityFormatError(f"case {record.get('id')!r} trimpToStrain requires args.trimp")
        denominator = args.get("denominator", STRAIN_DEFAULT_DENOMINATOR)
        if not _is_number(denominator) or denominator <= 1:
            raise ParityFormatError(
                f"case {record.get('id')!r} trimpToStrain denominator must be > 1; "
                "invalid log-domain parity is tracked by bhelm/noop#36"
            )
        return {
            "denominator": denominator,
            "trimp": args["trimp"],
        }
    if function == "StrainScorer.tanakaHRmax/1":
        if not _is_number(args.get("age")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric args.age")
        return dict(args)
    if function == "StrainScorer.defaultMaxHR/1":
        if "ageInt" in args and (not isinstance(args["ageInt"], int) or isinstance(args["ageInt"], bool)):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires integer args.ageInt")
        return {"ageInt": args.get("ageInt", STRAIN_DEFAULT_AGE)}
    if function == "StrainScorer.percentile/2":
        if not isinstance(args.get("values"), list) or not all(_is_number(v) for v in args["values"]) or not _is_number(args.get("pct")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.values/pct")
        if not 0 <= args["pct"] <= 100:
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires pct in 0...100")
        return dict(args)
    if function == "StrainScorer.estimateHRmax/2":
        history = args.get("history")
        if not isinstance(history, dict) or not isinstance(history.get("count"), int) or isinstance(history.get("count"), bool) or history["count"] < 0:
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires non-negative history.count")
        if not all(_is_number(history.get(k)) for k in ("low", "high")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric history.low/high")
        if "age" in args and not _is_number(args["age"]):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric args.age")
        return dict(args)
    if function in {"StrainScorer.pctHRR/3", "StrainScorer.zoneWeight/3"}:
        if not all(_is_number(args.get(k)) for k in ("bpm", "restingHR", "hrReserve")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires bpm/restingHR/hrReserve")
        if args["hrReserve"] <= 0:
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires positive hrReserve")
        if "characterizeZones" in args and (
            function != "StrainScorer.zoneWeight/3" or not isinstance(args["characterizeZones"], bool)
        ):
            raise ParityFormatError(f"case {record.get('id')!r} {function} characterizeZones must be boolean")
        return dict(args)
    if function == "StrainScorer.effectiveEffort/2":
        if any(k in args and not _is_number(args[k]) for k in ("live", "stored")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric live/stored")
        if all(k in args and args[k] == 0 for k in ("live", "stored")) and math.copysign(1.0, args["live"]) != math.copysign(1.0, args["stored"]):
            raise ParityFormatError(
                f"case {record.get('id')!r} mixed signed-zero exact bits are excluded; "
                "parity is tracked by bhelm/noop#37"
            )
        return dict(args)
    if function in {"StrainScorer.sampleDurationMinutes/1", "StrainScorer.sampleDurationsMinutes/1"}:
        _validate_hr_samples(args, record)
        return dict(args)
    if function in {"StrainScorer.edwardsTRIMP/4", "StrainScorer.banisterTRIMP/5"}:
        _validate_hr_samples(args, record)
        if not isinstance(args.get("durations"), list) or len(args["durations"]) != len(args["hr"]) or not all(_is_number(v) for v in args["durations"]):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires one duration per HR row")
        required = ("restingHR", "hrReserve") + (("b",) if function.endswith("banisterTRIMP/5") else ())
        if not all(_is_number(args.get(k)) for k in required):
            raise ParityFormatError(f"case {record.get('id')!r} {function} has missing numeric scalars")
        if args["hrReserve"] <= 0:
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires positive hrReserve")
        return dict(args)
    if function == "StrainScorer.fitStrainDenominator/1":
        pairs = args.get("pairs")
        if not isinstance(pairs, list) or not all(isinstance(p, list) and len(p) == 2 and all(_is_number(v) for v in p) for p in pairs):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric [trimp,strain] pairs")
        return dict(args)
    if function == "StrainScorer.strain/6":
        calls = args.get("strainCalls")
        replay = args.get("replayFirstAtEnd", False)
        if not isinstance(calls, list) or not calls or not isinstance(replay, bool):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires strainCalls/replay flag")
        if replay and (len(calls) < 3 or calls[0] != calls[-1]):
            raise ParityFormatError(f"case {record.get('id')!r} {function} replay must be A→B→A")
        return {
            "replayFirstAtEnd": replay,
            "strainCalls": [_effective_strain_call(call, record) for call in calls],
        }
    if function == "RecoveryScorer.parasympatheticSaturation/2":
        if not _is_number(args.get("hrvZ")) or (
            "rhrZ" in args and args["rhrZ"] is not None and not _is_number(args["rhrZ"])
        ):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires hrvZ/rhrZ")
        if "characterizeRecoveryConstants" in args and not isinstance(
            args["characterizeRecoveryConstants"], bool
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} characterizeRecoveryConstants must be boolean"
            )
        return {"hrvZ": args["hrvZ"], "rhrZ": args.get("rhrZ"), **(
            {"characterizeRecoveryConstants": args["characterizeRecoveryConstants"]}
            if "characterizeRecoveryConstants" in args else {}
        )}
    if function in {"RecoveryScorer.restingHR/3", "RecoveryScorer.recoveryIndexSlope/3"}:
        return _validate_recovery_window(args, record)
    if function == "RecoveryScorer.band/1":
        if not _is_number(args.get("score")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires numeric score")
        return dict(args)
    if function == "RecoveryScorer.zScore/3":
        if not all(_is_number(args.get(key)) for key in ("value", "mean", "spread")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires value/mean/spread")
        if args["spread"] <= 0:
            raise ParityFormatError(f"case {record.get('id')!r} {function} spread must be positive")
        return dict(args)
    if function == "RecoveryScorer.recovery/12":
        return _effective_recovery_call(args, record, "driver")
    if function == "RecoveryScorer.logisticScore/1":
        if not _is_number(args.get("compositeZ")):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires compositeZ")
        return dict(args)
    if function == "RecoveryScorer.recovery/11":
        if not isinstance(args.get("hrvBaseline"), dict):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires hrvBaseline")
        return _effective_recovery_call(args, record, "state")
    if function == RECOVERY_TRACE_KEY:
        return _validate_recovery_trace_args(args, record)
    if function in {"rmssdRaw", "sdnnRaw"}:
        if not isinstance(args.get("nn"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.nn")
        return dict(args)
    if function == RAW_ANALYZE_KEY:
        if not isinstance(args.get("rawRR"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.rawRR")
        if "maxRejectedFraction" in args and not isinstance(
            args["maxRejectedFraction"], (int, float)
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires numeric args.maxRejectedFraction"
            )
        return dict(args)
    if function == HRV_MEDIAN_KEY:
        if not isinstance(args.get("values"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.values")
        return dict(args)
    if function == "analyze/3":
        if not isinstance(args.get("rr"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.rr")
        for bound in ("windowStart", "windowEnd"):
            if bound in args and not isinstance(args[bound], int):
                raise ParityFormatError(
                    f"case {record.get('id')!r} {function} requires an integer args.{bound}"
                )
        return dict(args)
    if function in {"rangeFilter", "rejectEctopic", "cleanRR", "cleanRRGapAware"}:
        if not isinstance(args.get("values"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.values")
        return dict(args)
    if function in {"rmssdGapAware", "pnn50GapAware"}:
        if not isinstance(args.get("nn"), list) or not isinstance(args.get("contiguous"), list):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires args.nn/contiguous"
            )
        return dict(args)
    if function == "beatSpreadIsTrustworthy":
        if not isinstance(args.get("verdict"), str):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.verdict")
        return dict(args)
    if function == "beatValuesAreTrustworthy":
        if not isinstance(args.get("fraction"), (int, float)):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.fraction")
        return dict(args)
    if function == "classifyCoverage":
        if not all(isinstance(args.get(key), (int, float)) for key in ("coverage", "collapsed")):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires args.coverage/collapsed"
            )
        return dict(args)
    if function in {"beatAccurateFraction", "rrCoverage", "duplicateBeatCount"}:
        if not isinstance(args.get("tsSec"), list) or not isinstance(args.get("rrMs"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.tsSec/rrMs")
        return dict(args)
    if function == "collapseOverCount":
        if not isinstance(args.get("tsSec"), list) or not isinstance(args.get("rrMs"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.tsSec/rrMs")
        return {
            "rrMs": args["rrMs"],
            "rrTolMs": args.get("rrTolMs", COLLAPSE_DEFAULT_RR_TOL_MS),
            "tsSec": args["tsSec"],
            "windowSec": args.get("windowSec", COLLAPSE_DEFAULT_WINDOW_SEC),
        }
    if function == "collapsedCoverage":
        if not isinstance(args.get("tsSec"), list) or not isinstance(args.get("rrMs"), list):
            raise ParityFormatError(f"case {record.get('id')!r} {function} requires args.tsSec/rrMs")
        return {
            "rrMs": args["rrMs"],
            "rrTolMs": args.get("rrTolMs", COLLAPSED_COVERAGE_DEFAULT_RR_TOL_MS),
            "tsSec": args["tsSec"],
        }
    if function == "densestSecondWindowSample":
        if not all(isinstance(args.get(key), list) for key in ("tsSec", "rrMs", "srcCodes")):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires args.tsSec/rrMs/srcCodes"
            )
        return {
            "halfWindowSec": args.get("halfWindowSec", DENSEST_DEFAULT_HALF_WINDOW_SEC),
            "maxRowsPerSecond": args.get(
                "maxRowsPerSecond", DENSEST_DEFAULT_MAX_ROWS_PER_SECOND
            ),
            "rrMs": args["rrMs"],
            "srcCodes": args["srcCodes"],
            "tsSec": args["tsSec"],
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
                "function": "StrainScorer.trimpToStrain/2",
                "id": "trimp_negative_probe",
                "source": "negative-control",
            },
            {
                "args": {"score": 34.0},
                "comparison": "exact",
                "function": "RecoveryScorer.band/1",
                "id": "recovery_negative_band_probe",
                "source": "negative-control",
            },
            {
                "args": {"compositeZ": 0.0},
                "comparison": "epsilon",
                "function": "RecoveryScorer.logisticScore/1",
                "id": "recovery_negative_logistic_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "hrv": 55.0, "rhr": 56.0,
                    "hrvBaseline": {"baseline": 50.0, "spread": 5.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "sleepPerf": 0.9, "useDefaults": True,
                },
                "comparison": "exact",
                "function": RECOVERY_TRACE_KEY,
                "id": "recovery_trace_negative_score_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "hrv": 45.0, "rhr": 55.0,
                    "hrvBaseline": {"baseline": 50.0, "spread": 10.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "rhrBaseline": {"baseline": 60.0, "spread": 10.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "skinTempDev": 0.125, "useDefaults": False,
                },
                "comparison": "exact",
                "function": RECOVERY_TRACE_KEY,
                "id": "recovery_trace_negative_line_probe",
                "source": "negative-control",
            },
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
        known_issue = record.get("knownBehaviorIssue")
        supported_known_issues = {"bhelm/noop#10", "bhelm/noop#12", "bhelm/noop#39", "bhelm/noop#40"}
        if known_issue is not None and known_issue not in supported_known_issues:
            raise ParityFormatError(
                f"case {case_id!r} has unsupported knownBehaviorIssue {known_issue!r}"
            )
        function = record.get("function")
        args = record.get("args", {})
        hr = args.get("hr", []) if isinstance(args, dict) else []
        has_non_increasing_ts = any(
            left.get("ts", 0) >= right.get("ts", 0)
            for left, right in zip(hr, hr[1:])
            if isinstance(left, dict) and isinstance(right, dict)
        )
        needs_issue_12 = (
            function == "StrainScorer.sampleDurationsMinutes/1"
            or (function == "StrainScorer.sampleDurationMinutes/1" and has_non_increasing_ts)
            or (
                function in {"trimpToStrain", "StrainScorer.trimpToStrain/2"}
                and isinstance(args, dict) and _is_number(args.get("trimp"))
                and args["trimp"] > 7_200
            )
        )
        if needs_issue_12 and known_issue != "bhelm/noop#12":
            raise ParityFormatError(
                f"case {case_id!r} characterizes shared Strain behavior tracked by bhelm/noop#12"
            )
        expected_recovery_issue = None
        if function == "RecoveryScorer.recoveryIndexSlope/3" and case_id in {
            "recovery_index_sparse_bins",
        }:
            expected_recovery_issue = "bhelm/noop#10"
        elif case_id in {"recovery_resting_aligned_endpoint", "recovery_index_aligned_endpoint_gate"}:
            expected_recovery_issue = "bhelm/noop#39"
        elif case_id == "recovery_driver_missing_hrv_baseline":
            expected_recovery_issue = "bhelm/noop#40"
        if expected_recovery_issue is not None and known_issue != expected_recovery_issue:
            raise ParityFormatError(
                f"case {case_id!r} characterizes shared Recovery behavior tracked by {expected_recovery_issue}"
            )
        record["effectiveArgs"] = _effective_args(record)
        _validate_finite_tree(record["effectiveArgs"], f"case {case_id!r} effectiveArgs")
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
