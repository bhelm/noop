import json
import math
import tempfile
import unittest
from pathlib import Path

from Tools import parity_diff


class ParityDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_jsonl(self, name, records):
        path = self.root / name
        path.write_text(
            "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def input_record(self, case_id="case", comparison="exact", nonce="nonce-1"):
        return {
            "args": {"trimp": 100.0},
            "comparison": comparison,
            "effectiveArgs": {"denominator": 7201.0, "trimp": 100.0},
            "function": "StrainScorer.trimpToStrain/2",
            "id": case_id,
            "nonce": nonce,
        }

    def output_record(self, case_id="case", nonce="nonce-1", **fields):
        record = {
            "comparison": fields.pop("comparison", "exact"),
            "function": "StrainScorer.trimpToStrain/2",
            "id": case_id,
            "nonce": nonce,
        }
        record.update(fields)
        return record

    def recovery_forecast_value(self, **overrides):
        value = {
            "score": "404e000000000000",
            "band": "4020000000000000",
            "baseline": "404e000000000000",
            "planned": "4020000000000000",
            "need": "401e000000000000",
            "nights": 10,
            "confidence": {"text": "solid"},
            "low": "404a000000000000",
            "high": "4051000000000000",
        }
        value.update(overrides)
        return value

    def compare(self, inputs, swift, kotlin):
        input_path = self.write_jsonl("input.jsonl", inputs)
        swift_path = self.write_jsonl("swift.jsonl", swift)
        kotlin_path = self.write_jsonl("kotlin.jsonl", kotlin)
        return parity_diff.compare_files(input_path, swift_path, kotlin_path)

    def test_id_sets_must_match_exactly(self):
        inputs = [self.input_record("a"), self.input_record("b")]
        swift = [self.output_record("a", valueBits="3ff0000000000000")]
        kotlin = [
            self.output_record("a", valueBits="3ff0000000000000"),
            self.output_record("b", valueBits="3ff0000000000000"),
        ]

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "ID set mismatch.*swift"):
            self.compare(inputs, swift, kotlin)

    def test_nonce_must_match_input_for_every_output(self):
        inputs = [self.input_record()]
        swift = [self.output_record(nonce="stale", valueBits="3ff0000000000000")]
        kotlin = [self.output_record(valueBits="3ff0000000000000")]

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "nonce mismatch.*swift.*case"):
            self.compare(inputs, swift, kotlin)

    def test_exact_class_reports_a_raw_bit_difference(self):
        inputs = [self.input_record()]
        swift = [self.output_record(valueBits="3ff0000000000000")]
        kotlin = [self.output_record(valueBits="3ff0000000000001")]

        diffs = self.compare(inputs, swift, kotlin)

        self.assertEqual(1, len(diffs))
        self.assertIn("id=case function=StrainScorer.trimpToStrain/2 class=exact", diffs[0])
        self.assertIn("swift=bits:3ff0000000000000", diffs[0])
        self.assertIn("kotlin=bits:3ff0000000000001", diffs[0])

    def test_epsilon_uses_absolute_and_relative_one_e_minus_nine_boundaries(self):
        inputs = [
            self.input_record("absolute", "epsilon"),
            self.input_record("relative", "epsilon"),
            self.input_record("outside", "epsilon"),
        ]
        swift = [
            self.output_record("absolute", comparison="epsilon", value=0.0),
            self.output_record("relative", comparison="epsilon", value=1_000_000_000.0),
            self.output_record("outside", comparison="epsilon", value=0.0),
        ]
        kotlin = [
            self.output_record("absolute", comparison="epsilon", value=1e-9),
            self.output_record("relative", comparison="epsilon", value=1_000_000_001.0),
            self.output_record("outside", comparison="epsilon", value=1.000001e-9),
        ]

        diffs = self.compare(inputs, swift, kotlin)

        self.assertEqual(1, len(diffs))
        self.assertIn("id=outside", diffs[0])

    def test_epsilon_rejects_non_finite_numbers(self):
        inputs = [self.input_record(comparison="epsilon")]
        swift = [self.output_record(comparison="epsilon", value=math.nan)]
        kotlin = [self.output_record(comparison="epsilon", value=0.0)]

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "finite"):
            self.compare(inputs, swift, kotlin)

    def test_exact_tree_rejects_unencoded_floating_values(self):
        inputs = [self.input_record()]
        swift = [self.output_record(valueBits={"rrMs": [800.0]})]
        kotlin = [self.output_record(valueBits={"rrMs": ["4089000000000000"]})]

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "encoded"):
            self.compare(inputs, swift, kotlin)

    def test_recovery_forecast_exact_schema_rejects_common_mode_missing_field(self):
        function = "RecoveryForecaster.forecast/6"
        input_record = self.input_record("forecast")
        input_record["function"] = function
        malformed = self.recovery_forecast_value()
        del malformed["low"]
        output = self.output_record("forecast", function=function, valueBits=malformed)

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "low"):
            self.compare([input_record], [output], [output])

    def test_recovery_forecast_exact_schema_rejects_common_mode_extra_field(self):
        function = "RecoveryForecaster.forecast/6"
        input_record = self.input_record("forecast")
        input_record["function"] = function
        malformed = self.recovery_forecast_value(unexpected="3ff0000000000000")
        output = self.output_record("forecast", function=function, valueBits=malformed)

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "unexpected"):
            self.compare([input_record], [output], [output])

    def test_recovery_forecast_exact_schema_rejects_common_mode_malformed_fields(self):
        function = "RecoveryForecaster.forecast/6"
        malformed_fields = {
            "score": 62,
            "nights": True,
            "confidence": {"text": "calibrating"},
        }
        for field, malformed_value in malformed_fields.items():
            input_record = self.input_record(f"forecast-{field}")
            input_record["function"] = function
            malformed = self.recovery_forecast_value(**{field: malformed_value})
            output = self.output_record(
                f"forecast-{field}", function=function, valueBits=malformed
            )

            with self.subTest(field=field), self.assertRaisesRegex(
                parity_diff.ParityFormatError, field
            ):
                self.compare([input_record], [output], [output])

    def test_recovery_forecast_exact_schema_accepts_null_and_valid_object(self):
        function = "RecoveryForecaster.forecast/6"
        inputs = [self.input_record("forecast-null"), self.input_record("forecast-value")]
        for input_record in inputs:
            input_record["function"] = function
        swift = [
            self.output_record("forecast-null", function=function, valueBits=None),
            self.output_record(
                "forecast-value", function=function, valueBits=self.recovery_forecast_value()
            ),
        ]

        self.assertEqual([], self.compare(inputs, swift, swift))

    def test_recovery_forecast_constants_schema_is_controlled_by_input_flag(self):
        function = "RecoveryForecaster.forecast/6"
        constants = {
            "baselineWindow": 14,
            "effortWindow": 14,
            "minBaselineNights": 5,
            "solidNeedNights": 7,
            "trustedNights": 10,
            "defaultNeedHours": "401e000000000000",
            "effortSpread": "4028000000000000",
            "minBandPoints": "4020000000000000",
            "reversionAdjCap": "4020000000000000",
            "reversionWeight": "3ff0000000000000",
            "sleepOverCap": "3fd0000000000000",
            "sleepWeight": "402c000000000000",
            "strainAdjCap": "4028000000000000",
            "strainWeight": "4022000000000000",
            "thinBandPoints": "4018000000000000",
        }
        characterized = self.input_record("forecast-constants")
        characterized["function"] = function
        characterized["args"]["characterizeForecastConstants"] = True
        characterized_output = self.output_record(
            "forecast-constants",
            function=function,
            valueBits=self.recovery_forecast_value(constants=constants),
        )
        self.assertEqual(
            [], self.compare([characterized], [characterized_output], [characterized_output])
        )

        missing = self.output_record(
            "forecast-constants", function=function, valueBits=self.recovery_forecast_value()
        )
        with self.assertRaisesRegex(parity_diff.ParityFormatError, "constants"):
            self.compare([characterized], [missing], [missing])

        ordinary = self.input_record("forecast")
        ordinary["function"] = function
        unexpected = self.output_record(
            "forecast", function=function, valueBits=self.recovery_forecast_value(constants=constants)
        )
        with self.assertRaisesRegex(parity_diff.ParityFormatError, "constants"):
            self.compare([ordinary], [unexpected], [unexpected])

    def test_pilot_generates_curated_and_seeded_cases_for_every_registered_hrv_function(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        functions = {case["function"] for case in cases}
        hrv_functions = {
            "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2",
            "HRVAnalyzer.median/1=HrvAnalyzer.median/1",
            "analyze/3",
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

        self.assertLessEqual(hrv_functions, functions)
        for function in hrv_functions:
            selected = [case for case in cases if case["function"] == function]
            self.assertTrue(any(case["source"].startswith("curated:") for case in selected), function)
            self.assertTrue(any(case["source"].startswith("seeded:") for case in selected), function)
        self.assertEqual(cases, parity_diff.generate_cases("pilot", "fixed-nonce"))

    def test_pilot_generates_curated_and_seeded_cases_for_every_registered_strain_function(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        strain_functions = {
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
            "StrainScorer.trimpToStrain/2",
            "StrainScorer.zoneWeight/3",
        }
        for function in strain_functions:
            selected = [case for case in cases if case["function"] == function]
            self.assertTrue(any(case["source"].startswith("curated:") for case in selected), function)
            self.assertGreaterEqual(
                len([case for case in selected if case["source"].startswith("seeded:")]), 2, function
            )

    def test_pilot_generates_boundaries_and_two_seeded_cases_for_every_recovery_core_function(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        functions = {
            "RecoveryScorer.parasympatheticSaturation/2",
            "RecoveryScorer.restingHR/3",
            "RecoveryScorer.recoveryIndexSlope/3",
            "RecoveryScorer.band/1",
            "RecoveryScorer.zScore/3",
            "RecoveryScorer.recovery/12",
            "RecoveryScorer.logisticScore/1",
            "RecoveryScorer.recovery/11",
        }
        for function in functions:
            selected = [case for case in cases if case["function"] == function]
            self.assertTrue(any(case["source"] == "curated:recovery.json" for case in selected), function)
            self.assertGreaterEqual(
                len([case for case in selected if case["source"].startswith("seeded:")]), 2, function
            )
        constants = next(case for case in cases if case["id"] == "recovery_saturation_full_and_constants")
        self.assertTrue(constants["effectiveArgs"]["characterizeRecoveryConstants"])

    def test_recovery_trace_has_curated_boundaries_and_two_structured_seeds(self):
        cases = parity_diff.generate_cases("pilot", "trace-coverage")
        selected = [case for case in cases if case["function"] == parity_diff.RECOVERY_TRACE_KEY]
        curated = [case for case in selected if case["source"] == "curated:recovery_trace.json"]
        seeded = [case for case in selected if case["source"].startswith("seeded:")]

        self.assertGreaterEqual(len(curated), 18)
        self.assertEqual(2, len(seeded))
        self.assertTrue(all(case["comparison"] == "exact" for case in selected))
        self.assertTrue(all(isinstance(case["args"]["hrvBaseline"], dict) for case in seeded))

    def test_heart_rate_recovery_has_complete_curated_matrix_and_exactly_two_structured_seeds(self):
        cases = parity_diff.generate_cases("pilot", "hrr-coverage")
        selected = [case for case in cases if case["function"] == parity_diff.HEART_RATE_RECOVERY_KEY]
        curated = [case for case in selected if case["source"] == "curated:heart_rate_recovery.json"]
        seeded = [case for case in selected if case["source"].startswith("seeded:")]

        self.assertGreaterEqual(len(curated), 18)
        self.assertEqual(2, len(seeded))
        self.assertTrue(all(case["comparison"] == "exact" for case in selected))
        ids = {case["id"] for case in curated}
        for fragment in (
            "threshold_below", "threshold_exact", "duration_119", "duration_120",
            "lookback_300", "gap_10", "gap_11", "bpm_bounds", "cessation_boundary",
            "sample_count", "tolerance_15_16", "post_count", "median_odd_even",
            "missing_windows", "negative_recovery", "shuffled_duplicates",
            "disconnected_segments_issue_55",
        ):
            self.assertTrue(any(fragment in case_id for case_id in ids), fragment)
        by_id = {case["id"]: case for case in curated}
        self.assertIn("heart_rate_recovery_cessation_sample_count_2", by_id)
        self.assertIn("heart_rate_recovery_cessation_sample_count_3", by_id)
        self.assertFalse(any("overall_sample_count" in case_id for case_id in ids))
        bpm_samples = by_id["heart_rate_recovery_bpm_bounds_asymmetric_29_30_250_251"]["args"]["samples"]
        self.assertEqual([29, 30, 31], [row["bpm"] for row in bpm_samples if 1059 <= row["ts"] <= 1061])
        self.assertEqual([249, 250, 251], [row["bpm"] for row in bpm_samples if 1119 <= row["ts"] <= 1121])
        tolerance = by_id["heart_rate_recovery_tolerance_15_16"]["args"]
        offsets = {row["ts"] - tolerance["workoutEnd"] for row in tolerance["samples"]}
        for target in (60, 120, 300):
            self.assertTrue({target - 16, target - 15, target, target + 15, target + 16} <= offsets)
        issue = next(case for case in curated if "disconnected_segments_issue_55" in case["id"])
        self.assertEqual("bhelm/noop#55", issue["knownBehaviorIssue"])
        self.assertEqual(
            {"endHR": 130, "after1Minute": 20, "after2Minutes": None, "after5Minutes": None},
            issue["expected"],
        )

    def test_heart_rate_recovery_validator_rejects_unknown_malformed_and_overflow(self):
        good = {
            "samples": [{"ts": 880, "bpm": 140}, {"ts": 890, "bpm": 140}, {"ts": 900, "bpm": 140}],
            "workoutStart": 500, "workoutEnd": 1000, "maxHR": 200.0,
        }
        int64_max = (1 << 63) - 1
        malformed = [
            {**good, "surprise": 1},
            {**good, "workoutStart": True},
            {**good, "workoutStart": 0},
            {**good, "workoutEnd": 500},
            {**good, "maxHR": float("inf")},
            {**good, "maxHR": 251.0},
            {**good, "samples": [{"ts": 1, "bpm": True}]},
            {**good, "samples": [{}]},
            {**good, "samples": good["samples"] * 4000},
            {**good, "workoutEnd": int64_max},
            {**good, "samples": [{"ts": -(1 << 63), "bpm": 140}]},
        ]
        for index, args in enumerate(malformed):
            with self.subTest(index=index), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args({
                    "id": f"bad-hrr-{index}", "function": parity_diff.HEART_RATE_RECOVERY_KEY,
                    "args": args,
                })

    def test_heart_rate_recovery_negative_suite_declares_one_exact_mutant_probe(self):
        selected = [
            case for case in parity_diff.generate_cases("negative", "hrr-mutant")
            if case["function"] == parity_diff.HEART_RATE_RECOVERY_KEY
        ]
        self.assertEqual(["heart_rate_recovery_negative_probe"], [case["id"] for case in selected])
        self.assertEqual("exact", selected[0]["comparison"])

    def test_heart_rate_recovery_known_behavior_expected_is_enforced_per_side(self):
        case_id = "heart_rate_recovery_disconnected_segments_issue_55"
        expected = {"endHR": 130, "after1Minute": 20, "after2Minutes": None, "after5Minutes": None}
        input_record = self.input_record(case_id)
        input_record.update({
            "function": parity_diff.HEART_RATE_RECOVERY_KEY,
            "knownBehaviorIssue": "bhelm/noop#55",
            "expected": expected,
        })
        swift = self.output_record(case_id, function=parity_diff.HEART_RATE_RECOVERY_KEY, valueBits=None)
        kotlin = self.output_record(case_id, function=parity_diff.HEART_RATE_RECOVERY_KEY, valueBits=None)

        diffs = self.compare([input_record], [swift], [kotlin])

        self.assertEqual(2, len(diffs))
        self.assertTrue(all("known_behavior=bhelm/noop#55" in diff for diff in diffs))
        self.assertTrue(any("side=swift" in diff for diff in diffs))
        self.assertTrue(any("side=kotlin" in diff for diff in diffs))

    def test_heart_rate_recovery_known_behavior_expected_fails_closed_when_malformed(self):
        case_id = "heart_rate_recovery_disconnected_segments_issue_55"
        input_record = self.input_record(case_id)
        input_record.update({
            "function": parity_diff.HEART_RATE_RECOVERY_KEY,
            "knownBehaviorIssue": "bhelm/noop#55",
            "expected": {"endHR": 130, "after1Minute": 20, "surprise": None},
        })
        output = self.output_record(
            case_id, function=parity_diff.HEART_RATE_RECOVERY_KEY, valueBits=None
        )

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "expected"):
            self.compare([input_record], [output], [output])

    def test_recovery_trace_issue_38_acceptance_is_an_exact_complete_trace_case(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "issue-38")}
        case = cases["recovery_trace_issue_38_negative_half_tie"]

        self.assertEqual(parity_diff.RECOVERY_TRACE_KEY, case["function"])
        self.assertEqual("exact", case["comparison"])
        self.assertEqual(0.125, case["args"]["skinTempDev"])
        self.assertFalse(case["args"]["useDefaults"])

    def test_recovery_trace_omitted_and_explicit_arg8_paths_are_distinct(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "trace-default")}
        omitted = cases["recovery_trace_default_arg8_omitted"]
        explicit = cases["recovery_trace_explicit_arg8_null"]

        self.assertNotIn("skinTempDev", omitted["args"])
        self.assertIsNone(omitted["effectiveArgs"]["skinTempDev"])
        self.assertIn("skinTempDev", explicit["args"])
        self.assertIsNone(explicit["effectiveArgs"]["skinTempDev"])

    def test_recovery_trace_payloads_fail_closed_with_issue_linked_rounding_exclusion(self):
        baseline = {
            "baseline": 50.0, "spread": 5.0, "nValid": 14,
            "nightsSinceUpdate": 0, "status": "trusted",
        }
        malformed = [
            {"hrv": 50.0, "rhr": 60.0, "useDefaults": True},
            {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": baseline, "useDefaults": False,
             "skinTempDev": -0.0},
            {"hrv": 49.98, "rhr": 60.0, "hrvBaseline": baseline, "useDefaults": True},
            {"hrv": 1e20, "rhr": 60.0, "hrvBaseline": baseline, "useDefaults": True},
            {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {**baseline, "status": "TRUSTED"},
             "useDefaults": True},
            {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {**baseline, "spread": 0.0},
             "useDefaults": True},
            {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": baseline, "skinTempDev": None,
             "useDefaults": True},
            {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": baseline, "useDefaults": True,
             "surprise": 1},
        ]
        for index, args in enumerate(malformed):
            record = {"id": f"bad-trace-{index}", "function": parity_diff.RECOVERY_TRACE_KEY,
                      "args": args}
            with self.subTest(index=index), self.assertRaises(parity_diff.ParityFormatError) as caught:
                parity_diff._effective_args(record)
            if 1 <= index < 4:
                self.assertIn("bhelm/noop#47", str(caught.exception))

    def test_recovery_trace_issue_47_exclusion_covers_each_derived_rounding_path(self):
        hrv_baseline = {
            "baseline": 50.0, "spread": 5.0, "nValid": 14,
            "nightsSinceUpdate": 0, "status": "trusted",
        }
        base = {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": hrv_baseline,
                "useDefaults": True}
        derived = {
            "rhr-z": {
                **base, "rhr": 60.02,
                "rhrBaseline": {**hrv_baseline, "baseline": 60.0},
            },
            "resp-z": {
                **base, "resp": 15.004,
                "respBaseline": {**hrv_baseline, "baseline": 15.0, "spread": 1.0},
            },
            "sleep-z": {**base, "sleepPerf": 0.84952},
            "skin-z": {**base, "skinTempDev": 0.004, "useDefaults": False},
            "composite-z": {
                **base, "hrv": 50.5, "rhr": 61.45,
                "rhrBaseline": {**hrv_baseline, "baseline": 60.0},
            },
        }
        for label, args in derived.items():
            record = {"id": f"issue-47-{label}", "function": parity_diff.RECOVERY_TRACE_KEY,
                      "args": args}
            with self.subTest(label=label), self.assertRaisesRegex(
                parity_diff.ParityFormatError, "bhelm/noop#47"
            ):
                parity_diff._effective_args(record)

        boundary = {
            **base, "hrv": 49.0,
            "hrvBaseline": {**hrv_baseline, "spread": 200.0},
        }
        effective = parity_diff._effective_args(
            {"id": "issue-47-boundary", "function": parity_diff.RECOVERY_TRACE_KEY,
             "args": boundary}
        )
        self.assertEqual(49.0, effective["hrv"])

    def test_recovery_trace_exact_payload_compares_score_bits_and_ordered_lines(self):
        input_record = self.input_record("trace")
        input_record["function"] = parity_diff.RECOVERY_TRACE_KEY
        inputs = [input_record]
        swift = [self.output_record(
            "trace", function=parity_diff.RECOVERY_TRACE_KEY,
            valueBits={"score": "4059000000000000", "trace": [{"text": "first"}, {"text": "second"}]},
        )]
        kotlin = [self.output_record(
            "trace", function=parity_diff.RECOVERY_TRACE_KEY,
            valueBits={"score": "4059000000000000", "trace": [{"text": "second"}, {"text": "first"}]},
        )]

        diffs = self.compare(inputs, swift, kotlin)
        self.assertEqual(1, len(diffs))
        self.assertIn("class=exact", diffs[0])

    def test_recovery_drivers_has_curated_boundaries_and_two_structured_seeds(self):
        cases = parity_diff.generate_cases("pilot", "drivers-coverage")
        selected = [case for case in cases if case["function"] == parity_diff.RECOVERY_DRIVERS_KEY]
        curated = [case for case in selected if case["source"] == "curated:recovery_drivers.json"]
        seeded = [case for case in selected if case["source"].startswith("seeded:splitmix64:")]

        self.assertGreaterEqual(len(curated), 12)
        self.assertEqual(2, len(seeded))
        self.assertTrue(all(case["comparison"] == "exact" for case in selected))
        ids = {case["id"] for case in curated}
        self.assertLessEqual(
            {
                "recovery_drivers_all_zero_stable_order",
                "recovery_drivers_cold_empty",
                "recovery_drivers_stale_empty",
                "recovery_drivers_resp_value_without_baseline",
                "recovery_drivers_resp_baseline_without_value",
                "recovery_drivers_saturation_detected",
                "recovery_drivers_skin_positive",
                "recovery_drivers_skin_negative",
            },
            ids,
        )
        self.assertTrue(all(isinstance(case["args"]["hrvBaseline"], dict) for case in seeded))

    def test_recovery_drivers_issue_51_has_exact_expected_row_on_default_path(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "drivers-half")}
        case = cases["recovery_drivers_issue_51_negative_half_tie"]

        self.assertEqual(parity_diff.RECOVERY_DRIVERS_KEY, case["function"])
        self.assertEqual("exact", case["comparison"])
        self.assertEqual(29.99117725828923, case["args"]["hrv"])
        self.assertEqual(60.0, case["args"]["rhr"])
        self.assertEqual(30.0, case["args"]["hrvBaseline"]["baseline"])
        self.assertEqual(0.55, case["args"]["hrvBaseline"]["spread"])
        self.assertTrue(case["args"]["useDefaults"])
        self.assertEqual({"hrv", "rhr", "hrvBaseline", "useDefaults"}, set(case["args"]))
        self.assertEqual("bhelm/noop#51", case["acceptanceIssue"])
        self.assertEqual(
            [{
                "baselineText": {"text": "30 ms baseline"},
                "deltaPoints": -1,
                "label": {"text": "Heart rate variability"},
                "valueText": {"text": "30 ms"},
                "verdict": {"text": "below baseline, limiting recovery"},
            }],
            case["expectedRows"],
        )
        z_score = (case["args"]["hrv"] - 30.0) / (1.253 * 0.55)
        def logistic(z):
            return 100.0 / (1.0 + math.exp(-1.6 * (z - -0.20)))
        self.assertEqual(-0.5, logistic(z_score) - logistic(0.0))

    def test_recovery_drivers_issue_52_seed_has_exact_expected_rows_and_issue_ref(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "drivers-skin")}
        case = cases["seeded_recovery_drivers_01"]

        self.assertEqual("bhelm/noop#52", case["acceptanceIssue"])
        self.assertEqual(-0.35, case["args"]["skinTempDev"])
        self.assertEqual(
            [
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
            ],
            case["expectedRows"],
        )

    def test_recovery_drivers_issue_oracle_rejects_common_mode_regression(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "drivers-oracle")}
        case = cases["recovery_drivers_issue_51_negative_half_tie"]
        wrong = [{**case["expectedRows"][0], "deltaPoints": 0}]
        output = [self.output_record(
            case["id"], nonce="drivers-oracle", function=parity_diff.RECOVERY_DRIVERS_KEY,
            valueBits=wrong,
        )]

        with self.assertRaisesRegex(parity_diff.ParityFormatError, "expectedRows"):
            self.compare([case], output, output)

    def test_recovery_drivers_omitted_and_explicit_arg8_paths_are_distinct(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "drivers-default")}
        omitted = cases["recovery_drivers_default_arg8_omitted"]
        explicit = cases["recovery_drivers_explicit_arg8_null"]

        self.assertTrue(omitted["args"]["useDefaults"])
        self.assertNotIn("skinTempDev", omitted["args"])
        self.assertIsNone(omitted["effectiveArgs"]["skinTempDev"])
        self.assertFalse(explicit["args"]["useDefaults"])
        self.assertIn("skinTempDev", explicit["args"])
        self.assertIsNone(explicit["effectiveArgs"]["skinTempDev"])

    def test_recovery_drivers_payloads_fail_closed_before_native_dispatch(self):
        baseline = {
            "baseline": 50.0, "spread": 5.0, "nValid": 14,
            "nightsSinceUpdate": 0, "status": "trusted",
        }
        valid = {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": baseline, "useDefaults": True}
        malformed = [
            {"hrv": 50.0, "rhr": 60.0, "useDefaults": True},
            {**valid, "surprise": 1},
            {**valid, "skinTempDev": None},
            {**valid, "useDefaults": False},
            {**valid, "hrv": 0.0},
            {**valid, "hrv": 10 ** 400},
            {**valid, "rhr": math.inf},
            {**valid, "hrvBaseline": {**baseline, "status": "TRUSTED"}},
            {**valid, "hrvBaseline": {**baseline, "spread": 0.0}},
            {**valid, "hrvBaseline": {**baseline, "extra": 1}},
            {**valid, "resp": 0.5},
            {**valid, "sleepPerf": 1.01},
            {**valid, "useDefaults": False, "skinTempDev": -0.0},
        ]
        for index, args in enumerate(malformed):
            record = {"id": f"bad-drivers-{index}", "function": parity_diff.RECOVERY_DRIVERS_KEY,
                      "args": args}
            with self.subTest(index=index), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(record)

    def test_recovery_drivers_output_requires_exact_row_fields_and_text_wrappers(self):
        input_record = self.input_record("drivers-shape")
        input_record["function"] = parity_diff.RECOVERY_DRIVERS_KEY
        good = {
            "label": {"text": "Heart rate variability"},
            "deltaPoints": 1,
            "valueText": {"text": "51 ms"},
            "baselineText": {"text": "50 ms baseline"},
            "verdict": {"text": "above baseline, supporting recovery"},
        }
        malformed = [
            {key: value for key, value in good.items() if key != "verdict"},
            {**good, "label": "Heart rate variability"},
            {**good, "deltaPoints": True},
            {**good, "deltaPoints": 101},
            {**good, "unexpected": {"text": "no"}},
        ]
        for index, row in enumerate(malformed):
            swift = [self.output_record(
                "drivers-shape", function=parity_diff.RECOVERY_DRIVERS_KEY, valueBits=[row]
            )]
            kotlin = [self.output_record(
                "drivers-shape", function=parity_diff.RECOVERY_DRIVERS_KEY, valueBits=[good]
            )]
            with self.subTest(index=index), self.assertRaises(parity_diff.ParityFormatError):
                self.compare([input_record], swift, kotlin)

    def test_recovery_drivers_exact_payload_detects_delta_and_row_order_mutations(self):
        input_record = self.input_record("drivers-exact")
        input_record["function"] = parity_diff.RECOVERY_DRIVERS_KEY
        hrv = {
            "label": {"text": "Heart rate variability"}, "deltaPoints": 0,
            "valueText": {"text": "50 ms"}, "baselineText": {"text": "50 ms baseline"},
            "verdict": {"text": "at baseline"},
        }
        rhr = {
            "label": {"text": "Resting heart rate"}, "deltaPoints": 0,
            "valueText": {"text": "60 bpm"}, "baselineText": {"text": "60 bpm baseline"},
            "verdict": {"text": "at baseline"},
        }
        swift = [self.output_record(
            "drivers-exact", function=parity_diff.RECOVERY_DRIVERS_KEY, valueBits=[hrv, rhr]
        )]
        for label, kotlin_rows in (
            ("delta", [{**hrv, "deltaPoints": 1}, rhr]),
            ("order", [rhr, hrv]),
        ):
            kotlin = [self.output_record(
                "drivers-exact", function=parity_diff.RECOVERY_DRIVERS_KEY, valueBits=kotlin_rows
            )]
            with self.subTest(mutation=label):
                diffs = self.compare([input_record], swift, kotlin)
                self.assertEqual(1, len(diffs))
                self.assertIn("class=exact", diffs[0])

    def test_recovery_drivers_negative_suite_assigns_delta_and_order_probes_to_native_sides(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("negative", "drivers-neg")}
        self.assertEqual(
            parity_diff.RECOVERY_DRIVERS_KEY,
            cases["recovery_drivers_negative_delta_probe"]["function"],
        )
        self.assertEqual(
            parity_diff.RECOVERY_DRIVERS_KEY,
            cases["recovery_drivers_negative_order_probe"]["function"],
        )
        repository = Path(__file__).resolve().parents[2]
        swift = (repository / "Packages/StrandAnalytics/Tests/StrandAnalyticsTests/ParityRunner.swift").read_text()
        kotlin = (repository / "android/app/src/test/java/com/noop/analytics/ParityRunner.kt").read_text()
        self.assertIn('negativeSide == "swift", record.id == "recovery_drivers_negative_order_probe"', swift)
        self.assertIn('negativeSide == "swift", record.id == "recovery_drivers_negative_delta_probe"', swift)
        self.assertIn('negativeSide == "kotlin" && caseId == "recovery_drivers_negative_delta_probe"', kotlin)
        self.assertIn('negativeSide == "kotlin" && caseId == "recovery_drivers_negative_order_probe"', kotlin)

    def test_recovery_drivers_runners_pin_default_arity_and_posix_formatting(self):
        repository = Path(__file__).resolve().parents[2]
        swift = (repository / "Packages/StrandAnalytics/Tests/StrandAnalyticsTests/ParityRunner.swift").read_text()
        kotlin = (repository / "android/app/src/test/java/com/noop/analytics/ParityRunner.kt").read_text()
        swift_block = swift.split(
            'case "RecoveryScorer.chargeDrivers/8=RecoveryDrivers.chargeDrivers/8":', 1
        )[1].split('case "RecoveryScorer.recoveryTrace/8=RecoveryScorerTrace.recoveryTrace/8":', 1)[0]
        kotlin_block = kotlin.split(
            '"RecoveryScorer.chargeDrivers/8=RecoveryDrivers.chargeDrivers/8" ->', 1
        )[1].split('"RecoveryScorer.recoveryTrace/8=RecoveryScorerTrace.recoveryTrace/8" ->', 1)[0]
        self.assertEqual(2, swift_block.count("RecoveryScorer.chargeDrivers("))
        self.assertEqual(1, swift_block.count("skinTempDev:"))
        self.assertEqual(2, kotlin_block.count("RecoveryDrivers.chargeDrivers("))
        self.assertEqual(1, kotlin_block.count("skinTempDev ="))
        self.assertIn('Locale(identifier: "en_US_POSIX")', swift)
        self.assertIn('format: "%.1f br/min"', swift)
        self.assertIn('format: "%+.1f C vs baseline"', swift)

    def test_recovery_forecast_frozen_audit_and_exactly_two_seed_strategies(self):
        cases = parity_diff.generate_cases("pilot", "forecast-audit")
        for function in parity_diff.RECOVERY_FORECAST_FUNCTIONS:
            selected = [case for case in cases if case["function"] == function]
            curated = [case for case in selected if case["source"] == "curated:recovery_forecast.json"]
            seeded = [case for case in selected if case["source"].startswith("seeded:")]
            self.assertTrue(curated, function)
            self.assertEqual(2, len(seeded), function)
            self.assertEqual(
                {"splitmix64", "affine"},
                {case["source"].split(":", 2)[1] for case in seeded},
                function,
            )
        by_id = {case["id"]: case for case in cases}
        for required in (
            "forecast_four_nights_nil", "forecast_five_nights",
            "forecast_nine_nights_thin", "forecast_ten_nights_trusted",
            "forecast_thirteen_window", "forecast_fourteen_window",
            "forecast_fifteen_trails_fourteen", "forecast_negative_sleep",
            "forecast_zero_sleep", "forecast_need_zero_floor", "forecast_need_point_one",
            "forecast_sleep_cap_entry", "forecast_sleep_above_cap",
            "forecast_need_six_building", "forecast_need_seven_solid",
            "forecast_band_half_tie", "forecast_score_low_clamp",
            "forecast_score_high_clamp", "forecast_strain_positive_cap",
            "forecast_strain_negative_cap", "forecast_reversion_cap",
            "forecast_clamp_issue_56_positive_x_negative_lower_zero",
            "forecast_clamp_issue_56_negative_x_positive_lower_zero",
            "forecast_clamp_issue_56_positive_x_negative_upper_zero",
            "forecast_clamp_issue_56_negative_x_positive_upper_zero",
        ):
            self.assertIn(required, by_id)
        omitted = by_id["forecast_default_omitted"]
        explicit = by_id["forecast_default_explicit"]
        self.assertNotIn("needHours", omitted["args"])
        self.assertNotIn("recentEffort", omitted["args"])
        self.assertIsNone(explicit["args"]["needHours"])
        constants = by_id["forecast_ten_nights_trusted"]
        self.assertTrue(constants["effectiveArgs"]["characterizeForecastConstants"])
        issue_56 = [
            by_id["forecast_clamp_issue_56_positive_x_negative_lower_zero"],
            by_id["forecast_clamp_issue_56_negative_x_positive_lower_zero"],
            by_id["forecast_clamp_issue_56_positive_x_negative_upper_zero"],
            by_id["forecast_clamp_issue_56_negative_x_positive_upper_zero"],
        ]
        self.assertTrue(all(case["regressionIssue"] == "bhelm/noop#56" for case in issue_56))
        self.assertEqual(
            [(1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, 1.0)],
            [
                (math.copysign(1.0, case["args"]["x"]), math.copysign(
                    1.0, case["args"]["lo"] if index < 2 else case["args"]["hi"]
                ))
                for index, case in enumerate(issue_56)
            ],
        )

    def test_recovery_forecast_payloads_fail_closed_before_native_dispatch(self):
        good = {
            "recentCharge": [50.0] * 5, "todayEffort": None,
            "plannedSleepHours": 8.0, "useDefaults": True,
        }
        malformed = [
            {**good, "recentCharge": [50.0, "bad"]},
            {**good, "needHours": 8.0},
            {**good, "useDefaults": False},
            {**good, "needNights": -1, "useDefaults": False,
             "recentEffort": [], "needHours": 8.0},
            {**good, "surprise": 1},
            {"values": [1.0], "extra": 2.0},
            {"x": 0.0, "lo": 1.0, "hi": 0.0},
        ]
        functions = [parity_diff.RECOVERY_FORECAST_KEY] * 5 + [
            "RecoveryForecaster.mean/1", "RecoveryForecaster.clamp/3",
        ]
        for index, (function, args) in enumerate(zip(functions, malformed)):
            with self.subTest(index=index), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(
                    {"id": f"bad-forecast-{index}", "function": function, "args": args}
                )

    def test_recovery_forecast_numeric_domains_and_array_caps_fail_closed(self):
        base = {
            "recentCharge": [50.0] * 5, "recentEffort": [], "todayEffort": None,
            "plannedSleepHours": 8.0, "needHours": 8.0, "needNights": 7,
            "useDefaults": False,
        }
        malformed_forecasts = [
            {**base, "recentCharge": [-0.1] * 5},
            {**base, "recentCharge": [100.1] * 5},
            {**base, "recentCharge": [50.0] * 4097},
            {**base, "recentEffort": [100.1]},
            {**base, "recentEffort": [50.0] * 4097},
            {**base, "todayEffort": -0.1},
            {**base, "plannedSleepHours": -24.1},
            {**base, "plannedSleepHours": 24.1},
            {**base, "needHours": -0.1},
            {**base, "needHours": 24.1},
        ]
        for index, args in enumerate(malformed_forecasts):
            with self.subTest(kind="forecast", index=index), self.assertRaises(
                parity_diff.ParityFormatError
            ):
                parity_diff._effective_args({
                    "id": f"bad-forecast-domain-{index}",
                    "function": parity_diff.RECOVERY_FORECAST_KEY,
                    "args": args,
                })

        for index, values in enumerate(([0.0] * 4097, [1e101, -1e101])):
            with self.subTest(kind="helper", index=index), self.assertRaises(
                parity_diff.ParityFormatError
            ):
                parity_diff._effective_args({
                    "id": f"bad-forecast-helper-{index}",
                    "function": "RecoveryForecaster.sampleSD/1",
                    "args": {"values": values},
                })

    def test_recovery_forecast_seed_corpus_is_module_local_and_frozen(self):
        first = parity_diff._seeded_recovery_forecast_cases()
        unrelated = parity_diff.SplitMix64(parity_diff.GENERATOR_SEED)
        for _ in range(10_000):
            unrelated.next_u64()
        second = parity_diff._seeded_recovery_forecast_cases()

        self.assertEqual(first, second)
        self.assertEqual(
            [
                "seeded_recovery_forecast_splitmix64",
                "seeded_recovery_forecast_mean_splitmix64",
                "seeded_recovery_forecast_sample_sd_splitmix64",
                "seeded_recovery_forecast_slope_splitmix64",
                "seeded_recovery_forecast_clamp_splitmix64",
                "seeded_recovery_forecast_affine",
                "seeded_recovery_forecast_mean_affine",
                "seeded_recovery_forecast_sample_sd_affine",
                "seeded_recovery_forecast_slope_affine",
                "seeded_recovery_forecast_clamp_affine",
            ],
            [case["id"] for case in first],
        )

    def test_recovery_forecast_exact_payload_covers_every_normalized_field(self):
        input_record = self.input_record("forecast")
        input_record["function"] = parity_diff.RECOVERY_FORECAST_KEY
        value = {
            "score": "4059000000000000", "band": "4020000000000000",
            "baseline": "4049000000000000", "planned": "4020000000000000",
            "need": "4020000000000000", "low": "4051000000000000",
            "high": "4061000000000000", "nights": 10,
            "confidence": {"text": "solid"},
        }
        kotlin_value = dict(value)
        kotlin_value["low"] = "4051000000000001"
        swift = [self.output_record(
            "forecast", function=parity_diff.RECOVERY_FORECAST_KEY, valueBits=value,
        )]
        kotlin = [self.output_record(
            "forecast", function=parity_diff.RECOVERY_FORECAST_KEY, valueBits=kotlin_value,
        )]
        diffs = self.compare([input_record], swift, kotlin)
        self.assertEqual(1, len(diffs))
        self.assertIn("class=exact", diffs[0])

    def test_recovery_forecast_negative_suite_has_source_and_output_mutants(self):
        cases = {
            case["id"]: case for case in parity_diff.generate_cases("negative", "forecast-negative")
        }
        source = cases["recovery_forecast_negative_source_probe"]
        output = cases["recovery_forecast_negative_output_probe"]
        self.assertEqual("RecoveryForecaster.mean/1", source["function"])
        self.assertEqual("epsilon", source["comparison"])
        self.assertEqual(parity_diff.RECOVERY_FORECAST_KEY, output["function"])
        self.assertEqual("exact", output["comparison"])

    def test_recovery_payloads_fail_closed_before_native_dispatch(self):
        int64_max = (1 << 63) - 1
        malformed = [
            {"function": "RecoveryScorer.restingHR/3", "args": {"hr": [], "start": 2, "end": 1}},
            {"function": "RecoveryScorer.recoveryIndexSlope/3", "args": {"hr": [], "start": 0, "end": int64_max}},
            {"function": "RecoveryScorer.restingHR/3", "args": {"hr": [{"ts": int64_max + 1, "bpm": 60}], "start": 0, "end": 300}},
            {"function": "RecoveryScorer.zScore/3", "args": {"value": 1.0, "mean": 1.0, "spread": 0.0}},
            {"function": "RecoveryScorer.recovery/12", "args": {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {"mean": 50.0, "spread": -1.0}, "useDefaults": True}},
            {"function": "RecoveryScorer.recovery/12", "args": {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {"mean": 50.0, "spread": 5.0}, "hrvBaselineUsable": True, "useDefaults": True}},
            {"function": "RecoveryScorer.recovery/12", "args": {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {"mean": 50.0, "spread": 5.0}, "skinTempDev": None, "useDefaults": True}},
            {"function": "RecoveryScorer.recovery/11", "args": {"hrv": 50.0, "rhr": 60.0, "hrvBaseline": {"baseline": 50.0, "spread": 5.0, "nValid": 4, "nightsSinceUpdate": 0, "status": "PROVISIONAL"}, "useDefaults": True}},
        ]
        for index, record in enumerate(malformed):
            record["id"] = f"bad-recovery-{index}"
            with self.subTest(record=record), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(record)

    def test_recovery_known_shared_behaviors_are_explicitly_tagged(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "known-recovery")}
        self.assertEqual("bhelm/noop#10", cases["recovery_index_sparse_bins"]["knownBehaviorIssue"])
        self.assertEqual("bhelm/noop#39", cases["recovery_resting_aligned_endpoint"]["knownBehaviorIssue"])
        self.assertEqual("bhelm/noop#39", cases["recovery_index_aligned_endpoint_gate"]["knownBehaviorIssue"])
        self.assertEqual("bhelm/noop#40", cases["recovery_driver_missing_hrv_baseline"]["knownBehaviorIssue"])

    def test_recovery_omitted_defaults_and_explicit_controls_are_distinct(self):
        cases = {case["id"]: case for case in parity_diff.generate_cases("pilot", "defaults")}
        omitted = cases["recovery_driver_hrv_only_defaults"]
        explicit = cases["recovery_driver_hrv_only_explicit"]
        self.assertNotIn("hrvBaselineUsable", omitted["args"])
        self.assertTrue(omitted["effectiveArgs"]["hrvBaselineUsable"])
        self.assertTrue(explicit["args"]["hrvBaselineUsable"])

    def test_strain_payloads_fail_closed_before_reaching_native_runners(self):
        malformed = [
            {"id": "bad-hr", "function": "StrainScorer.sampleDurationsMinutes/1", "args": {"hr": [{"ts": 1.5, "bpm": 90}]}},
            {"id": "bad-duration", "function": "StrainScorer.edwardsTRIMP/4", "args": {"hr": [{"ts": 1, "bpm": 90}], "durations": [], "restingHR": 60.0, "hrReserve": 120.0}},
            {"id": "bad-percentile", "function": "StrainScorer.percentile/2", "args": {"values": [1.0], "pct": 101.0}},
            {"id": "bad-pairs", "function": "StrainScorer.fitStrainDenominator/1", "args": {"pairs": [[1.0]]}},
            {"id": "issue-36", "function": "StrainScorer.trimpToStrain/2", "args": {"trimp": 1.0, "denominator": 1.0}},
            {"id": "issue-37", "function": "StrainScorer.effectiveEffort/2", "args": {"live": 0.0, "stored": -0.0}},
            {"id": "bad-strain", "function": "StrainScorer.strain/6", "args": {"replayFirstAtEnd": False, "strainCalls": [{"series": {"count": 20, "startTs": 0, "stepSec": 0, "bpm": 100}, "useDefaults": True}]}},
        ]
        for record in malformed:
            with self.subTest(record=record["id"]), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(record)

    def test_legacy_and_qualified_trimp_labels_share_effective_arguments(self):
        expected = {"denominator": 7201.0, "trimp": 100.0}
        for function in ("trimpToStrain", "StrainScorer.trimpToStrain/2"):
            with self.subTest(function=function):
                self.assertEqual(
                    expected,
                    parity_diff._effective_args(
                        {"id": function, "function": function, "args": {"trimp": 100.0}}
                    ),
                )
        legacy_cases = [
            case for case in parity_diff.generate_cases("pilot", "legacy-pilot")
            if case["function"] == "trimpToStrain"
        ]
        self.assertEqual(["trimp_non_positive"], [case["id"] for case in legacy_cases])

    def test_strain_integer_payloads_fail_closed_on_signed_width_and_checked_arithmetic(self):
        int64_min = -(1 << 63)
        int64_max = (1 << 63) - 1
        int32_max = (1 << 31) - 1
        malformed = [
            {
                "id": "duration-subtraction-overflow",
                "function": "StrainScorer.sampleDurationMinutes/1",
                "args": {"hr": [{"ts": int64_min, "bpm": 90}, {"ts": int64_max, "bpm": 90}]},
            },
            {
                "id": "duration-ts-above-int64",
                "function": "StrainScorer.sampleDurationsMinutes/1",
                "args": {"hr": [{"ts": int64_max + 1, "bpm": 90}]},
            },
            {
                "id": "duration-ts-below-int64",
                "function": "StrainScorer.sampleDurationsMinutes/1",
                "args": {"hr": [{"ts": int64_min - 1, "bpm": 90}]},
            },
            {
                "id": "duration-bpm-above-int32",
                "function": "StrainScorer.sampleDurationMinutes/1",
                "args": {"hr": [{"ts": 0, "bpm": int32_max + 1}]},
            },
            {
                "id": "series-addition-overflow",
                "function": "StrainScorer.strain/6",
                "args": {"strainCalls": [{"series": {"count": 3, "startTs": int64_max - 1, "stepSec": 2, "bpm": 90}, "useDefaults": True}]},
            },
            {
                "id": "series-multiplication-overflow",
                "function": "StrainScorer.strain/6",
                "args": {"strainCalls": [{"series": {"count": int32_max, "startTs": 0, "stepSec": int64_max, "bpm": 90}, "useDefaults": True}]},
            },
            {
                "id": "series-count-above-int32",
                "function": "StrainScorer.strain/6",
                "args": {"strainCalls": [{"series": {"count": int32_max + 1, "startTs": 0, "stepSec": 1, "bpm": 90}, "useDefaults": True}]},
            },
            {
                "id": "series-final-neighbor-difference-overflow",
                "function": "StrainScorer.strain/6",
                "args": {"strainCalls": [{"series": {"count": 2, "startTs": int64_min, "stepSec": 1, "finalTs": int64_max, "bpm": 90}, "useDefaults": True}]},
            },
            {
                "id": "series-total-span-overflow",
                "function": "StrainScorer.strain/6",
                "args": {"strainCalls": [{"series": {"count": 2, "startTs": int64_min, "stepSec": 1, "finalTs": 0, "bpm": 90}, "useDefaults": True}]},
            },
        ]
        for record in malformed:
            with self.subTest(record=record["id"]), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(record)

        safe = [
            {"count": 3, "startTs": int64_max - 2, "stepSec": 1, "bpm": 90},
            {"count": 2, "startTs": int64_min, "stepSec": 1, "finalTs": int64_min + 1, "bpm": 90},
        ]
        for index, series in enumerate(safe):
            with self.subTest(safe=index):
                effective = parity_diff._effective_args(
                    {"id": f"safe-{index}", "function": "StrainScorer.strain/6", "args": {"strainCalls": [{"series": series, "useDefaults": True}]}}
                )
                self.assertEqual(series, effective["strainCalls"][0]["series"])

    def test_strain_hr_reserve_must_be_strictly_positive(self):
        records = [
            {"function": "StrainScorer.pctHRR/3", "args": {"bpm": 90.0, "restingHR": 60.0, "hrReserve": -1.0}},
            {"function": "StrainScorer.zoneWeight/3", "args": {"bpm": 90.0, "restingHR": 60.0, "hrReserve": 0.0}},
            {"function": "StrainScorer.edwardsTRIMP/4", "args": {"hr": [], "durations": [], "restingHR": 60.0, "hrReserve": -1.0}},
            {"function": "StrainScorer.banisterTRIMP/5", "args": {"hr": [], "durations": [], "restingHR": 60.0, "hrReserve": 0.0, "b": 1.92}},
        ]
        for index, record in enumerate(records):
            record["id"] = f"reserve-{index}"
            with self.subTest(function=record["function"]), self.assertRaises(parity_diff.ParityFormatError):
                parity_diff._effective_args(record)

    def test_edwards_zones_have_one_exact_ordered_characterization_case(self):
        selected = [
            case for case in parity_diff.generate_cases("pilot", "zones")
            if case["function"] == "StrainScorer.zoneWeight/3"
            and case["args"].get("characterizeZones") is True
        ]
        self.assertEqual(["strain_zone_thresholds"], [case["id"] for case in selected])

    def test_issue_12_shared_behavior_must_be_explicitly_characterized(self):
        record = {
            "args": {"hr": []},
            "comparison": "epsilon",
            "function": "StrainScorer.sampleDurationsMinutes/1",
            "id": "unmarked-shared-behavior",
            "source": "curated:test",
        }
        with self.assertRaisesRegex(parity_diff.ParityFormatError, "bhelm/noop#12"):
            original_curated = parity_diff._curated_cases
            original_seeded = parity_diff._seeded_cases
            try:
                parity_diff._curated_cases = lambda: [record]
                parity_diff._seeded_cases = lambda: []
                parity_diff.generate_cases("pilot", "nonce")
            finally:
                parity_diff._curated_cases = original_curated
                parity_diff._seeded_cases = original_seeded

    def test_analyze_cases_cover_the_min_beats_boundary_and_two_seeded_inputs(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        analyze = [case for case in cases if case["function"] == "analyze/3"]
        curated = [case for case in analyze if case["source"] == "curated:hrv"]
        seeded = [case for case in analyze if case["source"].startswith("seeded:")]

        self.assertEqual(
            ["hrv_analyze_min_beats_19", "hrv_analyze_min_beats_20"],
            [case["id"] for case in curated],
        )
        self.assertEqual([19, 20], [len(case["args"]["rr"]) for case in curated])
        self.assertTrue(all(300 <= beat["rrMs"] <= 2_000 for case in curated for beat in case["args"]["rr"]))
        self.assertEqual(2, len(seeded))

    def test_raw_analyze_cases_cover_path_boundaries_and_two_seeded_inputs(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        key = "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2"
        selected = [case for case in cases if case["function"] == key]
        curated = [case for case in selected if case["source"] == "curated:hrv"]
        seeded = [case for case in selected if case["source"].startswith("seeded:")]

        self.assertEqual(
            ["hrv_analyze_raw_clean", "hrv_analyze_raw_empty", "hrv_analyze_raw_under_min"],
            [case["id"] for case in curated],
        )
        self.assertEqual([20, 0, 19], [len(case["args"]["rawRR"]) for case in curated])
        self.assertEqual(2, len(seeded))

    def test_hrv_median_cases_cover_shape_edges_and_two_seeded_inputs(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        key = "HRVAnalyzer.median/1=HrvAnalyzer.median/1"
        selected = [case for case in cases if case["function"] == key]
        curated = [case for case in selected if case["source"] == "curated:hrv"]
        seeded = [case for case in selected if case["source"].startswith("seeded:")]

        self.assertEqual(
            [
                "hrv_median_duplicates",
                "hrv_median_empty",
                "hrv_median_even",
                "hrv_median_odd",
                "hrv_median_singleton",
            ],
            [case["id"] for case in curated],
        )
        self.assertEqual(2, len(seeded))


if __name__ == "__main__":
    unittest.main()
