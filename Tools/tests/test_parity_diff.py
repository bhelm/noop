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
            "function": "trimpToStrain",
            "id": case_id,
            "nonce": nonce,
        }

    def output_record(self, case_id="case", nonce="nonce-1", **fields):
        record = {
            "comparison": fields.pop("comparison", "exact"),
            "function": "trimpToStrain",
            "id": case_id,
            "nonce": nonce,
        }
        record.update(fields)
        return record

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
        self.assertIn("id=case function=trimpToStrain class=exact", diffs[0])
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

    def test_pilot_generates_curated_and_seeded_cases_for_every_registered_hrv_function(self):
        cases = parity_diff.generate_cases("pilot", "fixed-nonce")
        functions = {case["function"] for case in cases}
        hrv_functions = {
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


if __name__ == "__main__":
    unittest.main()
