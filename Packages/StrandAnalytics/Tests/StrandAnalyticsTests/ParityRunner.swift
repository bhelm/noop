import Foundation
import XCTest
@testable import StrandAnalytics
import WhoopProtocol

final class ParityRunner: XCTestCase {
    private struct RRInput: Decodable {
        let rrMs: Int
        let ts: Int
    }

    private struct Arguments: Decodable {
        let collapsed: Double?
        let contiguous: [Bool]?
        let coverage: Double?
        let denominator: Double?
        let fraction: Double?
        let halfWindowSec: Int?
        let maxRowsPerSecond: Int?
        let minBeatsPerWindow: Int?
        let nn: [Double]?
        let rr: [RRInput]?
        let rrMs: [Double]?
        let rrTolMs: Double?
        let srcCodes: [Int?]?
        let stepSec: Int?
        let tsSec: [Int]?
        let trimp: Double?
        let values: [Double]?
        let verdict: String?
        let windowEnd: Int?
        let windowSec: Int?
        let windowStart: Int?
    }

    private struct InputRecord: Decodable {
        let args: Arguments
        let comparison: String
        let effectiveArgs: Arguments
        let function: String
        let id: String
        let nonce: String
    }

    private enum RunnerError: Error, CustomStringConvertible {
        case invalidEnvironment(String)
        case invalidInput(String)
        case nonFinite(String)

        var description: String {
            switch self {
            case .invalidEnvironment(let message), .invalidInput(let message), .nonFinite(let message):
                return message
            }
        }
    }

    func testParityRunner() throws {
        let environment = ProcessInfo.processInfo.environment
        if environment["PARITY_INPUT"] == nil, environment["PARITY_OUTPUT"] == nil {
            if environment["PARITY_NEGATIVE_SIDE"] != nil {
                throw RunnerError.invalidEnvironment(
                    "PARITY_NEGATIVE_SIDE is set but PARITY_INPUT/PARITY_OUTPUT are not"
                )
            }
            throw XCTSkip("Parity files are provided only for explicit parity runs")
        }
        let inputPath = try XCTUnwrap(environment["PARITY_INPUT"], "PARITY_INPUT is required")
        let outputPath = try XCTUnwrap(environment["PARITY_OUTPUT"], "PARITY_OUTPUT is required")
        let negativeSide = environment["PARITY_NEGATIVE_SIDE"]
        if let negativeSide, negativeSide != "swift", negativeSide != "kotlin" {
            throw RunnerError.invalidEnvironment(
                "PARITY_NEGATIVE_SIDE must be swift, kotlin, or unset"
            )
        }

        let inputData = try Data(contentsOf: URL(fileURLWithPath: inputPath))
        guard let inputText = String(data: inputData, encoding: .utf8) else {
            throw RunnerError.invalidInput("PARITY_INPUT must be UTF-8")
        }
        let lines = inputText.split(separator: "\n", omittingEmptySubsequences: false)
        guard lines.last == "" else {
            throw RunnerError.invalidInput("PARITY_INPUT must end with a newline")
        }
        let decoder = JSONDecoder()
        let records = try lines.dropLast().enumerated().map { index, line in
            guard !line.isEmpty else {
                throw RunnerError.invalidInput("PARITY_INPUT line \(index + 1) is blank")
            }
            return try decoder.decode(InputRecord.self, from: Data(line.utf8))
        }
        guard !records.isEmpty else { throw RunnerError.invalidInput("PARITY_INPUT is empty") }
        let ids = records.map(\.id)
        guard Set(ids).count == ids.count else {
            throw RunnerError.invalidInput("PARITY_INPUT contains duplicate case IDs")
        }

        var output = Data()
        for record in records.sorted(by: { $0.id < $1.id }) {
            let result = try evaluate(record, negativeSide: negativeSide)
            let encoded = try JSONSerialization.data(
                withJSONObject: result,
                options: [.sortedKeys, .withoutEscapingSlashes]
            )
            output.append(encoded)
            output.append(0x0a)
        }
        try output.write(to: URL(fileURLWithPath: outputPath), options: .atomic)
    }

    private func evaluate(_ record: InputRecord, negativeSide: String?) throws -> [String: Any] {
        var result: [String: Any] = [
            "comparison": record.comparison,
            "function": record.function,
            "id": record.id,
            "nonce": record.nonce,
        ]
        switch record.function {
        case "rmssdRaw":
            guard record.comparison == "epsilon", let nn = record.args.nn else {
                throw RunnerError.invalidInput("invalid rmssdRaw case \(record.id)")
            }
            result["value"] = try finiteOrNull(HRVAnalyzer.rmssdRaw(nn), record: record)
        case "sdnnRaw":
            guard record.comparison == "epsilon", let nn = record.args.nn else {
                throw RunnerError.invalidInput("invalid sdnnRaw case \(record.id)")
            }
            result["value"] = try finiteOrNull(HRVAnalyzer.sdnnRaw(nn), record: record)
        case "rangeFilter":
            guard record.comparison == "exact", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid rangeFilter case \(record.id)")
            }
            result["valueBits"] = exactBits(HRVAnalyzer.rangeFilter(values))
        case "rejectEctopic":
            guard record.comparison == "exact", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid rejectEctopic case \(record.id)")
            }
            result["valueBits"] = exactBits(HRVAnalyzer.rejectEctopic(values))
        case "cleanRR":
            guard record.comparison == "exact", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid cleanRR case \(record.id)")
            }
            result["valueBits"] = exactBits(HRVAnalyzer.cleanRR(values))
        case "cleanRRGapAware":
            guard record.comparison == "exact", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid cleanRRGapAware case \(record.id)")
            }
            let clean = HRVAnalyzer.cleanRRGapAware(values)
            let encoded: [String: Any] = [
                "contiguous": clean.contiguous,
                "nn": exactBits(clean.nn),
            ]
            result["valueBits"] = encoded
        case "rmssdGapAware":
            guard record.comparison == "epsilon", let nn = record.args.nn,
                  let contiguous = record.args.contiguous, nn.count == contiguous.count else {
                throw RunnerError.invalidInput("invalid rmssdGapAware case \(record.id)")
            }
            result["value"] = try finiteOrNull(
                HRVAnalyzer.rmssdGapAware(nn, contiguous), record: record
            )
        case "pnn50GapAware":
            guard record.comparison == "epsilon", let nn = record.args.nn,
                  let contiguous = record.args.contiguous, nn.count == contiguous.count else {
                throw RunnerError.invalidInput("invalid pnn50GapAware case \(record.id)")
            }
            result["value"] = try finiteOrNull(
                HRVAnalyzer.pnn50GapAware(nn, contiguous), record: record
            )
        case "analyze/3":
            guard record.comparison == "epsilon", let inputRR = record.args.rr else {
                throw RunnerError.invalidInput("invalid analyze/3 case \(record.id)")
            }
            let rr = inputRR.map { RRInterval(ts: $0.ts, rrMs: $0.rrMs) }
            let value: HRVAnalyzer.HRVResult
            if record.args.windowStart == nil, record.args.windowEnd == nil {
                value = HRVAnalyzer.analyze(rr)
            } else {
                value = HRVAnalyzer.analyze(
                    rr,
                    windowStart: record.args.windowStart,
                    windowEnd: record.args.windowEnd
                )
            }
            result["value"] = [
                "meanNN": try finiteOrNull(value.meanNN, record: record),
                "nClean": value.nClean,
                "nInput": value.nInput,
                "pnn50": try finiteOrNull(value.pnn50, record: record),
                "rmssd": try finiteOrNull(value.rmssd, record: record),
                "sdnn": try finiteOrNull(value.sdnn, record: record),
            ]
        case "beatSpreadIsTrustworthy":
            guard record.comparison == "exact", let raw = record.args.verdict,
                  let verdict = HRVAnalyzer.RrCoverageVerdict(rawValue: raw) else {
                throw RunnerError.invalidInput("invalid beatSpreadIsTrustworthy case \(record.id)")
            }
            result["valueBits"] = HRVAnalyzer.beatSpreadIsTrustworthy(verdict)
        case "beatAccurateFraction":
            guard record.comparison == "epsilon", let ts = record.args.tsSec,
                  let rr = record.args.rrMs else {
                throw RunnerError.invalidInput("invalid beatAccurateFraction case \(record.id)")
            }
            result["value"] = try finite(
                HRVAnalyzer.beatAccurateFraction(tsSec: ts, rrMs: rr), record: record
            )
        case "beatValuesAreTrustworthy":
            guard record.comparison == "exact", let fraction = record.args.fraction else {
                throw RunnerError.invalidInput("invalid beatValuesAreTrustworthy case \(record.id)")
            }
            result["valueBits"] = HRVAnalyzer.beatValuesAreTrustworthy(
                beatAccurateFraction: fraction
            )
        case "classifyCoverage":
            guard record.comparison == "exact", let coverage = record.args.coverage,
                  let collapsed = record.args.collapsed else {
                throw RunnerError.invalidInput("invalid classifyCoverage case \(record.id)")
            }
            let verdict = HRVAnalyzer.classifyCoverage(coverage: coverage, collapsed: collapsed)
            result["valueBits"] = ["text": verdict.rawValue]
        case "rrCoverage":
            guard record.comparison == "epsilon", let ts = record.args.tsSec,
                  let rr = record.args.rrMs else {
                throw RunnerError.invalidInput("invalid rrCoverage case \(record.id)")
            }
            result["value"] = try finite(
                HRVAnalyzer.rrCoverage(tsSec: ts, rrMs: rr), record: record
            )
        case "duplicateBeatCount":
            guard record.comparison == "exact", let ts = record.args.tsSec,
                  let rr = record.args.rrMs else {
                throw RunnerError.invalidInput("invalid duplicateBeatCount case \(record.id)")
            }
            result["valueBits"] = HRVAnalyzer.duplicateBeatCount(tsSec: ts, rrMs: rr)
        case "collapseOverCount":
            guard record.comparison == "exact", let ts = record.args.tsSec,
                  let rr = record.args.rrMs, let rrTol = record.effectiveArgs.rrTolMs,
                  let window = record.effectiveArgs.windowSec else {
                throw RunnerError.invalidInput("invalid collapseOverCount case \(record.id)")
            }
            let collapsed: (tsSec: [Int], rrMs: [Double])
            if record.args.rrTolMs == nil, record.args.windowSec == nil {
                collapsed = HRVAnalyzer.collapseOverCount(tsSec: ts, rrMs: rr)
            } else {
                collapsed = HRVAnalyzer.collapseOverCount(
                    tsSec: ts, rrMs: rr, rrTolMs: rrTol, windowSec: window
                )
            }
            let encoded: [String: Any] = [
                "rrMs": exactBits(collapsed.rrMs),
                "tsSec": collapsed.tsSec,
            ]
            result["valueBits"] = encoded
        case "collapsedCoverage":
            guard record.comparison == "epsilon", let ts = record.args.tsSec,
                  let rr = record.args.rrMs, let rrTol = record.effectiveArgs.rrTolMs else {
                throw RunnerError.invalidInput("invalid collapsedCoverage case \(record.id)")
            }
            let value = record.args.rrTolMs == nil
                ? HRVAnalyzer.collapsedCoverage(tsSec: ts, rrMs: rr)
                : HRVAnalyzer.collapsedCoverage(tsSec: ts, rrMs: rr, rrTolMs: rrTol)
            result["value"] = try finite(value, record: record)
        case "densestSecondWindowSample":
            guard record.comparison == "exact", let ts = record.args.tsSec,
                  let rr = record.args.rrMs, let src = record.args.srcCodes,
                  let halfWindow = record.effectiveArgs.halfWindowSec,
                  let maxRows = record.effectiveArgs.maxRowsPerSecond else {
                throw RunnerError.invalidInput("invalid densestSecondWindowSample case \(record.id)")
            }
            let value: String
            if record.args.halfWindowSec == nil, record.args.maxRowsPerSecond == nil {
                value = HRVAnalyzer.densestSecondWindowSample(tsSec: ts, rrMs: rr, srcCodes: src)
            } else {
                value = HRVAnalyzer.densestSecondWindowSample(
                    tsSec: ts, rrMs: rr, srcCodes: src,
                    halfWindowSec: halfWindow, maxRowsPerSecond: maxRows
                )
            }
            result["valueBits"] = ["text": value]
        case "rollingRmssd":
            guard record.comparison == "epsilon",
                  let inputRR = record.args.rr,
                  let windowSec = record.effectiveArgs.windowSec,
                  let stepSec = record.effectiveArgs.stepSec,
                  let minBeats = record.effectiveArgs.minBeatsPerWindow else {
                throw RunnerError.invalidInput("invalid rollingRmssd case \(record.id)")
            }
            let rr = inputRR.map { RRInterval(ts: $0.ts, rrMs: $0.rrMs) }
            let points: [HRVAnalyzer.RollingRmssdPoint]
            if record.args.windowSec == nil, record.args.stepSec == nil,
               record.args.minBeatsPerWindow == nil {
                // Bare case: every omittable argument omitted, so the language's own default
                // expressions execute and the cross-comparison itself checks default parity.
                // windowSec stays explicit — Swift deliberately has no default for it (the
                // documented one-sided default; Kotlin's default is cross-checked by its runner).
                points = HRVAnalyzer.rollingRmssd(rr: rr, windowSec: windowSec)
            } else {
                points = HRVAnalyzer.rollingRmssd(
                    rr: rr,
                    windowSec: windowSec,
                    stepSec: stepSec,
                    minBeatsPerWindow: minBeats
                )
            }
            var values: [[String: Any]] = []
            for point in points {
                guard point.rmssd.isFinite else {
                    throw RunnerError.nonFinite("rollingRmssd returned a non-finite value for \(record.id)")
                }
                values.append(["rmssd": point.rmssd, "ts": String(point.ts)])
            }
            result["value"] = values
        case "trimpToStrain":
            guard record.comparison == "exact", let trimp = record.args.trimp else {
                throw RunnerError.invalidInput("invalid trimpToStrain case \(record.id)")
            }
            let value: Double
            if let denominator = record.args.denominator {
                value = StrainScorer.trimpToStrain(trimp, denominator: denominator)
            } else {
                value = StrainScorer.trimpToStrain(trimp)
            }
            var emitted = value
            if negativeSide == "swift", record.id == "trimp_negative_probe" {
                emitted += 1e-6
                result["negativeSide"] = "swift"
            }
            guard emitted.isFinite else {
                throw RunnerError.nonFinite("trimpToStrain returned a non-finite value for \(record.id)")
            }
            result["valueBits"] = String(format: "%016llx", emitted.bitPattern)
        default:
            throw RunnerError.invalidInput("unsupported parity function \(record.function)")
        }
        return result
    }

    private func exactBits(_ values: [Double]) -> [String] {
        values.map { String(format: "%016llx", $0.bitPattern) }
    }

    private func finite(_ value: Double, record: InputRecord) throws -> Double {
        guard value.isFinite else {
            throw RunnerError.nonFinite("\(record.function) returned a non-finite value for \(record.id)")
        }
        return value
    }

    private func finiteOrNull(_ value: Double?, record: InputRecord) throws -> Any {
        guard let value else { return NSNull() }
        return try finite(value, record: record)
    }
}
