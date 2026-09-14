import XCTest
@testable import WhoopProtocol

/// Swift twin of the Kotlin `FirmwareFlashUiPolicyTest` (android/.../ui/FirmwareFlashUiPolicyTest.kt).
final class FirmwareUiPolicyTests: XCTestCase {

    func testVersionComparisonCoversUpgradeEqualAndDowngrade() {
        XCTAssertEqual(compareFirmwareVersions(current: "50.9.1.2", target: "50.10.1.2"), .upgrade)
        XCTAssertEqual(compareFirmwareVersions(current: "50.10.1.2", target: "50.10.1.2"), .same)
        XCTAssertEqual(compareFirmwareVersions(current: "50.10.1.2", target: "50.9.9.9"), .downgrade)
    }

    func testVersionComparisonIsNumericAndRespondsToChangedLiveVersion() {
        let target = "50.10.1.2"
        XCTAssertEqual(compareFirmwareVersions(current: "50.9.99.99", target: target), .upgrade)
        XCTAssertEqual(compareFirmwareVersions(current: "50.10.1.2", target: target), .same)
        XCTAssertEqual(compareFirmwareVersions(current: "50.11.0.0", target: target), .downgrade)
    }

    func testUnknownMalformedAndOutOfRangeVersionsRemainIncomparable() {
        let target = "50.10.1.2"
        for current in [nil, "", "50.10.1", "50.10.x.2", "50.-1.1.2", "4294967296.1.2.3"] as [String?] {
            XCTAssertEqual(compareFirmwareVersions(current: current, target: target), .incomparable, "current=\(String(describing: current))")
        }
        XCTAssertEqual(compareFirmwareVersions(current: "50.10.1.2", target: "50.10.1.2.0"), .incomparable)
    }

    func testFileReplacementAndClearAreLockedAcrossEveryActiveDeviceStage() {
        let activeStages: [FirmwareUpdateStage] = [
            .preparing, .writing, .remoteValidating, .readyToActivate, .activationRequested, .reconnecting, .paused,
        ]
        for stage in activeStages {
            XCTAssertFalse(FirmwareFlashUiPolicy.canChooseFile(stage, uiBusy: false), "choose during \(stage)")
            XCTAssertFalse(FirmwareFlashUiPolicy.canClear(stage, uiBusy: false), "clear during \(stage)")
        }
    }

    func testSettledAndTerminalStagesUnlockSafeLocalActions() {
        for stage in [FirmwareUpdateStage.imageReady, .failed, .cancelled, .deviceReconnected] {
            XCTAssertTrue(FirmwareFlashUiPolicy.canChooseFile(stage, uiBusy: false), "choose after \(stage)")
            XCTAssertTrue(FirmwareFlashUiPolicy.canClear(stage, uiBusy: false), "clear after \(stage)")
        }
        XCTAssertTrue(FirmwareFlashUiPolicy.canChooseFile(.empty, uiBusy: false))
        XCTAssertFalse(FirmwareFlashUiPolicy.canClear(.empty, uiBusy: false))
        XCTAssertFalse(FirmwareFlashUiPolicy.canChooseFile(.empty, uiBusy: true))
        XCTAssertFalse(FirmwareFlashUiPolicy.canClear(.imageReady, uiBusy: true))
    }

    func testProgressIsShownOnlyThroughRemoteValidationAndTheActivationGate() {
        let progressStages: Set<FirmwareUpdateStage> = [.preparing, .writing, .remoteValidating, .readyToActivate, .paused]
        for stage in FirmwareUpdateStage.allCases {
            XCTAssertEqual(FirmwareFlashUiPolicy.showsProgress(stage), progressStages.contains(stage), "\(stage)")
        }
    }

    func testReaderRejectsEmptyAndOversize() {
        XCTAssertThrowsError(try validateFirmwareDocumentSize(byteCount: 0)) { XCTAssertTrue($0 is EmptyFirmwareImageError) }
        XCTAssertThrowsError(try validateFirmwareDocumentSize(byteCount: FirmwareImageParser.maxImageBytes + 1)) {
            XCTAssertTrue($0 is FirmwareImageTooLargeError)
        }
        XCTAssertThrowsError(try validateFirmwareDocumentSize(byteCount: 5, maxBytes: 4)) {
            XCTAssertTrue($0 is FirmwareImageTooLargeError)
        }
        XCTAssertNoThrow(try validateFirmwareDocumentSize(byteCount: 5))
    }

    func testByteLabelsStayDeterministic() {
        XCTAssertEqual(formatFirmwareBytes(5), "5 B")
        XCTAssertEqual(formatFirmwareBytes(1536), "1.5 KiB")
        XCTAssertEqual(formatFirmwareBytes(2 * 1024 * 1024), "2.00 MiB")
    }
}
