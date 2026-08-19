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
the named runner mutates its declared negative probes.
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
RECOVERY_FORECAST_KEY = "RecoveryForecaster.forecast/6"
HEART_RATE_RECOVERY_KEY = "HeartRateRecovery.calculate/4"
SLEEP_CREDIT_KEY = "SleepDebt.creditedSleepMin/2"
SLEEP_LEDGER_KEY = "SleepDebt.ledger/3"
SLEEP_AUTO_BED_KEY = "SleepEditGuard.autoCorrectedBed/5"
SLEEP_DISJOINT_KEY = "SleepEditGuard.isDisjoint/4"
SLEEP_CLAMP_KEY = "SleepEditGuard.clampedEditWindow/4"
SLEEP_WAKE_KEY = "SleepStageVocabulary.isWake/1"
SLEEP_RECLIP_KEY = "SleepWindowReclip.reclip/5"
SLEEP_FUNCTIONS = {
    SLEEP_CREDIT_KEY, SLEEP_LEDGER_KEY, SLEEP_AUTO_BED_KEY, SLEEP_DISJOINT_KEY,
    SLEEP_CLAMP_KEY, SLEEP_WAKE_KEY, SLEEP_RECLIP_KEY,
}
SLEEP_DEFAULT_NEED_HOURS = 8.0
SLEEP_DEFAULT_WINDOW = 14
SLEEP_DEFAULT_SLACK_SEC = 300
SLEEP_MAX_ROWS = 256
SLEEP_MAX_TEXT_BYTES = 16_384
SLEEP_SEEDS = {
    SLEEP_CREDIT_KEY: (0x534C_4352_4544_0001, 0x534C_4352_4544_0002),
    SLEEP_LEDGER_KEY: (0x534C_4C45_4447_0001, 0x534C_4C45_4447_0002),
    SLEEP_AUTO_BED_KEY: (0x534C_4155_544F_0001, 0x534C_4155_544F_0002),
    SLEEP_DISJOINT_KEY: (0x534C_4449_534A_0001, 0x534C_4449_534A_0002),
    SLEEP_CLAMP_KEY: (0x534C_434C_414D_0001, 0x534C_434C_414D_0002),
    SLEEP_WAKE_KEY: (0x534C_5741_4B45_0001, 0x534C_5741_4B45_0002),
    SLEEP_RECLIP_KEY: (0x534C_5245_434C_0001, 0x534C_5245_434C_0002),
}
SLEEP_REGRESSION_FIXTURES = {
    "sleep_wake_issue_41_lf": {
        "function": SLEEP_WAKE_KEY, "regressionIssue": "bhelm/noop#41",
        "args": {"stage": "\nwake\n"},
    },
    "sleep_wake_issue_41_cr": {
        "function": SLEEP_WAKE_KEY, "regressionIssue": "bhelm/noop#41",
        "args": {"stage": "\rawake\r"},
    },
    "sleep_reclip_issue_42_negative_start": {
        "function": SLEEP_RECLIP_KEY, "regressionIssue": "bhelm/noop#42",
        "args": {
            "stagesJSON": '[{"start":-10,"end":10,"stage":"light"}]',
            "sessionStart": 0, "oldEnd": 20, "newStart": 0, "newEnd": 20,
        },
    },
    "sleep_reclip_issue_42_empty_stage": {
        "function": SLEEP_RECLIP_KEY, "regressionIssue": "bhelm/noop#42",
        "args": {
            "stagesJSON": '[{"start":100,"end":200,"stage":""}]',
            "sessionStart": 100, "oldEnd": 300, "newStart": 100, "newEnd": 300,
        },
    },
}
RECOVERY_DRIVERS_KEY = (
    "RecoveryScorer.chargeDrivers/8=RecoveryDrivers.chargeDrivers/8"
)
RECOVERY_FORECAST_FUNCTIONS = (
    RECOVERY_FORECAST_KEY,
    "RecoveryForecaster.mean/1",
    "RecoveryForecaster.sampleSD/1",
    "RecoveryForecaster.leastSquaresSlope/1",
    "RecoveryForecaster.clamp/3",
)
RECOVERY_FORECAST_MAX_VALUES = 4096
RECOVERY_FORECAST_HELPER_ABS_MAX = 1e100
RECOVERY_FORECAST_SLEEP_HOURS_MAX = 24.0
RECOVERY_FORECAST_SEED = GENERATOR_SEED ^ 0x5246_4F52_4543_4153
WATCH_RECOVERY_KEY = "WatchRecovery.compute/4"
WATCH_RECOVERY_MAX_HISTORY = 4096
WATCH_RECOVERY_ABS_MAX = 1e6
WATCH_RECOVERY_SEED = GENERATOR_SEED ^ 0x5741_5443_4852_4543
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
        if record.get("function") == RECOVERY_DRIVERS_KEY:
            _validate_recovery_drivers_rows(value, f"{side} id={case_id} valueBits")
    else:
        _validate_finite_tree(value, f"{side} id={case_id} value")
    return ("value", value)


def _validate_recovery_drivers_rows(value: Any, path: str) -> None:
    """Require the exact public Charge-driver row contract, including text wrappers."""

    if not isinstance(value, list) or len(value) > 5:
        raise ParityFormatError(f"{path} must be a list of at most five driver rows")
    fields = {"label", "deltaPoints", "valueText", "baselineText", "verdict"}
    labels: set[str] = set()
    canonical_labels = {
        "Heart rate variability", "Resting heart rate", "Sleep quality",
        "Respiratory rate", "Skin temperature",
    }
    for index, row in enumerate(value):
        row_path = f"{path}[{index}]"
        if not isinstance(row, dict) or set(row) != fields:
            raise ParityFormatError(f"{row_path} must contain exactly {sorted(fields)}")
        delta = row.get("deltaPoints")
        if not _is_signed_integer(delta, -100, 100):
            raise ParityFormatError(f"{row_path}.deltaPoints must be within [-100, 100]")
        decoded: dict[str, str] = {}
        for field in ("label", "valueText", "baselineText", "verdict"):
            wrapper = row.get(field)
            if not isinstance(wrapper, dict) or set(wrapper) != {"text"} or not isinstance(
                wrapper.get("text"), str
            ):
                raise ParityFormatError(f"{row_path}.{field} must be an exact text wrapper")
            decoded[field] = wrapper["text"]
        if decoded["label"] not in canonical_labels:
            raise ParityFormatError(f"{row_path}.label is not a canonical Charge-driver label")
        if decoded["label"] in labels:
            raise ParityFormatError(f"{row_path}.label is duplicated")
        labels.add(decoded["label"])
        if not decoded["valueText"] or not decoded["verdict"]:
            raise ParityFormatError(f"{row_path} valueText/verdict must not be empty")


def _validate_recovery_drivers_output(
    value: Any, expected: dict[str, Any], path: str
) -> None:
    """Apply the structural contract and any issue-linked exact-row oracle."""

    _validate_recovery_drivers_rows(value, path)
    expected_rows = expected.get("expectedRows")
    if expected_rows is None:
        return
    _validate_recovery_drivers_rows(expected_rows, f"input id={expected['id']} expectedRows")
    if value != expected_rows:
        raise ParityFormatError(
            f"{path} does not match issue-linked expectedRows for {expected['id']}"
        )


def _validate_watch_recovery_output(
    value: Any, expected: dict[str, Any], path: str
) -> None:
    """Require the exact normalized WatchRecovery result and public gate constant."""

    result = _validate_exact_object_keys(
        value, {"recovery", "confidence", "minBaselineNights"}, path
    )
    recovery = result["recovery"]
    if recovery is not None:
        _validate_exact_bits(recovery, f"{path}.recovery")
    confidence = _validate_exact_object_keys(
        result["confidence"], {"text"}, f"{path}.confidence"
    )
    if confidence["text"] not in {"calibrating", "building", "solid"}:
        raise ParityFormatError(
            f"{path}.confidence.text must be one of ['building', 'calibrating', 'solid']"
        )
    if type(result["minBaselineNights"]) is not int or result["minBaselineNights"] != 7:
        raise ParityFormatError(f"{path}.minBaselineNights must be the integer 7")


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


def _validate_exact_object_keys(value: Any, expected_keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ParityFormatError(f"{path} must be an object")
    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ParityFormatError(f"{path} keys mismatch: missing={missing} extra={extra}")
    return value


def _validate_exact_bits(value: Any, path: str) -> None:
    if not isinstance(value, str) or _BITS_RE.fullmatch(value) is None:
        raise ParityFormatError(f"{path} must be 16 lowercase hex digits")


def _forecast_constants_requested(expected: dict[str, Any]) -> bool:
    effective_args = expected.get("effectiveArgs")
    if isinstance(effective_args, dict) and "characterizeForecastConstants" in effective_args:
        return effective_args["characterizeForecastConstants"] is True
    args = expected.get("args")
    return isinstance(args, dict) and args.get("characterizeForecastConstants") is True


def _validate_recovery_forecast_output(
    value: Any, expected: dict[str, Any], path: str
) -> None:
    """Validate the normalized nullable RecoveryForecast exact-output contract."""

    if value is None:
        return
    base_keys = {
        "score", "band", "baseline", "planned", "need",
        "nights", "confidence", "low", "high",
    }
    characterize_constants = _forecast_constants_requested(expected)
    forecast = _validate_exact_object_keys(
        value,
        base_keys | ({"constants"} if characterize_constants else set()),
        path,
    )
    for field in ("score", "band", "baseline", "planned", "need", "low", "high"):
        _validate_exact_bits(forecast[field], f"{path}.{field}")
    if type(forecast["nights"]) is not int:
        raise ParityFormatError(f"{path}.nights must be an integer")
    confidence = _validate_exact_object_keys(
        forecast["confidence"], {"text"}, f"{path}.confidence"
    )
    if confidence["text"] not in {"building", "solid"}:
        raise ParityFormatError(
            f"{path}.confidence.text must be one of ['building', 'solid']"
        )
    if not characterize_constants:
        return
    integer_constants = {
        "baselineWindow", "effortWindow", "minBaselineNights",
        "solidNeedNights", "trustedNights",
    }
    floating_constants = {
        "defaultNeedHours", "effortSpread", "minBandPoints", "reversionAdjCap",
        "reversionWeight", "sleepOverCap", "sleepWeight", "strainAdjCap",
        "strainWeight", "thinBandPoints",
    }
    constants = _validate_exact_object_keys(
        forecast["constants"], integer_constants | floating_constants, f"{path}.constants"
    )
    for field in integer_constants:
        if type(constants[field]) is not int:
            raise ParityFormatError(f"{path}.constants.{field} must be an integer")
    for field in floating_constants:
        _validate_exact_bits(constants[field], f"{path}.constants.{field}")


def _validate_sleep_output(value: Any, expected: dict[str, Any], path: str) -> None:
    function = expected["function"]
    if function == SLEEP_CREDIT_KEY:
        if value is not None:
            _validate_exact_bits(value, path)
        return
    if function == SLEEP_LEDGER_KEY:
        ledger = _validate_exact_object_keys(value, {"balanceMin", "needMin", "nights"}, path)
        _validate_exact_bits(ledger["balanceMin"], f"{path}.balanceMin")
        _validate_exact_bits(ledger["needMin"], f"{path}.needMin")
        if not isinstance(ledger["nights"], list) or len(ledger["nights"]) > SLEEP_MAX_ROWS:
            raise ParityFormatError(f"{path}.nights must be a bounded array")
        for index, night_value in enumerate(ledger["nights"]):
            night = _validate_exact_object_keys(
                night_value, {"day", "sleptMin", "deltaMin"}, f"{path}.nights[{index}]"
            )
            if not isinstance(night["day"], dict) or set(night["day"]) != {"text"} or not isinstance(night["day"]["text"], str):
                raise ParityFormatError(f"{path}.nights[{index}].day must be exact text")
            _validate_exact_bits(night["sleptMin"], f"{path}.nights[{index}].sleptMin")
            _validate_exact_bits(night["deltaMin"], f"{path}.nights[{index}].deltaMin")
        return
    if function in {SLEEP_AUTO_BED_KEY, SLEEP_DISJOINT_KEY, SLEEP_WAKE_KEY}:
        if function == SLEEP_AUTO_BED_KEY and type(value) is not int:
            raise ParityFormatError(f"{path} must be an integer timestamp")
        if function != SLEEP_AUTO_BED_KEY and not isinstance(value, bool):
            raise ParityFormatError(f"{path} must be boolean")
        return
    if function == SLEEP_CLAMP_KEY:
        if value is None:
            return
        window = _validate_exact_object_keys(value, {"start", "end"}, path)
        if type(window["start"]) is not int or type(window["end"]) is not int:
            raise ParityFormatError(f"{path} window bounds must be integers")
        return
    if function == SLEEP_RECLIP_KEY:
        if value is None:
            return
        normalized = _validate_exact_object_keys(value, {"shape", "value"}, path)
        shape = normalized["shape"]
        if not isinstance(shape, dict) or set(shape) != {"text"} or shape["text"] not in {"segments", "minutes"}:
            raise ParityFormatError(f"{path}.shape must be exact segments/minutes text")
        if shape["text"] == "segments":
            if not isinstance(normalized["value"], list) or len(normalized["value"]) > SLEEP_MAX_ROWS + 1:
                raise ParityFormatError(f"{path}.value must be a bounded segment array")
            for index, raw_segment in enumerate(normalized["value"]):
                segment = _validate_exact_object_keys(
                    raw_segment, {"start", "end", "stage"}, f"{path}.value[{index}]"
                )
                if type(segment["start"]) is not int or type(segment["end"]) is not int:
                    raise ParityFormatError(f"{path}.value[{index}] bounds must be integers")
                if not isinstance(segment["stage"], dict) or set(segment["stage"]) != {"text"} or not isinstance(segment["stage"]["text"], str):
                    raise ParityFormatError(f"{path}.value[{index}].stage must be exact text")
        else:
            minutes = _validate_exact_object_keys(
                normalized["value"], {"awake", "light", "deep", "rem"}, f"{path}.value"
            )
            for key in minutes:
                _validate_exact_bits(minutes[key], f"{path}.value.{key}")
        return
    raise ParityFormatError(f"{path} has no Sleep output contract for {function}")


_EXACT_OUTPUT_VALIDATORS = {
    RECOVERY_DRIVERS_KEY: _validate_recovery_drivers_output,
    RECOVERY_FORECAST_KEY: _validate_recovery_forecast_output,
    WATCH_RECOVERY_KEY: _validate_watch_recovery_output,
    **{function: _validate_sleep_output for function in SLEEP_FUNCTIONS},
}


def _validate_function_output(
    expected: dict[str, Any], comparison: str, kind: str, value: Any, path: str
) -> None:
    if comparison != "exact" or kind != "value":
        return
    validator = _EXACT_OUTPUT_VALIDATORS.get(expected["function"])
    if validator is not None:
        validator(value, expected, path)


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


def _known_behavior_expected(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return an issue-linked exact oracle, rejecting inert or malformed metadata."""

    issue = record.get("knownBehaviorIssue")
    if issue not in {"bhelm/noop#55", "bhelm/noop#61", "bhelm/noop#62"}:
        return None
    if issue in {"bhelm/noop#61", "bhelm/noop#62"}:
        if record.get("function") != WATCH_RECOVERY_KEY or record.get("comparison") != "exact":
            raise ParityFormatError(
                f"case {record.get('id')!r} {issue} must characterize "
                f"{WATCH_RECOVERY_KEY} with exact comparison"
            )
        expected = record.get("expected")
        _validate_watch_recovery_output(
            expected, record, f"case {record.get('id')!r} expected"
        )
        return expected
    if record.get("function") != HEART_RATE_RECOVERY_KEY or record.get("comparison") != "exact":
        raise ParityFormatError(
            f"case {record.get('id')!r} bhelm/noop#55 must characterize "
            f"{HEART_RATE_RECOVERY_KEY} with exact comparison"
        )
    expected = record.get("expected")
    keys = {"endHR", "after1Minute", "after2Minutes", "after5Minutes"}
    if not isinstance(expected, dict) or set(expected) != keys:
        raise ParityFormatError(
            f"case {record.get('id')!r} bhelm/noop#55 expected must contain exactly {sorted(keys)}"
        )
    if not _is_signed_integer(expected["endHR"], INT32_MIN, INT32_MAX):
        raise ParityFormatError(
            f"case {record.get('id')!r} bhelm/noop#55 expected.endHR must fit Int32"
        )
    for key in ("after1Minute", "after2Minutes", "after5Minutes"):
        value = expected[key]
        if value is not None and not _is_signed_integer(value, INT32_MIN, INT32_MAX):
            raise ParityFormatError(
                f"case {record.get('id')!r} bhelm/noop#55 expected.{key} must be null or fit Int32"
            )
    return expected


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
        _validate_function_output(
            expected, comparison, swift_kind, swift_value, f"swift id={case_id} valueBits"
        )
        _validate_function_output(
            expected, comparison, kotlin_kind, kotlin_value, f"kotlin id={case_id} valueBits"
        )
        known_expected = _known_behavior_expected(expected)
        if known_expected is not None:
            known_issue = expected["knownBehaviorIssue"]
            for side, kind, value in (
                ("swift", swift_kind, swift_value),
                ("kotlin", kotlin_kind, kotlin_value),
            ):
                if kind != "value" or value != known_expected:
                    diffs.append(
                        f"KNOWN_BEHAVIOR id={case_id} function={function} "
                        f"known_behavior={known_issue} side={side} "
                        f"expected=bits:{known_expected} "
                        f"actual={_render_payload(kind, value, comparison)}"
                    )
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


def _seeded_recovery_forecast_cases() -> list[dict[str, Any]]:
    """Return the module-local Forecast corpus, independent of earlier seed consumers."""

    rng = SplitMix64(RECOVERY_FORECAST_SEED)
    records: list[dict[str, Any]] = []
    seeded_forecasts = (
        (
            "splitmix64",
            [40.0 + float(rng.bounded(41)) for _ in range(14)],
            [20.0 + float(rng.bounded(61)) for _ in range(14)],
            20.0 + float(rng.bounded(61)),
            5.0 + float(rng.bounded(40)) / 10.0,
        ),
        (
            "affine",
            [44.0 + index * 2.25 for index in range(14)],
            [30.0 + index * 1.5 for index in range(14)],
            63.0,
            7.75,
        ),
    )
    for strategy, charges, efforts, today, planned in seeded_forecasts:
        source = f"seeded:{strategy}:{RECOVERY_FORECAST_SEED:#018x}"
        records.extend(
            (
                {
                    "args": {
                        "recentCharge": charges,
                        "recentEffort": efforts,
                        "todayEffort": today,
                        "plannedSleepHours": planned,
                        "needHours": 8.0,
                        "needNights": 7,
                        "useDefaults": False,
                    },
                    "comparison": "exact",
                    "function": RECOVERY_FORECAST_KEY,
                    "id": f"seeded_recovery_forecast_{strategy}",
                    "source": source,
                },
                {
                    "args": {"values": charges}, "comparison": "epsilon",
                    "function": "RecoveryForecaster.mean/1",
                    "id": f"seeded_recovery_forecast_mean_{strategy}", "source": source,
                },
                {
                    "args": {"values": charges}, "comparison": "epsilon",
                    "function": "RecoveryForecaster.sampleSD/1",
                    "id": f"seeded_recovery_forecast_sample_sd_{strategy}", "source": source,
                },
                {
                    "args": {"values": charges}, "comparison": "epsilon",
                    "function": "RecoveryForecaster.leastSquaresSlope/1",
                    "id": f"seeded_recovery_forecast_slope_{strategy}", "source": source,
                },
                {
                    "args": {"x": today, "lo": 25.0, "hi": 75.0}, "comparison": "exact",
                    "function": "RecoveryForecaster.clamp/3",
                    "id": f"seeded_recovery_forecast_clamp_{strategy}", "source": source,
                },
            )
        )
    return records


def _seeded_watch_recovery_cases() -> list[dict[str, Any]]:
    """Exactly two structured WatchRecovery seeds, isolated from every other module."""

    rng = SplitMix64(WATCH_RECOVERY_SEED)
    random_hrv = [35.0 + float(rng.bounded(31)) for _ in range(14)]
    random_rhr = [45.0 + float(rng.bounded(21)) for _ in range(14)]
    return [
        {
            "args": {
                "todayHrv": 35.0 + float(rng.bounded(31)),
                "todayRhr": 45 + rng.bounded(21),
                "hrvHistory": random_hrv,
                "rhrHistory": random_rhr,
            },
            "comparison": "exact",
            "function": WATCH_RECOVERY_KEY,
            "id": "seeded_watch_recovery_splitmix64",
            "source": f"seeded:watch-recovery:splitmix64:{WATCH_RECOVERY_SEED:#018x}",
        },
        {
            "args": {
                "todayHrv": 52.0,
                "todayRhr": None,
                "hrvHistory": [38.0 + index * 2.25 for index in range(14)],
                "rhrHistory": [61.0 - index * 0.75 for index in range(14)],
            },
            "comparison": "exact",
            "function": WATCH_RECOVERY_KEY,
            "id": "seeded_watch_recovery_affine",
            "source": f"seeded:watch-recovery:affine:{WATCH_RECOVERY_SEED:#018x}",
        },
    ]


def _seeded_sleep_cases() -> list[dict[str, Any]]:
    """Two function-local deterministic streams per Sleep helper; never share RNG state."""

    records: list[dict[str, Any]] = []
    for function, seeds in SLEEP_SEEDS.items():
        for index, seed in enumerate(seeds):
            rng = SplitMix64(seed)
            source = f"seeded:splitmix64:{seed:#018x}"
            if function == SLEEP_CREDIT_KEY:
                args = {
                    "mainSleepMin": 300.0 + rng.bounded(241) / 2.0,
                    "napSleepMin": (-30.0 if index else 0.0) + rng.bounded(121) / 2.0,
                    "useDefaults": False,
                }
            elif function == SLEEP_LEDGER_KEY:
                args = {
                    "series": [
                        {"day": f"2026-08-{day:02d}", "totalSleepMin": (
                            None if day % 5 == 0 else 300.0 + rng.bounded(241) / 2.0
                        )}
                        for day in range(1, 9)
                    ],
                    "needHours": 7.0 + rng.bounded(9) / 4.0,
                    "window": 2 + rng.bounded(7),
                    "useDefaults": False,
                }
            elif function == SLEEP_AUTO_BED_KEY:
                candidate = 1_775_081_600 + rng.bounded(3_600)
                args = {
                    "previousBed": candidate - rng.bounded(1_800),
                    "candidateBed": candidate,
                    "originalWake": candidate - 1_800 if index else None,
                    "now": candidate - (600 if index == 0 else -600),
                    "zone": "UTC",
                    "useDefaults": False,
                }
            elif function == SLEEP_DISJOINT_KEY:
                start = 10_000 + rng.bounded(1_000)
                args = {
                    "newStart": start, "newEnd": start + 600 + rng.bounded(600),
                    "coverageStart": start + (-1200 if index == 0 else 1200),
                    "coverageEnd": start + (-1 if index == 0 else 1800),
                }
            elif function == SLEEP_CLAMP_KEY:
                start = 20_000 + rng.bounded(1_000)
                args = {
                    "start": start, "end": start + 1_800 + rng.bounded(3_600),
                    "now": start + 2_400, "slackSec": 60 + rng.bounded(600),
                    "useDefaults": False,
                }
            elif function == SLEEP_WAKE_KEY:
                pad = " \t"[rng.bounded(2)]
                args = {"stage": f"{pad}{'WAKE' if index == 0 else 'awake'}{pad}"}
            else:
                start = 30_000 + rng.bounded(1_000)
                args = {
                    "stagesJSON": (
                        f'[{ {"start": start, "end": start + 1200, "stage": "light"} }]'.replace("'", '"')
                        if index == 0 else
                        '{"awake":15.0,"light":240.0,"deep":60.0,"rem":75.0}'
                    ),
                    "sessionStart": start, "oldEnd": start + 3_600,
                    "newStart": start + 300, "newEnd": start + 3_900,
                }
            records.append({
                "args": args, "comparison": "exact", "function": function,
                "id": f"seeded_sleep_{function.split('.')[1].split('/')[0]}_{index:02d}",
                "source": source,
            })
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
    charge_rng = SplitMix64(GENERATOR_SEED)
    charge_rng.state = rng.state
    for index in range(2):
        workout_end = 80_000 + index * 1_000
        samples = [
            {"ts": workout_end - 120 + offset * 10, "bpm": 145 + (rng.bounded(3) - 1)}
            for offset in range(13)
        ]
        samples.extend(
            {"ts": workout_end + 59 + offset, "bpm": 118 + rng.bounded(9)}
            for offset in range(3)
        )
        if index:
            samples = list(reversed(samples)) + [dict(samples[-1])]
        records.append(
            {
                "args": {
                    "samples": samples,
                    "workoutStart": workout_end - 300,
                    "workoutEnd": workout_end,
                    "maxHR": 200.0,
                },
                "comparison": "exact",
                "function": HEART_RATE_RECOVERY_KEY,
                "id": f"seeded_heart_rate_recovery_{index:02d}",
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
        args = {
            "hrv": 44.0 + charge_rng.bounded(17),
            "hrvBaseline": baseline,
            "resp": 13.0 + charge_rng.bounded(5) / 2.0,
            "respBaseline": {
                "baseline": 15.0,
                "nValid": 12,
                "nightsSinceUpdate": round_index,
                "spread": 1.5,
                "status": "trusted",
            },
            "rhr": 48.0 + charge_rng.bounded(17),
            "rhrBaseline": {
                "baseline": 58.0,
                "nValid": 12,
                "nightsSinceUpdate": round_index,
                "spread": 4.0,
                "status": "trusted",
            },
            "sleepPerf": 0.72 + round_index * 0.18,
            "useDefaults": round_index == 0,
        }
        if round_index != 0:
            args["skinTempDev"] = -0.35
        record = {
            "args": args,
            "comparison": "exact",
            "function": RECOVERY_DRIVERS_KEY,
            "id": f"seeded_recovery_drivers_{round_index:02d}",
            "source": f"seeded:splitmix64:{GENERATOR_SEED:#018x}",
        }
        if round_index == 1:
            record["acceptanceIssue"] = "bhelm/noop#52"
            record["expectedRows"] = [
                {"baselineText": {"text": "51 ms baseline"}, "deltaPoints": -17,
                 "label": {"text": "Heart rate variability"}, "valueText": {"text": "46 ms"},
                 "verdict": {"text": "below baseline, limiting recovery"}},
                {"baselineText": {"text": ""}, "deltaPoints": 2,
                 "label": {"text": "Sleep quality"}, "valueText": {"text": "90%"},
                 "verdict": {"text": "a strong night, supporting recovery"}},
                {"baselineText": {"text": "15.0 br/min baseline"}, "deltaPoints": 1,
                 "label": {"text": "Respiratory rate"}, "valueText": {"text": "14.0 br/min"},
                 "verdict": {"text": "below baseline, supporting recovery"}},
                {"baselineText": {"text": ""}, "deltaPoints": -1,
                 "label": {"text": "Skin temperature"}, "valueText": {"text": "-0.4 C vs baseline"},
                 "verdict": {"text": "cooler than baseline, limiting recovery"}},
                {"baselineText": {"text": "58 bpm baseline"}, "deltaPoints": 0,
                 "label": {"text": "Resting heart rate"}, "valueText": {"text": "58 bpm"},
                 "verdict": {"text": "at baseline"}},
            ]
        records.append(record)
    records.extend(_seeded_recovery_forecast_cases())
    records.extend(_seeded_watch_recovery_cases())
    records.extend(_seeded_sleep_cases())
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


def _validate_watch_recovery_args(args: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Validate transport safety only; production owns physiological rejection semantics."""

    function = WATCH_RECOVERY_KEY
    fields = {"todayHrv", "todayRhr", "hrvHistory", "rhrHistory"}
    if not isinstance(args, dict) or set(args) != fields:
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} args must contain exactly {sorted(fields)}"
        )
    today_hrv = args["todayHrv"]
    if today_hrv is not None and not (
        _is_number(today_hrv)
        and math.isfinite(float(today_hrv))
        and abs(float(today_hrv)) <= WATCH_RECOVERY_ABS_MAX
    ):
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} todayHrv must be null or finite and bounded"
        )
    today_rhr = args["todayRhr"]
    if today_rhr is not None and not _is_signed_integer(today_rhr, INT32_MIN, INT32_MAX):
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} todayRhr must be null or fit Int32"
        )
    for key in ("hrvHistory", "rhrHistory"):
        values = args[key]
        if not isinstance(values, list) or len(values) > WATCH_RECOVERY_MAX_HISTORY or not all(
            _is_number(value)
            and math.isfinite(float(value))
            and abs(float(value)) <= WATCH_RECOVERY_ABS_MAX
            for value in values
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} must contain at most "
                f"{WATCH_RECOVERY_MAX_HISTORY} finite bounded numbers"
            )
    return dict(args)


def _validate_heart_rate_recovery(args: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    function = HEART_RATE_RECOVERY_KEY
    allowed = {"samples", "workoutStart", "workoutEnd", "maxHR"}
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ParityFormatError(f"case {record.get('id')!r} {function} has unknown fields {unknown}")
    if set(args) != allowed:
        raise ParityFormatError(f"case {record.get('id')!r} {function} requires exactly {sorted(allowed)}")
    samples = args["samples"]
    if not isinstance(samples, list) or len(samples) > 4096:
        raise ParityFormatError(f"case {record.get('id')!r} {function} samples must be a bounded array")
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != {"ts", "bpm"}:
            raise ParityFormatError(f"case {record.get('id')!r} {function} samples[{index}] has invalid keys")
        if not _is_signed_integer(sample["ts"], INT64_MIN, INT64_MAX):
            raise ParityFormatError(f"case {record.get('id')!r} {function} sample timestamp must fit Int64")
        if not _is_signed_integer(sample["bpm"], INT32_MIN, INT32_MAX):
            raise ParityFormatError(f"case {record.get('id')!r} {function} sample bpm must fit Int32")
    start, end, max_hr = args["workoutStart"], args["workoutEnd"], args["maxHR"]
    if not _is_signed_integer(start, INT64_MIN, INT64_MAX) or not _is_signed_integer(
        end, INT64_MIN, INT64_MAX
    ):
        raise ParityFormatError(f"case {record.get('id')!r} {function} workout bounds must fit Int64")
    if start <= 0 or end <= start:
        raise ParityFormatError(f"case {record.get('id')!r} {function} requires 0 < workoutStart < workoutEnd")
    if not _is_number(max_hr) or not math.isfinite(max_hr) or not 30.0 <= max_hr <= 250.0:
        raise ParityFormatError(f"case {record.get('id')!r} {function} maxHR is outside [30, 250]")
    _checked_subtract(end, start, record, "workout duration subtraction")
    _checked_subtract(end, 300, record, "lookback subtraction")
    _checked_subtract(end, 30, record, "cessation subtraction")
    upper = _checked_add(end, 315, record, "upper-bound addition")
    targets = [_checked_add(end, offset, record, "target addition") for offset in (60, 120, 300)]
    for sample in samples:
        for target in targets:
            delta = _checked_subtract(sample["ts"], target, record, "target subtraction")
            if delta == INT64_MIN:
                raise ParityFormatError(
                    f"case {record.get('id')!r} {function} absolute target difference overflows Int64"
                )
        _checked_subtract(sample["ts"], start, record, "sample/start subtraction")
        _checked_subtract(upper, sample["ts"], record, "upper/sample subtraction")
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


def _validate_recovery_drivers_args(args: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Fail closed around the finite physiological domain of the public driver adapter."""

    function = RECOVERY_DRIVERS_KEY
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
            f"case {record.get('id')!r} {function} seven-argument call cannot provide arg8"
        )
    if not use_defaults and "skinTempDev" not in args:
        raise ParityFormatError(
            f"case {record.get('id')!r} {function} explicit eight-argument call requires arg8"
        )

    def scalar(key: str, minimum: float, maximum: float, optional: bool = False) -> float | None:
        value = args.get(key)
        if optional and value is None:
            return None
        if not _is_number(value) or not minimum <= value <= maximum or not math.isfinite(float(value)):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} is outside [{minimum}, {maximum}]"
            )
        if value == 0.0 and math.copysign(1.0, value) < 0:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} must not be negative zero"
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
        required_fields = {"baseline", "spread", "nValid", "nightsSinceUpdate", "status"}
        if not isinstance(value, dict) or set(value) != required_fields:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key} must be a complete baseline state"
            )
        center = value.get("baseline")
        spread = value.get("spread")
        if not _is_number(center) or not minimum <= center <= maximum or not math.isfinite(float(center)):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} {key}.baseline is out of bounds"
            )
        if not _is_number(spread) or not 0.001 <= spread <= 1_000.0 or not math.isfinite(float(spread)):
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
    effective = dict(args)
    effective.update(
        {
            "hrv": hrv,
            "rhr": rhr,
            "resp": resp,
            "hrvBaseline": hrv_base,
            "rhrBaseline": rhr_base,
            "respBaseline": resp_base,
            "sleepPerf": sleep,
            "skinTempDev": skin,
        }
    )
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


def _sleep_int(value: Any, record: dict[str, Any], label: str) -> int:
    if not _is_signed_integer(value, -1_000_000_000_000, 1_000_000_000_000):
        raise ParityFormatError(
            f"case {record.get('id')!r} {record.get('function')} {label} must be a safe signed timestamp"
        )
    return value


def _validate_sleep_args(args: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Validate the deliberately bounded, platform-neutral Sleep differential domain."""

    function = record.get("function")
    case_id = record.get("id")
    if function == SLEEP_CREDIT_KEY:
        allowed = {"mainSleepMin", "napSleepMin", "useDefaults"}
        if set(args) - allowed or not {"mainSleepMin", "useDefaults"} <= set(args):
            raise ParityFormatError(f"case {case_id!r} {function} has invalid fields")
        if args["mainSleepMin"] is not None and not (
            _is_number(args["mainSleepMin"]) and -1_440.0 <= args["mainSleepMin"] <= 1_440.0
        ):
            raise ParityFormatError(f"case {case_id!r} {function} mainSleepMin is outside [-1440, 1440]")
        use_defaults = args["useDefaults"]
        if not isinstance(use_defaults, bool):
            raise ParityFormatError(f"case {case_id!r} {function} useDefaults must be boolean")
        if use_defaults and "napSleepMin" in args:
            raise ParityFormatError(f"case {case_id!r} {function} bare call cannot override arg2")
        nap = args.get("napSleepMin", 0.0)
        if not _is_number(nap) or not -1_440.0 <= nap <= 1_440.0:
            raise ParityFormatError(f"case {case_id!r} {function} napSleepMin is outside [-1440, 1440]")
        return {"mainSleepMin": args["mainSleepMin"], "napSleepMin": nap, "useDefaults": use_defaults}
    if function == SLEEP_LEDGER_KEY:
        allowed = {"series", "needHours", "window", "useDefaults"}
        if set(args) - allowed or not {"series", "useDefaults"} <= set(args):
            raise ParityFormatError(f"case {case_id!r} {function} has invalid fields")
        series = args["series"]
        if not isinstance(series, list) or len(series) > SLEEP_MAX_ROWS:
            raise ParityFormatError(f"case {case_id!r} {function} series must be a bounded array")
        for index, row in enumerate(series):
            if not isinstance(row, dict) or set(row) != {"day", "totalSleepMin"}:
                raise ParityFormatError(f"case {case_id!r} {function} series[{index}] has invalid fields")
            if not isinstance(row["day"], str) or not (1 <= len(row["day"].encode("utf-8")) <= 64):
                raise ParityFormatError(f"case {case_id!r} {function} series[{index}].day is invalid")
            slept = row["totalSleepMin"]
            if slept is not None and not (_is_number(slept) and -1_440.0 <= slept <= 1_440.0):
                raise ParityFormatError(f"case {case_id!r} {function} series[{index}].totalSleepMin is invalid")
        use_defaults = args["useDefaults"]
        if not isinstance(use_defaults, bool):
            raise ParityFormatError(f"case {case_id!r} {function} useDefaults must be boolean")
        if use_defaults and ({"needHours", "window"} & set(args)):
            raise ParityFormatError(f"case {case_id!r} {function} bare call cannot override defaults")
        need = args.get("needHours", SLEEP_DEFAULT_NEED_HOURS)
        window = args.get("window", SLEEP_DEFAULT_WINDOW)
        if not _is_number(need) or not -24.0 <= need <= 24.0:
            raise ParityFormatError(f"case {case_id!r} {function} needHours is outside [-24, 24]")
        if not _is_signed_integer(window, -SLEEP_MAX_ROWS, SLEEP_MAX_ROWS):
            raise ParityFormatError(f"case {case_id!r} {function} window is outside the bounded domain")
        return {"series": series, "needHours": need, "window": window, "useDefaults": use_defaults}
    if function == SLEEP_AUTO_BED_KEY:
        allowed = {"previousBed", "candidateBed", "originalWake", "now", "zone", "useDefaults"}
        if set(args) - allowed or not {"previousBed", "candidateBed", "originalWake", "now", "useDefaults"} <= set(args):
            raise ParityFormatError(f"case {case_id!r} {function} has invalid fields")
        for key in ("previousBed", "candidateBed", "now"):
            _sleep_int(args[key], record, key)
        if args["originalWake"] is not None:
            _sleep_int(args["originalWake"], record, "originalWake")
        use_defaults = args["useDefaults"]
        if not isinstance(use_defaults, bool):
            raise ParityFormatError(f"case {case_id!r} {function} useDefaults must be boolean")
        if use_defaults and "zone" in args:
            raise ParityFormatError(f"case {case_id!r} {function} bare call cannot override arg5")
        zone = args.get("zone", "system")
        if zone not in {"UTC", "system"}:
            raise ParityFormatError(f"case {case_id!r} {function} zone must be UTC or system")
        return {**args, "zone": zone}
    if function == SLEEP_DISJOINT_KEY:
        if set(args) != {"newStart", "newEnd", "coverageStart", "coverageEnd"}:
            raise ParityFormatError(f"case {case_id!r} {function} requires exactly four bounds")
        for key in args:
            _sleep_int(args[key], record, key)
        return dict(args)
    if function == SLEEP_CLAMP_KEY:
        allowed = {"start", "end", "now", "slackSec", "useDefaults"}
        if set(args) - allowed or not {"start", "end", "now", "useDefaults"} <= set(args):
            raise ParityFormatError(f"case {case_id!r} {function} has invalid fields")
        for key in ("start", "end", "now"):
            _sleep_int(args[key], record, key)
        use_defaults = args["useDefaults"]
        if not isinstance(use_defaults, bool):
            raise ParityFormatError(f"case {case_id!r} {function} useDefaults must be boolean")
        if use_defaults and "slackSec" in args:
            raise ParityFormatError(f"case {case_id!r} {function} bare call cannot override arg4")
        slack = args.get("slackSec", SLEEP_DEFAULT_SLACK_SEC)
        if not _is_signed_integer(slack, -86_400, 86_400):
            raise ParityFormatError(f"case {case_id!r} {function} slackSec is outside [-86400, 86400]")
        _checked_add(args["now"], slack, record, "now + slackSec")
        _checked_subtract(args["end"], args["start"], record, "end - start")
        return {**args, "slackSec": slack}
    if function == SLEEP_WAKE_KEY:
        if set(args) != {"stage"} or not isinstance(args["stage"], str):
            raise ParityFormatError(f"case {case_id!r} {function} requires stage text")
        if len(args["stage"].encode("utf-8")) > 256 or any(ord(ch) > 127 for ch in args["stage"]):
            raise ParityFormatError(f"case {case_id!r} {function} stage must be bounded ASCII")
        return dict(args)
    if function == SLEEP_RECLIP_KEY:
        required = {"stagesJSON", "sessionStart", "oldEnd", "newStart", "newEnd"}
        if set(args) != required:
            raise ParityFormatError(f"case {case_id!r} {function} requires exactly {sorted(required)}")
        for key in required - {"stagesJSON"}:
            _sleep_int(args[key], record, key)
        if args["newEnd"] <= args["newStart"] or args["oldEnd"] < args["sessionStart"]:
            raise ParityFormatError(f"case {case_id!r} {function} requires valid old/new windows")
        _checked_subtract(args["newEnd"], args["newStart"], record, "newEnd - newStart")
        _checked_subtract(args["oldEnd"], args["sessionStart"], record, "oldEnd - sessionStart")
        stages = args["stagesJSON"]
        if stages is None:
            return dict(args)
        if not isinstance(stages, str) or len(stages.encode("utf-8")) > SLEEP_MAX_TEXT_BYTES:
            raise ParityFormatError(f"case {case_id!r} {function} stagesJSON must be bounded UTF-8 text or null")
        try:
            parsed = json.loads(stages)
        except json.JSONDecodeError as exc:
            raise ParityFormatError(f"case {case_id!r} {function} stagesJSON is invalid JSON: {exc}") from exc
        if isinstance(parsed, list):
            if len(parsed) > SLEEP_MAX_ROWS:
                raise ParityFormatError(f"case {case_id!r} {function} segment array is too large")
            for index, segment in enumerate(parsed):
                if not isinstance(segment, dict) or set(segment) != {"start", "end", "stage"}:
                    raise ParityFormatError(f"case {case_id!r} {function} segment[{index}] has invalid fields")
                _sleep_int(segment["start"], record, f"segment[{index}].start")
                _sleep_int(segment["end"], record, f"segment[{index}].end")
                if not isinstance(segment["stage"], str) or len(segment["stage"].encode("utf-8")) > 64:
                    raise ParityFormatError(f"case {case_id!r} {function} segment[{index}].stage is invalid")
                malformed = (
                    segment["start"] < 0
                    or segment["end"] <= segment["start"]
                    or not segment["stage"]
                )
                expected_fixture = SLEEP_REGRESSION_FIXTURES.get(str(case_id))
                if malformed and not (
                    expected_fixture is not None
                    and expected_fixture["function"] == function
                    and expected_fixture["args"] == args
                    and expected_fixture["regressionIssue"] == record.get("regressionIssue")
                ):
                    raise ParityFormatError(
                        f"case {case_id!r} {function} has a malformed segment outside an exact regression fixture"
                    )
        elif isinstance(parsed, dict):
            if set(parsed) - {"awake", "light", "deep", "rem"}:
                raise ParityFormatError(f"case {case_id!r} {function} minute dictionary has unknown fields")
            if not all(_is_number(value) and 0.0 <= value <= 1_440.0 for value in parsed.values()):
                raise ParityFormatError(f"case {case_id!r} {function} minute dictionary values are invalid")
        else:
            raise ParityFormatError(f"case {case_id!r} {function} stagesJSON must contain an array or object")
        return dict(args)
    raise AssertionError(f"unhandled Sleep function {function}")


def _effective_args(record: dict[str, Any]) -> dict[str, Any]:
    args = record.get("args")
    if not isinstance(args, dict):
        raise ParityFormatError(f"case {record.get('id')!r} args must be an object")
    function = record.get("function")
    if function in SLEEP_FUNCTIONS:
        return _validate_sleep_args(args, record)
    if function == RECOVERY_FORECAST_KEY:
        allowed = {
            "characterizeForecastConstants", "needHours", "needNights",
            "plannedSleepHours", "recentCharge", "recentEffort", "todayEffort",
            "useDefaults",
        }
        if set(args) - allowed:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} contains unsupported arguments"
            )
        required = {"recentCharge", "todayEffort", "plannedSleepHours", "useDefaults"}
        if not required <= set(args):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} is missing required arguments"
            )
        if not isinstance(args["recentCharge"], list) or not (
            len(args["recentCharge"]) <= RECOVERY_FORECAST_MAX_VALUES
            and all(
                _is_number(value) and 0.0 <= value <= 100.0
                for value in args["recentCharge"]
            )
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} recentCharge must contain at most "
                f"{RECOVERY_FORECAST_MAX_VALUES} values in [0, 100]"
            )
        if args["todayEffort"] is not None and not (
            _is_number(args["todayEffort"]) and 0.0 <= args["todayEffort"] <= 100.0
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} todayEffort must be null or in [0, 100]"
            )
        if not (
            _is_number(args["plannedSleepHours"])
            and -RECOVERY_FORECAST_SLEEP_HOURS_MAX
            <= args["plannedSleepHours"]
            <= RECOVERY_FORECAST_SLEEP_HOURS_MAX
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} plannedSleepHours must be in "
                f"[-{RECOVERY_FORECAST_SLEEP_HOURS_MAX:g}, "
                f"{RECOVERY_FORECAST_SLEEP_HOURS_MAX:g}]"
            )
        use_defaults = args["useDefaults"]
        controls = {"recentEffort", "needHours", "needNights"}
        if not isinstance(use_defaults, bool):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} useDefaults must be boolean"
            )
        if use_defaults and controls & set(args):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} bare calls cannot override defaults"
            )
        if not use_defaults and not controls <= set(args):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} explicit calls require every control"
            )
        effort = args.get("recentEffort", [])
        need = args.get("needHours")
        need_nights = args.get("needNights", 0)
        if not isinstance(effort, list) or not (
            len(effort) <= RECOVERY_FORECAST_MAX_VALUES
            and all(_is_number(value) and 0.0 <= value <= 100.0 for value in effort)
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} recentEffort must contain at most "
                f"{RECOVERY_FORECAST_MAX_VALUES} values in [0, 100]"
            )
        if need is not None and not (
            _is_number(need) and 0.0 <= need <= RECOVERY_FORECAST_SLEEP_HOURS_MAX
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} needHours must be null or in "
                f"[0, {RECOVERY_FORECAST_SLEEP_HOURS_MAX:g}]"
            )
        if not _is_signed_integer(need_nights, 0, INT32_MAX):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} needNights must fit non-negative Int32"
            )
        characterize = args.get("characterizeForecastConstants", False)
        if not isinstance(characterize, bool):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} characterizeForecastConstants must be boolean"
            )
        return {
            "characterizeForecastConstants": characterize,
            "needHours": need,
            "needNights": need_nights,
            "plannedSleepHours": args["plannedSleepHours"],
            "recentCharge": args["recentCharge"],
            "recentEffort": effort,
            "todayEffort": args["todayEffort"],
            "useDefaults": use_defaults,
        }
    if function in RECOVERY_FORECAST_FUNCTIONS[1:4]:
        if set(args) != {"values"} or not isinstance(args["values"], list) or not (
            len(args["values"]) <= RECOVERY_FORECAST_MAX_VALUES
            and all(
                _is_number(value) and abs(value) <= RECOVERY_FORECAST_HELPER_ABS_MAX
                for value in args["values"]
            )
        ):
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires at most "
                f"{RECOVERY_FORECAST_MAX_VALUES} values with absolute magnitude <= "
                f"{RECOVERY_FORECAST_HELPER_ABS_MAX:g}"
            )
        return dict(args)
    if function == "RecoveryForecaster.clamp/3":
        if set(args) != {"x", "lo", "hi"} or not all(
            _is_number(args.get(key)) for key in ("x", "lo", "hi")
        ) or args["lo"] > args["hi"]:
            raise ParityFormatError(
                f"case {record.get('id')!r} {function} requires numeric x and ordered lo/hi"
            )
        return dict(args)
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
    if function == HEART_RATE_RECOVERY_KEY:
        return _validate_heart_rate_recovery(args, record)
    if function == RECOVERY_DRIVERS_KEY:
        return _validate_recovery_drivers_args(args, record)
    if function == WATCH_RECOVERY_KEY:
        return _validate_watch_recovery_args(args, record)
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
                "args": {"stage": "wake"},
                "comparison": "exact",
                "function": SLEEP_WAKE_KEY,
                "id": "sleep_negative_source_probe",
                "source": "negative-control",
            },
            {
                "args": {"mainSleepMin": 420.0, "napSleepMin": 30.0, "useDefaults": False},
                "comparison": "exact",
                "function": SLEEP_CREDIT_KEY,
                "id": "sleep_negative_output_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "samples": [
                        *({"ts": 880 + index * 10, "bpm": 145} for index in range(13)),
                        {"ts": 1059, "bpm": 120}, {"ts": 1060, "bpm": 120},
                        {"ts": 1061, "bpm": 120},
                    ],
                    "workoutStart": 500, "workoutEnd": 1000, "maxHR": 200.0,
                },
                "comparison": "exact",
                "function": HEART_RATE_RECOVERY_KEY,
                "id": "heart_rate_recovery_negative_probe",
                "source": "negative-control",
            },
            {
                "args": {"values": [2.0, 4.0, 6.0]},
                "comparison": "epsilon",
                "function": "RecoveryForecaster.mean/1",
                "id": "recovery_forecast_negative_source_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "recentCharge": [50.0] * 10,
                    "recentEffort": [],
                    "todayEffort": None,
                    "plannedSleepHours": 8.0,
                    "needHours": 8.0,
                    "needNights": 7,
                    "useDefaults": False,
                },
                "comparison": "exact",
                "function": RECOVERY_FORECAST_KEY,
                "id": "recovery_forecast_negative_output_probe",
                "source": "negative-control",
            },
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
            {
                "args": {
                    "hrv": 55.0, "rhr": 60.0,
                    "hrvBaseline": {"baseline": 50.0, "spread": 5.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "useDefaults": True,
                },
                "comparison": "exact",
                "function": RECOVERY_DRIVERS_KEY,
                "id": "recovery_drivers_negative_delta_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "hrv": 50.0, "rhr": 60.0,
                    "hrvBaseline": {"baseline": 50.0, "spread": 5.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "rhrBaseline": {"baseline": 60.0, "spread": 5.0, "nValid": 14,
                                    "nightsSinceUpdate": 0, "status": "trusted"},
                    "useDefaults": True,
                },
                "comparison": "exact",
                "function": RECOVERY_DRIVERS_KEY,
                "id": "recovery_drivers_negative_order_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "todayHrv": 45.0, "todayRhr": 52,
                    "hrvHistory": [45.0] * 14, "rhrHistory": [52.0] * 14,
                },
                "comparison": "exact",
                "function": WATCH_RECOVERY_KEY,
                "id": "watch_recovery_negative_score_probe",
                "source": "negative-control",
            },
            {
                "args": {
                    "todayHrv": 45.0, "todayRhr": 52,
                    "hrvHistory": [45.0] * 14, "rhrHistory": [52.0] * 14,
                },
                "comparison": "exact",
                "function": WATCH_RECOVERY_KEY,
                "id": "watch_recovery_negative_confidence_probe",
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
        supported_known_issues = {
            "bhelm/noop#10", "bhelm/noop#12", "bhelm/noop#39", "bhelm/noop#40",
            "bhelm/noop#55", "bhelm/noop#61", "bhelm/noop#62",
        }
        if known_issue is not None and known_issue not in supported_known_issues:
            raise ParityFormatError(
                f"case {case_id!r} has unsupported knownBehaviorIssue {known_issue!r}"
            )
        acceptance_issue = record.get("acceptanceIssue")
        expected_acceptance_issue = {
            "recovery_drivers_issue_51_negative_half_tie": "bhelm/noop#51",
            "seeded_recovery_drivers_01": "bhelm/noop#52",
        }.get(case_id)
        if expected_acceptance_issue is not None:
            if acceptance_issue != expected_acceptance_issue:
                raise ParityFormatError(
                    f"case {case_id!r} must reference acceptanceIssue {expected_acceptance_issue}"
                )
            if record.get("function") != RECOVERY_DRIVERS_KEY or "expectedRows" not in record:
                raise ParityFormatError(
                    f"case {case_id!r} must carry exact Charge-driver expectedRows"
                )
            _validate_recovery_drivers_rows(
                record["expectedRows"], f"case {case_id!r} expectedRows"
            )
        elif acceptance_issue is not None or "expectedRows" in record:
            raise ParityFormatError(
                f"case {case_id!r} has unsupported issue-linked expectedRows metadata"
            )
        issue_56_cases = {
            "forecast_clamp_issue_56_positive_x_negative_lower_zero",
            "forecast_clamp_issue_56_negative_x_positive_lower_zero",
            "forecast_clamp_issue_56_positive_x_negative_upper_zero",
            "forecast_clamp_issue_56_negative_x_positive_upper_zero",
        }
        regression_issue = record.get("regressionIssue")
        supported_regression_issues = {"bhelm/noop#41", "bhelm/noop#42", "bhelm/noop#56"}
        if regression_issue is not None and regression_issue not in supported_regression_issues:
            raise ParityFormatError(
                f"case {case_id!r} has unsupported regressionIssue {regression_issue!r}"
            )
        if (case_id in issue_56_cases) != (regression_issue == "bhelm/noop#56"):
            raise ParityFormatError(
                f"case {case_id!r} must bind the signed-zero regression to bhelm/noop#56"
            )
        sleep_fixture = SLEEP_REGRESSION_FIXTURES.get(case_id)
        if sleep_fixture is not None:
            if not (
                record.get("comparison") == "exact"
                and record.get("function") == sleep_fixture["function"]
                and record.get("args") == sleep_fixture["args"]
                and regression_issue == sleep_fixture["regressionIssue"]
            ):
                raise ParityFormatError(
                    f"case {case_id!r} must preserve its exact regression fixture and issue reference"
                )
        elif regression_issue in {"bhelm/noop#41", "bhelm/noop#42"}:
            raise ParityFormatError(
                f"case {case_id!r} may not reuse a Sleep regression issue outside its exact fixture"
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
        elif case_id == "heart_rate_recovery_disconnected_segments_issue_55":
            expected_recovery_issue = "bhelm/noop#55"
        elif case_id in {
            "watch_recovery_issue_61_present_rhr_empty_history",
            "watch_recovery_issue_61_present_rhr_unusable_history",
            "watch_recovery_issue_61_missing_rhr_control",
        }:
            expected_recovery_issue = "bhelm/noop#61"
        elif case_id == "watch_recovery_issue_62_raw_seven_valid_four":
            expected_recovery_issue = "bhelm/noop#62"
        if expected_recovery_issue is not None and known_issue != expected_recovery_issue:
            raise ParityFormatError(
                f"case {case_id!r} characterizes shared Recovery behavior tracked by {expected_recovery_issue}"
            )
        _known_behavior_expected(record)
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
