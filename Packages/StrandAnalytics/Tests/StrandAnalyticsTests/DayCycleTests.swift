import XCTest
@testable import StrandAnalytics

final class DayCycleTests: XCTestCase {
    func testDefaultsAndCalendarMode() {
        XCTAssertEqual(DayCycleMode.persisted(nil), .sleepOnset)
        XCTAssertEqual(DayCycleMode.persisted("midnight"), .midnight)
        let window = DayCycleResolver.activeWindow(mode: .midnight, latestSleep: nil, now: 86_500,
                                                   offsetSec: 0, reliableAwakeCoverage: false)
        XCTAssertEqual(window.startInclusive, 86_400)
        XCTAssertEqual(window.source, .calendar)
    }

    func testMissingWakeEvidenceFallsBackAtFirstSafeMidnight() {
        let sleep = DayCycleWindow(id: "sleep", startInclusive: 20 * 3_600, endExclusive: 0,
                                   displayDay: "1970-01-01", source: .detectedSleep)
        let fallback = DayCycleResolver.fallbackMidnight(after: sleep.startInclusive, offsetSec: 0)
        XCTAssertEqual(fallback, 2 * 86_400)
        XCTAssertEqual(DayCycleResolver.activeWindow(mode: .sleepOnset, latestSleep: sleep,
                                                     now: fallback, offsetSec: 0,
                                                     reliableAwakeCoverage: false).source,
                       .syntheticMidnight)
    }

    func testCoverageSegmentsPreferPriorityWithoutCrossingDeviceCounters() {
        let window = PhysiologicalSteps.CycleWindow(sleepId: "night", onset: 100, endExclusive: 500)
        let segments = PhysiologicalSteps.ownerSegmentsFromCoverage(window, coverage: [
            .init(owner: "secondary", onset: 100, endExclusive: 350, priority: 1),
            .init(owner: "active", onset: 200, endExclusive: 500, priority: 0),
        ], fallbackOwner: "secondary")
        XCTAssertEqual(segments, [
            .init(owner: "secondary", onset: 100, endExclusive: 200),
            .init(owner: "active", onset: 200, endExclusive: 500),
        ])
    }
}
