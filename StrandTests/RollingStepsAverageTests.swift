import XCTest
@testable import Strand

final class RollingStepsAverageTests: XCTestCase {
    func testWindowIncludesSelectedDayAnd29PriorCalendarDays() {
        XCTAssertEqual(RollingStepsAverage.startDay(ending: "2026-03-01"), "2026-01-31")
        XCTAssertEqual(RollingStepsAverage.startDay(ending: "2024-03-01"), "2024-02-01")
        let result = RollingStepsAverage.calculate(readings: [
            ("2026-01-30", 99999), ("2026-01-31", 1000),
            ("2026-03-01", 3000), ("2026-03-02", 99999)
        ], ending: "2026-03-01")
        XCTAssertEqual(result.mean, 2000)
        XCTAssertEqual(result.observedDays, 2)
    }

    func testMissingDaysAreExcludedButRecordedZeroCounts() {
        let result = RollingStepsAverage.calculate(readings: [
            ("2026-09-01", 0), ("2026-09-14", 10000)
        ], ending: "2026-09-14")
        XCTAssertEqual(result.mean, 5000)
        XCTAssertEqual(result.observedDays, 2)
        XCTAssertNil(RollingStepsAverage.calculate(readings: [], ending: "2026-09-14").mean)
    }

    func testInvalidReadingsDoNotInflateCoverage() {
        let result = RollingStepsAverage.calculate(readings: [
            ("2026-09-01", -1), ("2026-09-02", .nan), ("2026-09-03", .infinity),
            ("2026-09-04", 2000), ("2026-09-04", 3000)
        ], ending: "2026-09-14")
        XCTAssertEqual(result.mean, 3000)
        XCTAssertEqual(result.observedDays, 1)
    }

    func testAverageRequiresExplicitOptInAndUsesAndroidPreferenceKey() {
        XCTAssertFalse(KeyMetric.defaultOrder.contains(.stepsAverage30))
        XCTAssertFalse(KeyMetricPrefs.decodeEnabled("").contains(.stepsAverage30))
        XCTAssertEqual(KeyMetricPrefs.decodeEnabled("stepsAverage30"), [.stepsAverage30])
    }

    func testCombinedDestinationDoesNotReplaceSourceSpecificCatalogEntries() throws {
        let combined = try XCTUnwrap(MetricCatalog.metric(key: "steps", source: MetricCatalog.combinedStepsSource))
        XCTAssertEqual(combined.source, MetricCatalog.combinedStepsSource)
        XCTAssertFalse(MetricCatalog.all.contains(combined))
        for source in ["my-whoop", "apple-health", "xiaomi-band"] {
            XCTAssertEqual(MetricCatalog.metric(key: "steps", source: source)?.source, source)
        }
        XCTAssertEqual(MetricCatalog.metric(key: "steps_est", source: "my-whoop")?.key, "steps_est")
    }
}
