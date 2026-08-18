import XCTest
@testable import StrandAnalytics
import WhoopStore

/// #16: `backfillWorkout` must carry over EVERY field of the real workout it doesn't backfill —
/// including `steps` (#1058). Regression for the bug where rebuilding the row via the initializer
/// dropped `steps`, silently nulling stored per-session steps and making an all-fields-present row
/// compare `!=` to its input, so the caller "backfilled" (rewrote) a row that needed no write.
final class WorkoutBackfillStepsTests: XCTestCase {

    private func rowWithSteps(avgHr: Int? = nil, maxHr: Int? = nil, energyKcal: Double? = nil,
                              strain: Double? = nil, steps: Int? = 4_200) -> WorkoutRow {
        WorkoutRow(startTs: 1_000, endTs: 1_600, sport: "Running", source: "activity-file",
                   durationS: 600, energyKcal: energyKcal, avgHr: avgHr, maxHr: maxHr,
                   strain: strain, distanceM: 3_210.5, zonesJSON: "{\"2\":50,\"3\":50}",
                   notes: "kept verbatim", steps: steps)
    }

    func testStepsSurviveBackfillOfMissingFields() {
        // Steps are stored, the HR/calorie fields are missing -> the bout may fill those,
        // but the stored steps must come through untouched.
        let real = rowWithSteps()
        let filled = WorkoutDetector.backfillWorkout(real, avgBpm: 150, peakHR: 170,
                                                     caloriesKcal: 80.0, strain: 9.5)
        XCTAssertEqual(filled.steps, 4_200, "stored per-session steps must never be nulled by backfill")
        XCTAssertEqual(filled.avgHr, 150)
        XCTAssertEqual(filled.maxHr, 170)
        XCTAssertEqual(filled.energyKcal, 80.0)
        XCTAssertEqual(filled.strain, 9.5)
    }

    func testRowWithStepsAndEverythingPresentIsUnchanged() {
        // Every field is present: the result must be `==` the input, so the caller's
        // didBackfill (result != real) does not fire a needless write.
        let real = rowWithSteps(avgHr: 140, maxHr: 160, energyKcal: 50.0, strain: 8.0)
        let filled = WorkoutDetector.backfillWorkout(real, avgBpm: 150, peakHR: 170,
                                                     caloriesKcal: 80.0, strain: 9.5)
        XCTAssertEqual(filled, real,
                       "nothing to fill -> row must be byte-identical, or the caller misreads it as a backfill")
    }
}
