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
        let denominator: Double?
        let minBeatsPerWindow: Int?
        let rr: [RRInput]?
        let stepSec: Int?
        let trimp: Double?
        let windowSec: Int?
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
}
