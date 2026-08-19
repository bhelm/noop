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

    private struct SleepNightInput: Decodable {
        let day: String
        let totalSleepMin: Double?
    }
    private struct SSTBlockInput: Decodable { let start: Int; let end: Int }
    private struct SSTStagesInput: Decodable { let startTs: Int; let stagesJSON: String? }
    private struct SSTOnsetInput: Decodable { let startTs: Int; let onset: Int }
    private struct SSTHistoryInput: Decodable { let start: Int; let end: Int; let dayKey: String }
    private enum HistoryArgument: Decodable {
        case generated(HistoryInput)
        case sleep([SSTHistoryInput])
        init(from decoder: Decoder) throws {
            let box = try decoder.singleValueContainer()
            if let value = try? box.decode(HistoryInput.self) { self = .generated(value) }
            else { self = .sleep(try box.decode([SSTHistoryInput].self)) }
        }
        var generated: HistoryInput? { if case .generated(let value) = self { return value }; return nil }
        var sleep: [SSTHistoryInput]? { if case .sleep(let value) = self { return value }; return nil }
    }

    private struct Arguments: Decodable {
        let age: Double?
        let ageInt: Int?
        let b: Double?
        let bpm: Double?
        let characterizeZones: Bool?
        let characterizeRecoveryConstants: Bool?
        let characterizeForecastConstants: Bool?
        let collapsed: Double?
        let contiguous: [Bool]?
        let coverage: Double?
        let denominator: Double?
        let compositeZ: Double?
        let fraction: Double?
        let hrv: Double?
        let todayHrv: Double?
        let todayRhr: Int?
        let hrvHistory: [Double]?
        let rhrHistory: [Double]?
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
        let recentCharge: [Double]?
        let recentEffort: [Double]?
        let todayEffort: Double?
        let plannedSleepHours: Double?
        let needHours: Double?
        let needNights: Int?
        let effortBaseline: BaselineInput?
        let priorDayEffort: Double?
        let useDefaults: Bool?
        let history: HistoryArgument?
        let hr: [HRInput]?
        let halfWindowSec: Int?
        let maxRowsPerSecond: Int?
        let maxRejectedFraction: Double?
        let maxHR: Double?
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
        let samples: [HRInput]?
        let spread: Double?
        let start: Int?
        let ts: Int?
        let end: Int?
        let stored: Double?
        let strainCalls: [StrainCallInput]?
        let values: [Double]?
        let value: Double?
        let verdict: String?
        let windowEnd: Int?
        let windowSec: Int?
        let windowStart: Int?
        let workoutEnd: Int?
        let workoutStart: Int?
        let x: Double?
        let lo: Double?
        let hi: Double?
        let mainSleepMin: Double?
        let napSleepMin: Double?
        let series: [SleepNightInput]?
        let window: Int?
        let previousBed: Int?
        let candidateBed: Int?
        let originalWake: Int?
        let now: Int?
        let zone: String?
        let newStart: Int?
        let newEnd: Int?
        let coverageStart: Int?
        let coverageEnd: Int?
        let slackSec: Int?
        let stage: String?
        let stagesJSON: String?
        let sessionStart: Int?
        let oldEnd: Int?
        let stagesJSONs: [String?]?
        let onsetSec: Int?
        let interFragmentAwakeSeconds: Double?
        let spans: [SSTBlockInput]?
        let blocks: [SSTBlockInput]?
        let offsetSec: Int?
        let habitualMidsleepSec: Int?
        let detected: [SSTStagesInput]?
        let edited: [SSTStagesInput]?
        let manual: [SSTStagesInput]?
        let onsetByStart: [SSTOnsetInput]?
        let minDays: Int?
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
        case "SleepStageTotals.minutes/1":
            var payload = minutesPayload(SleepStageTotals.minutes(fromStagesJSON: record.args.stagesJSON))
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_decode_probe" {
                payload = minutesPayload(.init()); result["negativeSide"] = "swift"
            }
            result["valueBits"] = payload
        case "SleepStageTotals.clampStagesToOnset/2":
            let raw = SleepStageTotals.clampStagesToOnset(record.args.stagesJSON, onsetSec: try XCTUnwrap(record.args.onsetSec))
            var payload: [String: Any] = ["returnedNull": raw == nil, "minutes": minutesPayload(SleepStageTotals.minutes(fromStagesJSON: raw))]
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_clamp_probe" { payload["minutes"] = minutesPayload(SleepStageTotals.minutes(fromStagesJSON: record.args.stagesJSON)); result["negativeSide"] = "swift" }
            result["valueBits"] = payload
        case "SleepStageTotals.dailyAggregate/1":
            result["valueBits"] = dailyPayload(SleepStageTotals.dailyAggregate(try XCTUnwrap(record.args.stagesJSONs)))
        case "SleepStageTotals.dailyAggregate/2":
            let stages = try XCTUnwrap(record.args.stagesJSONs)
            let awake = try XCTUnwrap(record.args.interFragmentAwakeSeconds)
            var value = SleepStageTotals.dailyAggregate(stages, interFragmentAwakeSeconds: awake)
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_daily_probe" { value = SleepStageTotals.dailyAggregate(stages); result["negativeSide"] = "swift" }
            result["valueBits"] = dailyPayload(value)
        case "SleepStageTotals.interFragmentAwakeSeconds/1":
            let spans = try XCTUnwrap(record.args.spans).map { (start: $0.start, end: $0.end) }
            result["valueBits"] = exactBit(SleepStageTotals.interFragmentAwakeSeconds(spans))
        case "SleepStageTotals.isOvernightOnset/2":
            result["valueBits"] = SleepStageTotals.isOvernightOnset(try XCTUnwrap(record.args.ts), offsetSec: try XCTUnwrap(record.args.offsetSec))
        case "SleepStageTotals.bridgedNightGroups/2":
            let blocks = try sstBlocks(record.args.blocks)
            var groups = SleepStageTotals.bridgedNightGroups(blocks, offsetSec: try XCTUnwrap(record.args.offsetSec))
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_bridge_probe" { groups = groups.map { .init(indices: Array($0.indices.prefix(1)), gaps: []) }; result["negativeSide"] = "swift" }
            result["valueBits"] = groups.map { ["indices":$0.indices,"gaps":$0.gaps.map { ["start":$0.start,"end":$0.end] }] }
        case "SleepStageTotals.mainNightGroupIndices/3":
            let blocks = try sstBlocks(record.args.blocks); let offset = try XCTUnwrap(record.args.offsetSec)
            let value = record.args.useDefaults == true ? SleepStageTotals.mainNightGroupIndices(blocks, offsetSec: offset) : SleepStageTotals.mainNightGroupIndices(blocks, offsetSec: offset, habitualMidsleepSec: record.effectiveArgs.habitualMidsleepSec)
            result["valueBits"] = value ?? NSNull()
        case "SleepStageTotals.mainNightIndex/3":
            let blocks = try sstBlocks(record.args.blocks); let offset = try XCTUnwrap(record.args.offsetSec)
            let value = record.args.useDefaults == true ? SleepStageTotals.mainNightIndex(blocks, offsetSec: offset) : SleepStageTotals.mainNightIndex(blocks, offsetSec: offset, habitualMidsleepSec: record.effectiveArgs.habitualMidsleepSec)
            result["valueBits"] = value ?? NSNull()
        case "SleepStageTotals.mainNightSelection/3":
            let blocks = try sstBlocks(record.args.blocks); let offset = try XCTUnwrap(record.args.offsetSec)
            let value = record.args.useDefaults == true ? SleepStageTotals.mainNightSelection(blocks, offsetSec: offset) : SleepStageTotals.mainNightSelection(blocks, offsetSec: offset, habitualMidsleepSec: record.effectiveArgs.habitualMidsleepSec)
            var payload: Any = value.map { ["index":$0.index,"reason":["text":$0.reason.rawValue],"asleepSeconds":$0.asleepSeconds] as [String:Any] } ?? NSNull()
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_selection_probe", let value { payload = ["index":value.index,"reason":["text":"longest"],"asleepSeconds":value.asleepSeconds]; result["negativeSide"] = "swift" }
            result["valueBits"] = payload
        case "SleepStageTotals.dailyAggregateHonoringEdits/6":
            let detected = try XCTUnwrap(record.args.detected).map { (startTs:$0.startTs, stagesJSON:$0.stagesJSON) }
            let edited = Dictionary(uniqueKeysWithValues: try XCTUnwrap(record.args.edited).map { ($0.startTs,$0.stagesJSON) })
            let effective = record.effectiveArgs
            let value = record.args.useDefaults == true
                ? SleepStageTotals.dailyAggregateHonoringEdits(detected: detected, edited: edited)
                : SleepStageTotals.dailyAggregateHonoringEdits(detected: detected, edited: edited, manual: (effective.manual ?? []).map { ($0.startTs,$0.stagesJSON) }, onsetByStart: effective.onsetByStart.map { Dictionary(uniqueKeysWithValues:$0.map { ($0.startTs,$0.onset) }) }, offsetSec: try XCTUnwrap(effective.offsetSec), habitualMidsleepSec: effective.habitualMidsleepSec)
            var payload: Any = value.map { ["sleep":dailyPayload($0.sleep),"editApplied":$0.editApplied] as [String:Any] } ?? NSNull()
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_edits_probe" { let legacy=SleepStageTotals.dailyAggregate(detected.map{$0.stagesJSON}); payload=legacy.map{["sleep":dailyPayload($0),"editApplied":false] as [String:Any]} ?? NSNull(); result["negativeSide"]="swift" }
            result["valueBits"] = payload
        case "SleepStageTotals.habitualMidsleepSec/3":
            let history = try XCTUnwrap(record.args.history?.sleep).map { SleepStageTotals.HistoryBlock(start:$0.start,end:$0.end,dayKey:$0.dayKey) }
            let offset = try XCTUnwrap(record.args.offsetSec)
            var value = record.args.useDefaults == true ? SleepStageTotals.habitualMidsleepSec(history, offsetSec:offset) : SleepStageTotals.habitualMidsleepSec(history, offsetSec:offset, minDays:try XCTUnwrap(record.effectiveArgs.minDays))
            if negativeSide == "swift", record.id == "sleep_stage_totals_negative_history_probe" { value = 0; result["negativeSide"]="swift" }
            result["valueBits"] = value ?? NSNull()
        case "SleepDebt.creditedSleepMin/2":
            guard record.comparison == "exact", let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid creditedSleepMin case \(record.id)")
            }
            let value = useDefaults
                ? SleepDebt.creditedSleepMin(mainSleepMin: record.args.mainSleepMin)
                : SleepDebt.creditedSleepMin(
                    mainSleepMin: record.args.mainSleepMin,
                    napSleepMin: try XCTUnwrap(record.effectiveArgs.napSleepMin)
                )
            var encoded: Any = value.map(exactBit) ?? NSNull()
            if negativeSide == "swift", record.id == "sleep_negative_output_probe", let value {
                encoded = exactBit(value + 1.0)
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = encoded
        case "SleepDebt.ledger/3":
            guard record.comparison == "exact", let input = record.args.series,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid SleepDebt ledger case \(record.id)")
            }
            let series = input.map { (day: $0.day, totalSleepMin: $0.totalSleepMin) }
            let value = useDefaults
                ? SleepDebt.ledger(series: series)
                : SleepDebt.ledger(
                    series: series,
                    needHours: try XCTUnwrap(record.effectiveArgs.needHours),
                    window: try XCTUnwrap(record.effectiveArgs.window)
                )
            result["valueBits"] = [
                "balanceMin": exactBit(value.balanceMin),
                "needMin": exactBit(value.needMin),
                "nights": value.nights.map { night in
                    [
                        "day": ["text": night.day],
                        "sleptMin": exactBit(night.sleptMin),
                        "deltaMin": exactBit(night.deltaMin),
                    ] as [String: Any]
                },
            ] as [String: Any]
        case "SleepEditGuard.autoCorrectedBed/5":
            guard record.comparison == "exact", let previous = record.args.previousBed,
                  let candidate = record.args.candidateBed, let now = record.args.now,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid autoCorrectedBed case \(record.id)")
            }
            let wake = record.args.originalWake.map { Date(timeIntervalSince1970: Double($0)) }
            let value: Date
            if useDefaults {
                value = SleepEditGuard.autoCorrectedBed(
                    previousBed: Date(timeIntervalSince1970: Double(previous)),
                    candidateBed: Date(timeIntervalSince1970: Double(candidate)),
                    originalWake: wake, now: Date(timeIntervalSince1970: Double(now))
                )
            } else {
                guard record.effectiveArgs.zone == "UTC" else {
                    throw RunnerError.invalidInput("explicit autoCorrectedBed zone must be UTC \(record.id)")
                }
                var calendar = Calendar(identifier: .gregorian)
                calendar.timeZone = TimeZone(secondsFromGMT: 0)!
                value = SleepEditGuard.autoCorrectedBed(
                    previousBed: Date(timeIntervalSince1970: Double(previous)),
                    candidateBed: Date(timeIntervalSince1970: Double(candidate)),
                    originalWake: wake, now: Date(timeIntervalSince1970: Double(now)), calendar: calendar
                )
            }
            result["valueBits"] = Int(value.timeIntervalSince1970)
        case "SleepEditGuard.isDisjoint/4":
            guard record.comparison == "exact", let newStart = record.args.newStart,
                  let newEnd = record.args.newEnd, let coverageStart = record.args.coverageStart,
                  let coverageEnd = record.args.coverageEnd else {
                throw RunnerError.invalidInput("invalid isDisjoint case \(record.id)")
            }
            result["valueBits"] = SleepEditGuard.isDisjoint(
                newStart: newStart, newEnd: newEnd,
                coverageStart: coverageStart, coverageEnd: coverageEnd
            )
        case "SleepEditGuard.clampedEditWindow/4":
            guard record.comparison == "exact", let start = record.args.start,
                  let end = record.args.end, let now = record.args.now,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid clampedEditWindow case \(record.id)")
            }
            let value = useDefaults
                ? SleepEditGuard.clampedEditWindow(start: start, end: end, now: now)
                : SleepEditGuard.clampedEditWindow(
                    start: start, end: end, now: now,
                    slackSec: try XCTUnwrap(record.effectiveArgs.slackSec)
                )
            result["valueBits"] = value.map { ["start": $0.start, "end": $0.end] } ?? NSNull()
        case "SleepStageVocabulary.isWake/1":
            guard record.comparison == "exact", var stage = record.args.stage else {
                throw RunnerError.invalidInput("invalid isWake case \(record.id)")
            }
            if negativeSide == "swift", record.id == "sleep_negative_source_probe" {
                stage = "asleep"
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = SleepStageVocabulary.isWake(stage)
        case "SleepWindowReclip.reclip/5":
            guard record.comparison == "exact", let sessionStart = record.args.sessionStart,
                  let oldEnd = record.args.oldEnd, let newStart = record.args.newStart,
                  let newEnd = record.args.newEnd else {
                throw RunnerError.invalidInput("invalid reclip case \(record.id)")
            }
            let value = SleepWindowReclip.reclip(
                stagesJSON: record.args.stagesJSON, sessionStart: sessionStart,
                oldEnd: oldEnd, newStart: newStart, newEnd: newEnd
            )
            result["valueBits"] = try value.map(normalizedSleepJSON) ?? NSNull()
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
        case "HeartRateRecovery.calculate/4":
            guard record.comparison == "exact", let input = record.args.samples,
                  let workoutStart = record.args.workoutStart,
                  let workoutEnd = record.args.workoutEnd, let maxHR = record.args.maxHR else {
                throw RunnerError.invalidInput("invalid HeartRateRecovery.calculate/4 case \(record.id)")
            }
            var value = HeartRateRecovery.calculate(
                samples: hrSamples(input), workoutStart: workoutStart,
                workoutEnd: workoutEnd, maxHR: maxHR
            )
            if negativeSide == "swift", record.id == "heart_rate_recovery_negative_probe",
               let current = value {
                value = HeartRateRecovery.Result(
                    endHR: current.endHR,
                    after1Minute: current.after1Minute.map { $0 + 1 },
                    after2Minutes: current.after2Minutes,
                    after5Minutes: current.after5Minutes
                )
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = value.map { heartRateRecoveryPayload($0) } ?? NSNull()
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
        case "RecoveryScorer.chargeDrivers/8=RecoveryDrivers.chargeDrivers/8":
            guard record.comparison == "exact", let hrv = record.args.hrv,
                  let rhr = record.args.rhr, let hrvInput = record.args.hrvBaseline,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid chargeDrivers case \(record.id)")
            }
            let hrvBaseline = try baselineState(hrvInput, record: record)
            let value: [ChargeDriver]
            if useDefaults {
                value = RecoveryScorer.chargeDrivers(
                    hrv: hrv, rhr: rhr, resp: record.args.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.args.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.args.respBaseline, record: record),
                    sleepPerf: record.args.sleepPerf
                )
            } else {
                value = RecoveryScorer.chargeDrivers(
                    hrv: hrv, rhr: rhr, resp: record.effectiveArgs.resp,
                    hrvBaseline: hrvBaseline,
                    rhrBaseline: try baselineStateOptional(record.effectiveArgs.rhrBaseline, record: record),
                    respBaseline: try baselineStateOptional(record.effectiveArgs.respBaseline, record: record),
                    sleepPerf: record.effectiveArgs.sleepPerf,
                    skinTempDev: record.effectiveArgs.skinTempDev
                )
            }
            try validateChargeDriverFormatting(value, record: record)
            var encoded = value.map { driver in
                [
                    "baselineText": ["text": driver.baselineText],
                    "deltaPoints": driver.deltaPoints,
                    "label": ["text": driver.label],
                    "valueText": ["text": driver.valueText],
                    "verdict": ["text": driver.verdict],
                ] as [String: Any]
            }
            if negativeSide == "swift", record.id == "recovery_drivers_negative_delta_probe",
               !encoded.isEmpty, let delta = encoded[0]["deltaPoints"] as? Int {
                encoded[0]["deltaPoints"] = delta + 1
                result["negativeSide"] = "swift"
            }
            if negativeSide == "swift", record.id == "recovery_drivers_negative_order_probe",
               encoded.count >= 2 {
                encoded.swapAt(0, 1)
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = encoded
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
        case "RecoveryForecaster.forecast/6":
            guard record.comparison == "exact", let recentCharge = record.args.recentCharge,
                  let planned = record.args.plannedSleepHours,
                  let useDefaults = record.args.useDefaults else {
                throw RunnerError.invalidInput("invalid RecoveryForecast case \(record.id)")
            }
            let value: RecoveryForecast?
            if useDefaults {
                value = RecoveryForecaster.forecast(
                    recentCharge: recentCharge, todayEffort: record.args.todayEffort,
                    plannedSleepHours: planned
                )
            } else {
                guard let recentEffort = record.effectiveArgs.recentEffort,
                      let needNights = record.effectiveArgs.needNights else {
                    throw RunnerError.invalidInput("invalid explicit RecoveryForecast controls \(record.id)")
                }
                value = RecoveryForecaster.forecast(
                    recentCharge: recentCharge, recentEffort: recentEffort,
                    todayEffort: record.args.todayEffort, plannedSleepHours: planned,
                    needHours: record.effectiveArgs.needHours, needNights: needNights
                )
            }
            var encoded: Any = value.map(recoveryForecastBits) ?? NSNull()
            if negativeSide == "swift", record.id == "recovery_forecast_negative_output_probe",
               var forecast = encoded as? [String: Any] {
                forecast["low"] = exactBit(1.0)
                encoded = forecast
                result["negativeSide"] = "swift"
            }
            if var forecast = encoded as? [String: Any],
               record.args.characterizeForecastConstants == true {
                forecast["constants"] = recoveryForecastConstants()
                encoded = forecast
            }
            result["valueBits"] = encoded
        case "RecoveryForecaster.mean/1":
            guard record.comparison == "epsilon", var values = record.args.values else {
                throw RunnerError.invalidInput("invalid RecoveryForecast mean case \(record.id)")
            }
            if negativeSide == "swift", record.id == "recovery_forecast_negative_source_probe" {
                values.append(100.0)
                result["negativeSide"] = "swift"
            }
            result["value"] = try finite(RecoveryForecaster.mean(values), record: record)
        case "RecoveryForecaster.sampleSD/1":
            guard record.comparison == "epsilon", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid RecoveryForecast sampleSD case \(record.id)")
            }
            result["value"] = try finite(RecoveryForecaster.sampleSD(values), record: record)
        case "RecoveryForecaster.leastSquaresSlope/1":
            guard record.comparison == "epsilon", let values = record.args.values else {
                throw RunnerError.invalidInput("invalid RecoveryForecast slope case \(record.id)")
            }
            result["value"] = try finite(RecoveryForecaster.leastSquaresSlope(values), record: record)
        case "RecoveryForecaster.clamp/3":
            guard record.comparison == "exact", let x = record.args.x,
                  let lo = record.args.lo, let hi = record.args.hi else {
                throw RunnerError.invalidInput("invalid RecoveryForecast clamp case \(record.id)")
            }
            result["valueBits"] = exactBit(RecoveryForecaster.clamp(x, lo, hi))
        case "WatchRecovery.compute/4":
            guard record.comparison == "exact", let hrvHistory = record.args.hrvHistory,
                  let rhrHistory = record.args.rhrHistory else {
                throw RunnerError.invalidInput("invalid WatchRecovery.compute/4 case \(record.id)")
            }
            let value = WatchRecovery.compute(
                todaySDNN: record.args.todayHrv, todayRHR: record.args.todayRhr,
                sdnnHistory: hrvHistory, rhrHistory: rhrHistory
            )
            var encoded: [String: Any] = [
                "recovery": value.recovery.map(exactBit) ?? NSNull(),
                "confidence": ["text": value.confidence.rawValue],
                "minBaselineNights": WatchRecovery.minBaselineNights,
            ]
            if negativeSide == "swift", record.id == "watch_recovery_negative_score_probe" {
                encoded["recovery"] = exactBit((value.recovery ?? 0.0) + 1.0)
                result["negativeSide"] = "swift"
            }
            if negativeSide == "swift", record.id == "watch_recovery_negative_confidence_probe" {
                encoded["confidence"] = ["text": "calibrating"]
                result["negativeSide"] = "swift"
            }
            result["valueBits"] = encoded
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
            guard record.comparison == "epsilon", let history = record.args.history?.generated,
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

    private func normalizedSleepJSON(_ text: String) throws -> [String: Any] {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) else {
            throw RunnerError.invalidInput("Sleep reclip returned invalid JSON")
        }
        if let segments = object as? [[String: Any]] {
            let value: [[String: Any]] = try segments.map { segment in
                guard Set(segment.keys) == ["start", "end", "stage"],
                      let start = (segment["start"] as? NSNumber)?.intValue,
                      let end = (segment["end"] as? NSNumber)?.intValue,
                      let stage = segment["stage"] as? String else {
                    throw RunnerError.invalidInput("Sleep reclip returned an invalid segment")
                }
                return ["start": start, "end": end, "stage": ["text": stage]]
            }
            return ["shape": ["text": "segments"], "value": value]
        }
        if let minutes = object as? [String: Any] {
            guard Set(minutes.keys) == ["awake", "light", "deep", "rem"] else {
                throw RunnerError.invalidInput("Sleep reclip returned unexpected minutes fields")
            }
            var value: [String: String] = [:]
            for key in ["awake", "light", "deep", "rem"] {
                guard let number = minutes[key] as? NSNumber else {
                    throw RunnerError.invalidInput("Sleep reclip returned incomplete minutes")
                }
                value[key] = exactBit(number.doubleValue)
            }
            return ["shape": ["text": "minutes"], "value": value]
        }
        throw RunnerError.invalidInput("Sleep reclip returned an unsupported JSON shape")
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

    private func recoveryForecastBits(_ value: RecoveryForecast) -> [String: Any] {
        [
            "band": exactBit(value.band), "baseline": exactBit(value.baseline),
            "confidence": ["text": value.confidence.rawValue],
            "high": exactBit(value.high), "low": exactBit(value.low),
            "need": exactBit(value.needHours), "nights": value.nights,
            "planned": exactBit(value.plannedSleepHours), "score": exactBit(value.charge),
        ]
    }

    private func recoveryForecastConstants() -> [String: Any] {
        [
            "baselineWindow": RecoveryForecaster.baselineWindow,
            "defaultNeedHours": exactBit(RecoveryForecaster.defaultNeedHours),
            "effortSpread": exactBit(RecoveryForecaster.effortSpread),
            "effortWindow": RecoveryForecaster.effortWindow,
            "minBandPoints": exactBit(RecoveryForecaster.minBandPoints),
            "minBaselineNights": RecoveryForecaster.minBaselineNights,
            "reversionAdjCap": exactBit(RecoveryForecaster.reversionAdjCap),
            "reversionWeight": exactBit(RecoveryForecaster.reversionWeight),
            "sleepOverCap": exactBit(RecoveryForecaster.sleepOverCap),
            "sleepWeight": exactBit(RecoveryForecaster.sleepWeight),
            "solidNeedNights": RecoveryForecaster.solidNeedNights,
            "strainAdjCap": exactBit(RecoveryForecaster.strainAdjCap),
            "strainWeight": exactBit(RecoveryForecaster.strainWeight),
            "thinBandPoints": exactBit(RecoveryForecaster.thinBandPoints),
            "trustedNights": RecoveryForecaster.trustedNights,
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

    private func sstBlocks(_ input: [SSTBlockInput]?) throws -> [SleepStageTotals.NightBlock] {
        try XCTUnwrap(input).map { .init(start: $0.start, end: $0.end) }
    }

    private func minutesPayload(_ value: SleepStageTotals.Minutes?) -> Any {
        guard let value else { return NSNull() }
        return ["awake":exactBit(value.awake),"light":exactBit(value.light),"deep":exactBit(value.deep),"rem":exactBit(value.rem),"asleep":exactBit(value.asleep),"inBed":exactBit(value.inBed)]
    }

    private func dailyPayload(_ value: SleepStageTotals.DailySleep?) -> Any {
        guard let value else { return NSNull() }
        return ["totalSleepMin":exactBit(value.totalSleepMin),"efficiency":exactBit(value.efficiency),"deepMin":exactBit(value.deepMin),"remMin":exactBit(value.remMin),"lightMin":exactBit(value.lightMin)]
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

    private func validateChargeDriverFormatting(
        _ drivers: [ChargeDriver], record: InputRecord
    ) throws {
        guard !drivers.isEmpty else { return }
        let locale = Locale(identifier: "en_US_POSIX")
        if let resp = record.effectiveArgs.resp, let baseline = record.effectiveArgs.respBaseline,
           let baselineValue = baseline.baseline {
            guard let row = drivers.first(where: { $0.label == "Respiratory rate" }) else {
                throw RunnerError.invalidInput("chargeDrivers omitted respiration row \(record.id)")
            }
            let expectedValue = String(
                format: "%.1f br/min", locale: locale, resp
            )
            let expectedBaseline = String(
                format: "%.1f br/min baseline", locale: locale, baselineValue
            )
            guard row.valueText == expectedValue, row.baselineText == expectedBaseline else {
                throw RunnerError.invalidInput("chargeDrivers respiration formatting is not en_US_POSIX \(record.id)")
            }
        }
        if let skin = record.effectiveArgs.skinTempDev {
            guard let row = drivers.first(where: { $0.label == "Skin temperature" }) else {
                throw RunnerError.invalidInput("chargeDrivers omitted skin-temperature row \(record.id)")
            }
            let expected = String(
                format: "%+.1f C vs baseline", locale: locale, skin
            )
            guard row.valueText == expected else {
                throw RunnerError.invalidInput(
                    "chargeDrivers skin formatting is not en_US_POSIX \(record.id): "
                    + "actual=\(row.valueText.debugDescription) expected=\(expected.debugDescription)"
                )
            }
        }
    }

    private func hrSamples(_ input: [HRInput]) -> [HRSample] {
        input.map { HRSample(ts: $0.ts, bpm: $0.bpm) }
    }

    private func heartRateRecoveryPayload(_ value: HeartRateRecovery.Result) -> [String: Any] {
        [
            "endHR": value.endHR,
            "after1Minute": value.after1Minute.map { $0 as Any } ?? NSNull(),
            "after2Minutes": value.after2Minutes.map { $0 as Any } ?? NSNull(),
            "after5Minutes": value.after5Minutes.map { $0 as Any } ?? NSNull(),
        ]
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
