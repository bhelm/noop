import XCTest
import StrandDesign
@testable import Strand

final class NightDetailDebtFormattingTests: XCTestCase {
    func testTenMinuteDebtBoundary() {
        XCTAssertEqual(nightDetailDebtCaption(9.9), "On target")
        XCTAssertEqual(nightDetailDebtColor(9.9), StrandPalette.statusPositive)

        XCTAssertEqual(nightDetailDebtCaption(10.0), "Below need")
        XCTAssertEqual(nightDetailDebtColor(10.0), StrandPalette.statusWarning)
    }

    func testImportedDebtAboveBoundaryRemainsDebt() {
        XCTAssertEqual(nightDetailDebtCaption(12.5), "Below need")
        XCTAssertEqual(nightDetailDebtColor(12.5), StrandPalette.statusWarning)
    }
}
