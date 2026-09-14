import Foundation

// The firmware update state machine, shared by the BLE integration and the UI. Twin of the Kotlin
// `FirmwareUpdateStage` / `FirmwareUpdateState` / `FirmwareUpdateTransitions`. The `status` strings are
// the same English text the Android transitions emit — on both platforms this is a plain state string,
// not a localized resource, so keeping them identical is the parity contract (the localized copy lives
// in the `firmware_flash_*` resources the UI renders around this state).

public enum FirmwareUpdateStage: CaseIterable, Sendable {
    case empty
    case imageReady
    case preparing
    case writing
    case remoteValidating
    case readyToActivate
    case activationRequested
    case reconnecting
    case deviceReconnected
    case paused
    case failed
    case cancelled
}

public struct FirmwareUpdateState: Sendable {
    public var stage: FirmwareUpdateStage
    public var image: FirmwareImageInfo?
    public var bytesAcknowledged: Int
    public var totalBytes: Int
    public var status: String
    public var error: String?
    public var deviceEligible: Bool
    public var lockedDeviceLabel: String?

    public init(stage: FirmwareUpdateStage = .empty,
                image: FirmwareImageInfo? = nil,
                bytesAcknowledged: Int = 0,
                totalBytes: Int? = nil,
                status: String = "Choose an original .zbin update image",
                error: String? = nil,
                deviceEligible: Bool = false,
                lockedDeviceLabel: String? = nil) {
        self.stage = stage
        self.image = image
        self.bytesAcknowledged = bytesAcknowledged
        self.totalBytes = totalBytes ?? (image?.byteCount ?? 0)
        self.status = status
        self.error = error
        self.deviceEligible = deviceEligible
        self.lockedDeviceLabel = lockedDeviceLabel
    }

    public var progress: Float {
        guard totalBytes > 0 else { return 0 }
        return min(1, max(0, Float(bytesAcknowledged) / Float(totalBytes)))
    }
    public var canStart: Bool { stage == .imageReady && deviceEligible }
    public var canActivate: Bool { stage == .readyToActivate }
    public var canResume: Bool { stage == .paused && deviceEligible }
    public var canCancel: Bool {
        [.preparing, .writing, .remoteValidating, .readyToActivate, .paused].contains(stage)
    }
}

/// Pure state transitions shared by the BLE integration and unit tests.
public enum FirmwareUpdateTransitions {
    public static func selected(_ image: FirmwareImageInfo, eligible: Bool) -> FirmwareUpdateState {
        FirmwareUpdateState(
            stage: .imageReady,
            image: image,
            totalBytes: image.byteCount,
            status: eligible ? "Image validated locally. Ready to transfer."
                : "Image validated. Connect and bond a WHOOP 5/MG strap to continue.",
            deviceEligible: eligible)
    }

    public static func begin(_ state: FirmwareUpdateState, deviceLabel: String) -> FirmwareUpdateState {
        var next = state
        next.stage = .preparing
        next.bytesAcknowledged = 0
        next.status = "Preparing the strap's update slot"
        next.error = nil
        next.lockedDeviceLabel = deviceLabel
        next.deviceEligible = true
        return next
    }

    public static func writing(_ state: FirmwareUpdateState, acknowledged: Int) -> FirmwareUpdateState {
        var next = state
        let clamped = min(max(0, acknowledged), state.totalBytes)
        next.stage = .writing
        next.bytesAcknowledged = clamped
        next.status = "Writing firmware: \(clamped) / \(state.totalBytes) bytes acknowledged"
        return next
    }

    public static func retrying(_ state: FirmwareUpdateState, acknowledged: Int, attempt: Int, maximum: Int) -> FirmwareUpdateState {
        var next = state
        next.stage = .writing
        next.bytesAcknowledged = min(max(0, acknowledged), state.totalBytes)
        next.status = "Retrying firmware chunk at offset \(acknowledged) (\(attempt) / \(maximum))"
        return next
    }

    public static func paused(_ state: FirmwareUpdateState, acknowledged: Int, reason: String) -> FirmwareUpdateState {
        var next = state
        next.stage = .paused
        next.bytesAcknowledged = min(max(0, acknowledged), state.totalBytes)
        next.status = "Transfer paused at the last acknowledged offset. Resume is available only on this connection."
        next.error = reason
        next.deviceEligible = true
        return next
    }

    public static func resuming(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .preparing
        next.status = "Preparing the same strap connection to resume at offset \(state.bytesAcknowledged)"
        next.error = nil
        return next
    }

    public static func remoteValidating(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .remoteValidating
        next.bytesAcknowledged = state.totalBytes
        next.status = "Transfer complete. Waiting for the strap's asynchronous integrity result."
        return next
    }

    public static func ready(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .readyToActivate
        next.status = "The strap verified the image. Activation needs your confirmation."
        return next
    }

    public static func activationRequested(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .activationRequested
        next.status = "Activation accepted. The strap is restarting."
        return next
    }

    public static func reconnecting(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .reconnecting
        next.status = "The strap is restarting. Waiting for it to reconnect."
        return next
    }

    public static func reconnected(_ state: FirmwareUpdateState, reportedVersion: String?) -> FirmwareUpdateState {
        var next = state
        let suffix = reportedVersion.map { " and reports firmware \($0)" } ?? ""
        next.stage = .deviceReconnected
        next.status = "The strap reconnected\(suffix)."
        return next
    }

    public static func failed(_ state: FirmwareUpdateState, reason: String) -> FirmwareUpdateState {
        var next = state
        next.stage = .failed
        next.status = "Firmware update stopped"
        next.error = reason
        next.deviceEligible = false
        return next
    }

    public static func cancelled(_ state: FirmwareUpdateState) -> FirmwareUpdateState {
        var next = state
        next.stage = .cancelled
        next.status = "Update session cancelled. The app will not resume or activate it automatically."
        next.error = nil
        next.deviceEligible = false
        return next
    }

    public static func eligibility(_ state: FirmwareUpdateState, eligible: Bool) -> FirmwareUpdateState {
        guard state.stage == .imageReady else { return state }
        var next = state
        next.deviceEligible = eligible
        next.status = eligible ? "Image validated locally. Ready to transfer."
            : "Image validated. Connect and bond a WHOOP 5/MG strap to continue."
        return next
    }
}

// MARK: - Version comparison + UI policy (pure)

public enum FirmwareVersionRelation: Sendable {
    case downgrade
    case same
    case upgrade
    case incomparable
}

private let firmwareMaxVersionComponent: UInt64 = UInt64(UInt32.max)

/// Compares canonical four-component firmware versions numerically, never lexicographically. Twin of the
/// Kotlin `compareFirmwareVersions`.
public func compareFirmwareVersions(current currentVersion: String?, target targetVersion: String) -> FirmwareVersionRelation {
    guard let current = parseFirmwareVersion(currentVersion), let target = parseFirmwareVersion(targetVersion) else {
        return .incomparable
    }
    for index in current.indices {
        if target[index] < current[index] { return .downgrade }
        if target[index] > current[index] { return .upgrade }
    }
    return .same
}

private func parseFirmwareVersion(_ version: String?) -> [UInt64]? {
    guard let version else { return nil }
    let components = version.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ".", omittingEmptySubsequences: false)
    guard components.count == 4 else { return nil }
    var result: [UInt64] = []
    for component in components {
        if component.isEmpty || component.contains(where: { !$0.isASCII || !$0.isNumber }) { return nil }
        guard let value = UInt64(component), value <= firmwareMaxVersionComponent else { return nil }
        result.append(value)
    }
    return result
}

/// Which local file actions and progress the UI allows for a stage. Twin of the Kotlin
/// `FirmwareFlashUiPolicy`.
public enum FirmwareFlashUiPolicy {
    private static let settledStages: Set<FirmwareUpdateStage> = [
        .empty, .imageReady, .failed, .cancelled, .deviceReconnected,
    ]

    public static func canChooseFile(_ stage: FirmwareUpdateStage, uiBusy: Bool) -> Bool {
        !uiBusy && settledStages.contains(stage)
    }

    public static func canClear(_ stage: FirmwareUpdateStage, uiBusy: Bool) -> Bool {
        !uiBusy && settledStages.contains(stage) && stage != .empty
    }

    public static func showsProgress(_ stage: FirmwareUpdateStage) -> Bool {
        [.preparing, .writing, .remoteValidating, .readyToActivate, .paused].contains(stage)
    }
}

// MARK: - Bounded firmware document reader (pure)

public struct FirmwareImageTooLargeError: Error { public init() {} }
public struct EmptyFirmwareImageError: Error { public init() {} }

/// Bytes-per-unit label matching the Kotlin `formatFirmwareBytes`.
public func formatFirmwareBytes(_ bytes: Int) -> String {
    if bytes >= 1024 * 1024 {
        return String(format: "%.2f MiB", locale: Locale(identifier: "en_US_POSIX"), Double(bytes) / (1024.0 * 1024.0))
    }
    if bytes >= 1024 {
        return String(format: "%.1f KiB", locale: Locale(identifier: "en_US_POSIX"), Double(bytes) / 1024.0)
    }
    return "\(bytes) B"
}

/// Bound a firmware document before reading it: reject an empty file and a declared-oversize one, and
/// stop reading a stream that grows past `maxBytes`. Twin of the Kotlin `readFirmwareBytes`; the file
/// importer supplies the bytes it already read, so here `data` is the loaded content and the check is
/// on size only (the streaming/declared-size split is an Android ContentResolver concern).
public func validateFirmwareDocumentSize(byteCount: Int, maxBytes: Int = FirmwareImageParser.maxImageBytes) throws {
    if byteCount > maxBytes { throw FirmwareImageTooLargeError() }
    if byteCount == 0 { throw EmptyFirmwareImageError() }
}
