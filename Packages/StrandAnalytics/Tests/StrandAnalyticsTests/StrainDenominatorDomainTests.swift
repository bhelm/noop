import XCTest
@testable import StrandAnalytics

/// Domain of the log map's denominator D (#36).
///
/// D ≤ 1 has no ln-based score: ln(1) = 0 divides to ±∞, ln(D) < 0 below 1 flips the sign, and ln(D)
/// is NaN at or below 0. Before the guard, Swift returned `+Inf` for D = 1 while Kotlin's
/// `roundToLong()` saturated to 9.223372036854776E16 — the same input, two different answers.
/// The Kotlin twin is `StrainScorerDenominatorDomainTest`; its expected literals are the stdout of
/// this file's formula compiled standalone.
final class StrainDenominatorDomainTests: XCTestCase {

    func testDenominatorAtOrBelowOneScoresZero() {
        for denominator in [1.0, 0.5, 0.0, -3.0, Double.nan] {
            let s = StrainScorer.trimpToStrain(1, denominator: denominator)
            XCTAssertTrue(s.isFinite, "D = \(denominator) produced a non-finite Effort")
            XCTAssertEqual(s, 0.0, accuracy: 0, "D = \(denominator)")
        }
    }

    func testValidDenominatorsAreUnchanged() {
        XCTAssertEqual(StrainScorer.trimpToStrain(1, denominator: 7201), 7.8, accuracy: 1e-9)
        XCTAssertEqual(StrainScorer.trimpToStrain(100, denominator: 7201), 51.96, accuracy: 1e-9)
        XCTAssertEqual(StrainScorer.trimpToStrain(7200, denominator: 7201), 100.0, accuracy: 1e-9)
        XCTAssertEqual(StrainScorer.trimpToStrain(1, denominator: 2), 100.0, accuracy: 1e-9)
    }

    /// Just above the boundary the score is enormous but finite, and it must stay a Double on both
    /// platforms — this is the value Kotlin's `roundToLong()` clipped to Long.MAX / 100.
    func testDenominatorJustAboveOneStaysFinite() {
        let s = StrainScorer.trimpToStrain(1, denominator: 1.0000000000000002)
        XCTAssertTrue(s.isFinite)
        XCTAssertEqual(s, 3.1216573840826803e+17, accuracy: 0)
    }
}
