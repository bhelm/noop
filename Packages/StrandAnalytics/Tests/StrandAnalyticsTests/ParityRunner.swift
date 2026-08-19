import Foundation
import XCTest
@testable import StrandAnalytics
import WhoopProtocol

final class ParityRunner: XCTestCase {
    private struct RRInput: Decodable {
        let rrMs: Int
        let ts: Int
    }

    private struct HRInput: Decodable {
        let ts: Int
        let bpm: Int
    }

    private struct HistoryInput: Decodable {
        let count: Int
        let low: Double
        let high: Double
    }

    private struct HRSeriesInput: Decodable {
        let alternateBpm: Int?
        let bpm: Int
        let count: Int
        let finalTs: Int?
        let startTs: Int
        let stepSec: Int
    }

    private struct StrainCallInput: Decodable {
        let denominator: Double?
        let maxHR: Double?
        let method: String?
        let restingHR: Double?
        let series: HRSeriesInput
        let sex: String?
        let useDefaults: Bool
    }

    private struct BaselineInput: Decodable {
        let mean: Double?
        let baseline: Double?
        let spread: Double
        let nValid: Int?
        let nightsSinceUpdate: Int?
        let status: String?
    }

    private struct Arguments: Decodable {
        let age: Double?
        let ageInt: Int?
        let b: Double?
        let bpm: Double?
        let characterizeZones: Bool?
        let characterizeRecoveryConstants: Bool?
        let collapsed: Double?
        let contiguous: [Bool]?
        let coverage: Double?
        let denominator: Double?
        let compositeZ: Double?
        let fraction: Double?
        let hrv: Double?
        let hrvBaseline: BaselineInput?
        let hrvZ: Double?
        let rhr: Double?
        let rhrZ: Double?
        let rhrBaseline: BaselineInput?
        let resp: Double?
        let respBaseline: BaselineInput?
        let sleepPerf: Double?
        let skinTempDev: Double?
        let hrvBaselineUsable: Bool?
        let recoveryIndexSlope: Double?
        let effortBaseline: BaselineInput?
        let priorDayEffort: Double?
        let useDefaults: Bool?
        let history: HistoryInput?
        let hr: [HRInput]?
        let halfWindowSec: Int?
        let maxRowsPerSecond: Int?
        let maxRejectedFraction: Double?
        let minBeatsPerWindow: Int?
        let nn: [Double]?
        let pairs: [[Double]]?
        let pct: Double?
        let rawRR: [Double]?
        let replayFirstAtEnd: Bool?
        let restingHR: Double?
        let hrReserve: Double?
        let rr: [RRInput]?
        let rrMs: [Double]?
        let rrTolMs: Double?
        let srcCodes: [Int?]?
        let stepSec: Int?
        let tsSec: [Int]?
        let trimp: Double?
        let durations: [Double]?
        let live: Double?
        let mean: Double?
        let score: Double?
        let spread: Double?
        let start: Int?
        let end: Int?
        let stored: Double?
        let strainCalls: [StrainCallInput]?
        let values: [Double]?
        let value: Double?
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
        let dispatchFunction = record.function == "trimpToStrain"
            ? "StrainScorer.trimpToStrain/2"
            : record.function
        switch dispatchFunction {
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
        case "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2":
            guard record.comparison == "epsilon", let rawRR = record.args.rawRR else {
                throw RunnerError.invalidInput("invalid raw analyze case \(record.id)")
            }
            let value = record.args.maxRejectedFraction.map {
                HRVAnalyzer.analyze(rawRR: rawRR, maxRejectedFraction: $0)
            } ?? HRVAnalyzer.analyze(rawRR: rawRR)
            result["value"] = [
                "meanNN": try finiteOrNull(value.meanNN, record: record),
                "nClean": value.nClean,
                "nInput": value.nInput,
                "pnn50": try finiteOrNull(value.pnn50, record: record),
                "rmssd": try finiteOrNull(value.rmssd, record: record),
                "sdnn": try finiteOrNull(value.sdnn, record: record),
            ]
        case "HRVAnalyzer.median/1=HrvAnalyzer.median/1":
            guard record.comparison == "exact", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid HRV median case \(record.id)")
            }
            result["valueBits"] = String(
                format: "%016llx", HRVAnalyzer.median(values).bitPattern
            )
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
        case "RecoveryScorer.parasympatheticSaturation/2":
            guard record.comparison == "epsilon", let hrvZ = record.args.hrvZ else {
                throw RunnerError.invalidInput("invalid parasympatheticSaturation case \(record.id)")
            }
            let value = RecoveryScorer.parasympatheticSaturation(hrvZ: hrvZ, rhrZ: record.args.rhrZ)
            var encoded: [String: Any] = [
                "active": value.active,
                "dampFraction": try finite(value.dampFraction, record: record),
                "easedHrvZ": try finite(value.easedHrvZ, record: record),
            ]
            if record.args.characterizeRecoveryConstants == true {
                encoded["constants"] = recoveryConstants()
            }
            result["value"] = encoded
        case "RecoveryScorer.restingHR/3":
            guard record.comparison == "exact", let input = record.args.hr,
                  let start = record.args.start, let end = record.args.end else {
                throw RunnerError.invalidInput("invalid restingHR case \(record.id)")
            }
            result["valueBits"] = RecoveryScorer.restingHR(
                hrSamples(input), start: start, end: end
            ) ?? NSNull()
        case "RecoveryScorer.recoveryIndexSlope/3":
            guard record.comparison == "epsilon", let input = record.args.hr,
                  let start = record.args.start, let end = record.args.end else {
                throw RunnerError.invalidInput("invalid recoveryIndexSlope case \(record.id)")
            }
            result["value"] = try finiteOrNull(
                RecoveryScorer.recoveryIndexSlope(hrSamples(input), start: start, end: end),
                record: record
            )
        case "RecoveryScorer.band/1":
            guard record.comparison == "exact", let score = record.args.score else {
                throw RunnerError.invalidInput("invalid recovery band case \(record.id)")
            }
            var value = RecoveryScorer.band(score)
            if negativeSide == "swift", record.id == "recovery_negative_band_probe" {
                value += "-mutant"
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = ["text": value]
        case "RecoveryScorer.zScore/3":
            guard record.comparison == "epsilon", let value = record.args.value,
                  let mean = record.args.mean, let spread = record.args.spread else {
                throw RunnerError.invalidInput("invalid recovery zScore case \(record.id)")
            }
            result["value"] = try finite(
                RecoveryScorer.zScore(value, mean: mean, spread: spread), record: record
            )
        case "RecoveryScorer.recovery/12":
            guard record.comparison == "exact", let hrv = record.args.hrv,
                  let rhr = record.args.rhr, let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid driver recovery case \(record.id)")
            }
            let value: Double?
            if useDefaults {
                value = RecoveryScorer.recovery(
                    hrv: hrv, rhr: rhr, resp: record.args.resp,
                    hrvBaseline: try driverBaseline(record.args.hrvBaseline, record: record),
                    rhrBaseline: try driverBaseline(record.args.rhrBaseline, record: record),
                    respBaseline: try driverBaseline(record.args.respBaseline, record: record),
                    sleepPerf: record.args.sleepPerf
                )
            } else {
                guard let usable = record.effectiveArgs.hrvBaselineUsable else {
                    throw RunnerError.invalidInput("missing effective driver recovery defaults \(record.id)")
                }
                value = RecoveryScorer.recovery(
                    hrv: hrv, rhr: rhr, resp: record.effectiveArgs.resp,
                    hrvBaseline: try driverBaseline(record.effectiveArgs.hrvBaseline, record: record),
                    rhrBaseline: try driverBaseline(record.effectiveArgs.rhrBaseline, record: record),
                    respBaseline: try driverBaseline(record.effectiveArgs.respBaseline, record: record),
                    sleepPerf: record.effectiveArgs.sleepPerf,
                    skinTempDev: record.effectiveArgs.skinTempDev,
                    hrvBaselineUsable: usable,
                    recoveryIndexSlope: record.effectiveArgs.recoveryIndexSlope,
                    effortBaseline: try driverBaseline(record.effectiveArgs.effortBaseline, record: record),
                    priorDayEffort: record.effectiveArgs.priorDayEffort
                )
            }
            result["valueBits"] = value.map(exactBit) ?? NSNull()
        case "RecoveryScorer.logisticScore/1":
            guard record.comparison == "epsilon", let compositeZ = record.args.compositeZ else {
                throw RunnerError.invalidInput("invalid logisticScore case \(record.id)")
            }
            var value = RecoveryScorer.logisticScore(compositeZ: compositeZ)
            if negativeSide == "swift", record.id == "recovery_negative_logistic_probe" {
                value += 1e-6
                result["negativeSide"] = "swift"
            }
            result["value"] = try finite(value, record: record)
        case "RecoveryScorer.recovery/11":
            guard record.comparison == "exact", let hrv = record.args.hrv,
                  let rhr = record.args.rhr, let hrvInput = record.args.hrvBaseline,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid baseline-state recovery case \(record.id)")
            }
            let hrvBaseline = try baselineState(hrvInput, record: record)
            let value: Double?
            if useDefaults {
                value = RecoveryScorer.recovery(
                    hrv: hrv, rhr: rhr, resp: record.args.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.args.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.args.respBaseline, record: record),
                    sleepPerf: record.args.sleepPerf
                )
            } else {
                value = RecoveryScorer.recovery(
                    hrv: hrv, rhr: rhr, resp: record.effectiveArgs.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.effectiveArgs.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.effectiveArgs.respBaseline, record: record),
                    sleepPerf: record.effectiveArgs.sleepPerf,
                    skinTempDev: record.effectiveArgs.skinTempDev,
                    recoveryIndexSlope: record.effectiveArgs.recoveryIndexSlope,
                    effortBaseline: try baselineStateOptional(record.effectiveArgs.effortBaseline, record: record),
                    priorDayEffort: record.effectiveArgs.priorDayEffort
                )
            }
            result["valueBits"] = value.map(exactBit) ?? NSNull()
        case "RecoveryScorer.recoveryTrace/8=RecoveryScorerTrace.recoveryTrace/8":
            guard record.comparison == "exact", let hrv = record.args.hrv,
                  let rhr = record.args.rhr, let hrvInput = record.args.hrvBaseline,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid recoveryTrace case \(record.id)")
            }
            let hrvBaseline = try baselineState(hrvInput, record: record)
            let value: (score: Double?, trace: [String])
            if useDefaults {
                value = RecoveryScorer.recoveryTrace(
                    hrv: hrv, rhr: rhr, resp: record.args.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.args.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.args.respBaseline, record: record),
                    sleepPerf: record.args.sleepPerf
                )
            } else {
                value = RecoveryScorer.recoveryTrace(
                    hrv: hrv, rhr: rhr, resp: record.effectiveArgs.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.effectiveArgs.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.effectiveArgs.respBaseline, record: record),
                    sleepPerf: record.effectiveArgs.sleepPerf,
                    skinTempDev: record.effectiveArgs.skinTempDev
                )
            }
            var emittedScore = value.score
            var emittedTrace = value.trace
            if negativeSide == "swift", record.id == "recovery_trace_negative_score_probe",
               let score = emittedScore {
                emittedScore = score + 1e-6
                result["negativeSide"] = "swift"
            }
            if negativeSide == "swift", record.id == "recovery_trace_negative_line_probe",
               !emittedTrace.isEmpty {
                emittedTrace[0] += " [mutant]"
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = [
                "score": emittedScore.map(exactBit) ?? NSNull(),
                "trace": emittedTrace.map { ["text": $0] },
            ] as [String: Any]
        case "StrainScorer.trimpToStrain/2":
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
        case "StrainScorer.tanakaHRmax/1":
            guard record.comparison == "epsilon", let age = record.args.age else {
                throw RunnerError.invalidInput("invalid tanakaHRmax case \(record.id)")
            }
            result["value"] = try finite(StrainScorer.tanakaHRmax(age: age), record: record)
        case "StrainScorer.defaultMaxHR/1":
            guard record.comparison == "exact", let effectiveAge = record.effectiveArgs.ageInt else {
                throw RunnerError.invalidInput("invalid defaultMaxHR case \(record.id)")
            }
            let value = record.args.ageInt.map { StrainScorer.defaultMaxHR(age: $0) }
                ?? StrainScorer.defaultMaxHR()
            guard value == 220 - effectiveAge else {
                throw RunnerError.invalidInput("defaultMaxHR effective args disagree for \(record.id)")
            }
            result["valueBits"] = value
        case "StrainScorer.percentile/2":
            guard record.comparison == "epsilon", let values = record.args.values,
                  let pct = record.args.pct else {
                throw RunnerError.invalidInput("invalid percentile case \(record.id)")
            }
            result["value"] = try finite(StrainScorer.percentile(values, pct), record: record)
        case "StrainScorer.estimateHRmax/2":
            guard record.comparison == "epsilon", let history = record.args.history,
                  history.count >= 0 else {
                throw RunnerError.invalidInput("invalid estimateHRmax case \(record.id)")
            }
            let values = expandedHistory(history)
            let value = StrainScorer.estimateHRmax(values, age: record.args.age)
            result["value"] = [
                "hrmax": try finite(value.0, record: record),
                "source": value.1,
            ]
        case "StrainScorer.pctHRR/3":
            guard record.comparison == "epsilon", let bpm = record.args.bpm,
                  let resting = record.args.restingHR, let reserve = record.args.hrReserve else {
                throw RunnerError.invalidInput("invalid pctHRR case \(record.id)")
            }
            result["value"] = try finite(
                StrainScorer.pctHRR(bpm, restingHR: resting, hrReserve: reserve), record: record
            )
        case "StrainScorer.zoneWeight/3":
            guard record.comparison == "exact", let bpm = record.args.bpm,
                  let resting = record.args.restingHR, let reserve = record.args.hrReserve else {
                throw RunnerError.invalidInput("invalid zoneWeight case \(record.id)")
            }
            let weight = StrainScorer.zoneWeight(bpm, restingHR: resting, hrReserve: reserve)
            if record.args.characterizeZones == true {
                result["valueBits"] = [
                    "weight": weight,
                    "zones": StrainScorer.edwardsZones.map {
                        ["threshold": exactBit($0.threshold), "weight": $0.weight] as [String: Any]
                    },
                ] as [String: Any]
            } else {
                result["valueBits"] = weight
            }
        case "StrainScorer.effectiveEffort/2":
            guard record.comparison == "exact" else {
                throw RunnerError.invalidInput("invalid effectiveEffort case \(record.id)")
            }
            let value = StrainScorer.effectiveEffort(live: record.args.live, stored: record.args.stored)
            result["valueBits"] = value.map(exactBit) ?? NSNull()
        case "StrainScorer.sampleDurationMinutes/1":
            guard record.comparison == "epsilon", let input = record.args.hr else {
                throw RunnerError.invalidInput("invalid sampleDurationMinutes case \(record.id)")
            }
            result["value"] = try finite(
                StrainScorer.sampleDurationMinutes(hrSamples(input)), record: record
            )
        case "StrainScorer.sampleDurationsMinutes/1":
            guard record.comparison == "epsilon", let input = record.args.hr else {
                throw RunnerError.invalidInput("invalid sampleDurationsMinutes case \(record.id)")
            }
            result["value"] = try StrainScorer.sampleDurationsMinutes(hrSamples(input)).map {
                try finite($0, record: record)
            }
        case "StrainScorer.edwardsTRIMP/4":
            guard record.comparison == "epsilon", let input = record.args.hr,
                  let resting = record.args.restingHR, let reserve = record.args.hrReserve,
                  let durations = record.args.durations, durations.count == input.count else {
                throw RunnerError.invalidInput("invalid edwardsTRIMP case \(record.id)")
            }
            result["value"] = try finite(
                StrainScorer.edwardsTRIMP(
                    hrSamples(input), restingHR: resting, hrReserve: reserve, durations: durations
                ), record: record
            )
        case "StrainScorer.banisterTRIMP/5":
            guard record.comparison == "epsilon", let input = record.args.hr,
                  let resting = record.args.restingHR, let reserve = record.args.hrReserve,
                  let durations = record.args.durations, durations.count == input.count,
                  let b = record.args.b else {
                throw RunnerError.invalidInput("invalid banisterTRIMP case \(record.id)")
            }
            result["value"] = try finite(
                StrainScorer.banisterTRIMP(
                    hrSamples(input), restingHR: resting, hrReserve: reserve,
                    durations: durations, b: b
                ), record: record
            )
        case "StrainScorer.fitStrainDenominator/1":
            guard record.comparison == "epsilon", let rawPairs = record.args.pairs,
                  rawPairs.allSatisfy({ $0.count == 2 }) else {
                throw RunnerError.invalidInput("invalid fitStrainDenominator case \(record.id)")
            }
            do {
                let value = try StrainScorer.fitStrainDenominator(
                    rawPairs.map { (trimp: $0[0], strain: $0[1]) }
                )
                result["value"] = try finite(value, record: record)
            } catch let error as StrainScorer.StrainError {
                result["error"] = error == .tooFewPairs ? "tooFewPairs" : "degenerate"
            }
        case "StrainScorer.strain/6":
            guard record.comparison == "exact", let calls = record.args.strainCalls,
                  let effectiveCalls = record.effectiveArgs.strainCalls,
                  calls.count == effectiveCalls.count,
                  let replay = record.effectiveArgs.replayFirstAtEnd else {
                throw RunnerError.invalidInput("invalid strain case \(record.id)")
            }
            var encoded: [String?] = []
            for (call, effective) in zip(calls, effectiveCalls) {
                let hr = expandedHRSeries(call.series)
                let value: Double?
                if call.useDefaults {
                    value = StrainScorer.strain(hr)
                } else {
                    guard let maxHR = effective.maxHR, let resting = effective.restingHR,
                          let methodRaw = effective.method, let sex = effective.sex,
                          let denominator = effective.denominator else {
                        throw RunnerError.invalidInput("invalid explicit strain controls \(record.id)")
                    }
                    let method: StrainScorer.Method
                    if methodRaw == "edwards" {
                        method = .edwards
                    } else if methodRaw == "banister" {
                        method = .banister
                    } else {
                        throw RunnerError.invalidInput("invalid strain method \(record.id)")
                    }
                    value = StrainScorer.strain(
                        hr, maxHR: maxHR, restingHR: resting, method: method,
                        sex: sex, denominator: denominator
                    )
                }
                if let value {
                    guard value.isFinite else {
                        throw RunnerError.nonFinite("strain returned non-finite for \(record.id)")
                    }
                    encoded.append(exactBit(value))
                } else {
                    encoded.append(nil)
                }
            }
            if replay, encoded.first != encoded.last {
                throw RunnerError.invalidInput("strain A→B→A replay changed result for \(record.id)")
            }
            result["valueBits"] = encoded.map { value -> Any in value ?? NSNull() }
        default:
            throw RunnerError.invalidInput("unsupported parity function \(record.function)")
        }
        return result
    }

    private func exactBits(_ values: [Double]) -> [String] {
        values.map { String(format: "%016llx", $0.bitPattern) }
    }

    private func exactBit(_ value: Double) -> String {
        String(format: "%016llx", value.bitPattern)
    }

    private func recoveryConstants() -> [String: Any] {
        [
            "bandRedMax": exactBit(RecoveryScorer.bandRedMax),
            "bandYellowMax": exactBit(RecoveryScorer.bandYellowMax),
            "logisticK": exactBit(RecoveryScorer.logisticK),
            "logisticZ0": exactBit(RecoveryScorer.logisticZ0),
            "populationMean": exactBit(RecoveryScorer.populationMean),
            "recoveryIndexMinBins": RecoveryScorer.recoveryIndexMinBins,
            "recoveryIndexScaleBpmPerHr": exactBit(RecoveryScorer.recoveryIndexScaleBpmPerHr),
            "restingHRMinBinSamples": RecoveryScorer.restingHRMinBinSamples,
            "restingHRMinPlausibleBpm": exactBit(RecoveryScorer.restingHRMinPlausibleBpm),
            "restingHRWindowS": RecoveryScorer.restingHRWindowS,
            "satEnterZ": exactBit(RecoveryScorer.satEnterZ),
            "satFullZ": exactBit(RecoveryScorer.satFullZ),
            "satMaxDampFraction": exactBit(RecoveryScorer.satMaxDampFraction),
            "skinTempScale": exactBit(RecoveryScorer.skinTempScaleC),
            "sleepPerfCenter": exactBit(RecoveryScorer.sleepPerfCenter),
            "sleepPerfScale": exactBit(RecoveryScorer.sleepPerfScale),
            "wActivityBalance": exactBit(RecoveryScorer.wActivityBalance),
            "wHRV": exactBit(RecoveryScorer.wHRV),
            "wRHR": exactBit(RecoveryScorer.wRHR),
            "wRecoveryIndex": exactBit(RecoveryScorer.wRecoveryIndex),
            "wResp": exactBit(RecoveryScorer.wResp),
            "wSkinTemp": exactBit(RecoveryScorer.wSkinTemp),
            "wSleep": exactBit(RecoveryScorer.wSleep),
        ]
    }

    private func driverBaseline(_ input: BaselineInput?, record: InputRecord) throws -> RecoveryScorer.DriverBaseline? {
        guard let input else { return nil }
        guard let mean = input.mean else {
            throw RunnerError.invalidInput("invalid driver baseline \(record.id)")
        }
        return RecoveryScorer.DriverBaseline(mean: mean, spread: input.spread)
    }

    private func baselineStateOptional(_ input: BaselineInput?, record: InputRecord) throws -> BaselineState? {
        guard let input else { return nil }
        return try baselineState(input, record: record)
    }

    private func baselineState(_ input: BaselineInput, record: InputRecord) throws -> BaselineState {
        guard let baseline = input.baseline, let nValid = input.nValid,
              let nights = input.nightsSinceUpdate, let rawStatus = input.status,
              let status = BaselineStatus(rawValue: rawStatus) else {
            throw RunnerError.invalidInput("invalid baseline state \(record.id)")
        }
        return BaselineState(
            baseline: baseline, spread: input.spread, nValid: nValid,
            nightsSinceUpdate: nights, status: status
        )
    }

    private func hrSamples(_ input: [HRInput]) -> [HRSample] {
        input.map { HRSample(ts: $0.ts, bpm: $0.bpm) }
    }

    private func expandedHistory(_ input: HistoryInput) -> [Double] {
        guard input.count > 0 else { return [] }
        guard input.count > 1 else { return [input.high] }
        return (0..<input.count).map { index in
            input.low + Double(index) * (input.high - input.low) / Double(input.count - 1)
        }
    }

    private func expandedHRSeries(_ input: HRSeriesInput) -> [HRSample] {
        guard input.count > 0 else { return [] }
        return (0..<input.count).map { index in
            HRSample(
                ts: index == input.count - 1
                    ? (input.finalTs ?? input.startTs + index * input.stepSec)
                    : input.startTs + index * input.stepSec,
                bpm: index.isMultiple(of: 2) ? input.bpm : (input.alternateBpm ?? input.bpm)
            )
        }
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
