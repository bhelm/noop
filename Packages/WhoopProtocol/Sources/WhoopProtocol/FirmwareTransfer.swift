import Foundation

// Transport-independent OTA transaction and its pure decision helpers. Twin of the Kotlin
// `FirmwareTransferEngine` / `FirmwareWhoop5ResponseDecoder` / matcher / policy objects
// (android/.../ble/FirmwareUpdate.kt). The BLE client supplies correlation and timeouts through
// `FirmwareTransferTransport`; the tests supply a deterministic fake. No CoreBluetooth here.
//
// Command numbers and payload shapes live here, not as hex frames in the app: the app frames each
// command through `puffinCommandFrame` at the write site, exactly as every other 5/MG command does.

/// The strap command numbers the firmware transaction uses. These match `CommandNumber` in
/// `whoop_protocol.json` (142 START_FIRMWARE_LOAD_NEW, 143 LOAD_FIRMWARE_DATA_NEW,
/// 144 PROCESS_FIRMWARE_IMAGE_NEW, 83 VERIFY_FIRMWARE_IMAGE, plus the three quiesce commands).
public enum FirmwareCommand {
    public static let prepare = 142
    public static let write = 143
    public static let activate = 144
    public static let verify = 83
    public static let stopRealtimeHr = 3
    public static let stopImu = 106
    public static let abortHistory = 20

    public static let maxChunkAttempts = 7
    public static let preTransferDelayMs = 500
    public static let commandTimeoutMs = 8_000
    public static let verifyTimeoutMs = 30_000
}

/// A decoded firmware COMMAND_RESPONSE, wire padding removed.
public struct FirmwareWireResponse: Sendable {
    public let command: Int
    public let originSequence: Int
    public let result: Int
    public let body: [UInt8]

    public init(command: Int, originSequence: Int, result: Int, body: [UInt8]) {
        self.command = command
        self.originSequence = originSequence
        self.result = result
        self.body = body
    }
}

/// Decode the WHOOP 5/MG command-response envelope used by the firmware transaction. Rejects anything
/// that is not a CRC-valid COMMAND_RESPONSE (0x24) or PUFFIN_COMMAND_RESPONSE (0x26) for one of the
/// firmware commands, with the exact body length that command's reply carries.
public enum FirmwareWhoop5ResponseDecoder {
    private static let commandResponse = 0x24
    private static let puffinCommandResponse = 0x26
    private static let bodyStart = 13

    /// Kotlin twin: `FirmwareWhoop5ResponseDecoder.decode`.
    public static func decode(_ frame: [UInt8]) -> FirmwareWireResponse? {
        if frame.count < 20 || frame[0] != 0xaa { return nil }
        let declaredLength = fwU16le(frame, 2)
        if declaredLength < 4 || declaredLength + 8 != frame.count { return nil }
        let payloadEnd = frame.count - 4
        if crc16Modbus(frame, 0, 6) != (UInt16(frame[6]) | (UInt16(frame[7]) << 8)) { return nil }
        if crc32(frame, 8, payloadEnd) != fwU32le(frame, payloadEnd) { return nil }

        let responseType = Int(frame[8])
        if responseType != commandResponse && responseType != puffinCommandResponse { return nil }
        let command = Int(frame[10])
        let bodyLength: Int
        switch command {
        case FirmwareCommand.verify:
            bodyLength = 1
        case FirmwareCommand.stopRealtimeHr, FirmwareCommand.abortHistory:
            bodyLength = 0
        case FirmwareCommand.stopImu, FirmwareCommand.prepare, FirmwareCommand.write, FirmwareCommand.activate:
            bodyLength = 2
        default:
            return nil
        }
        let unpaddedInnerLength = 5 + bodyLength
        let paddedInnerLength = (unpaddedInnerLength + 3) & ~3
        if payloadEnd - 8 != paddedInnerLength || bodyStart + bodyLength > payloadEnd { return nil }
        return FirmwareWireResponse(
            command: command,
            originSequence: Int(frame[11]),
            result: Int(frame[12]),
            body: Array(frame[bodyStart..<(bodyStart + bodyLength)])
        )
    }
}

/// The correlation key for one in-flight firmware command.
public struct FirmwareResponseKey {
    public let pendingSessionId: Int
    public let currentSessionId: Int
    public let expectedCommand: Int
    public let expectedSequence: Int
    public let actualCommand: Int
    public let actualSequence: Int
    public let sameDevice: Bool

    public init(pendingSessionId: Int, currentSessionId: Int, expectedCommand: Int, expectedSequence: Int,
                actualCommand: Int, actualSequence: Int, sameDevice: Bool) {
        self.pendingSessionId = pendingSessionId
        self.currentSessionId = currentSessionId
        self.expectedCommand = expectedCommand
        self.expectedSequence = expectedSequence
        self.actualCommand = actualCommand
        self.actualSequence = actualSequence
        self.sameDevice = sameDevice
    }
}

public enum FirmwareResponseMatcher {
    /// Kotlin twin: `FirmwareResponseMatcher.correlated`.
    public static func correlated(_ key: FirmwareResponseKey) -> Bool {
        key.pendingSessionId == key.currentSessionId && key.sameDevice
            && key.expectedCommand == key.actualCommand && key.expectedSequence == key.actualSequence
    }

    /// VERIFY is asynchronous; body[0] == 1 is the recovered final-result discriminator.
    /// Kotlin twin: `FirmwareResponseMatcher.isFinal`.
    public static func isFinal(command: Int, _ response: FirmwareWireResponse) -> Bool {
        command != FirmwareCommand.verify || (response.result != 2 && response.body.first == 1)
    }
}

public enum FirmwareUpdateAdmission {
    /// A 220-byte data chunk produces a 244-byte puffin frame, requiring ATT MTU 247 with its 3-byte header.
    /// Kotlin twin: `FirmwareUpdateAdmission.busyReason`.
    public static func busyReason(
        backfilling: Bool,
        writeInFlight: Bool,
        retryPending: Bool,
        queuedWrites: Int,
        negotiatedMtu: Int = Int.max,
        requiredMtu: Int = 247,
        cccdInFlight: Bool = false,
        queuedCccds: Int = 0
    ) -> String? {
        let mtuLabel = negotiatedMtu > 0 ? String(negotiatedMtu) : "not negotiated"
        if backfilling || writeInFlight || retryPending || queuedWrites > 0 {
            return "Bluetooth is busy with another strap operation. Wait for it to finish, then reselect the image."
        }
        if negotiatedMtu < requiredMtu {
            return "The Bluetooth MTU is \(mtuLabel); firmware chunks require MTU \(requiredMtu). Reconnect and reselect the image."
        }
        if cccdInFlight || queuedCccds > 0 {
            return "Bluetooth notification setup is still finishing. Wait for it to complete, then reselect the image."
        }
        return nil
    }
}

/// Keep ordinary BLE queue behavior intact while fail-closing session-bound OTA writes.
public enum FirmwareWriteQueuePolicy {
    /// Kotlin twin: `FirmwareWriteQueuePolicy.belongsToCurrentSession`.
    public static func belongsToCurrentSession(firmwareSessionId: Int?, currentSessionId: Int?) -> Bool {
        firmwareSessionId == nil || firmwareSessionId == currentSessionId
    }

    /// Kotlin twin: `FirmwareWriteQueuePolicy.mayRetryAfterAmbiguousRejection`.
    public static func mayRetryAfterAmbiguousRejection(firmwareSessionId: Int?) -> Bool {
        firmwareSessionId == nil
    }
}

public enum FirmwareActivationObservation {
    public static let disconnectTimeoutMs = 30_000
    public static let reconnectTimeoutMs = 60_000

    /// Kotlin twin: `FirmwareActivationObservation.sessionIsCurrent`.
    public static func sessionIsCurrent(observedSessionId: Int, currentSessionId: Int?) -> Bool {
        observedSessionId == currentSessionId
    }

    /// Kotlin twin: `FirmwareActivationObservation.canAcceptReportedVersion`.
    public static func canAcceptReportedVersion(_ stage: FirmwareUpdateStage) -> Bool {
        stage == .reconnecting
    }
}

public struct FirmwareResumeBinding {
    public let pendingSessionId: Int
    public let currentSessionId: Int
    public let pendingDeviceAddress: String
    public let currentDeviceAddress: String
    public let pendingConnectionGeneration: Int
    public let currentConnectionGeneration: Int
    public let pendingImageSha256: String
    public let currentImageSha256: String
    public let acknowledgedOffset: Int
    public let totalBytes: Int

    public init(pendingSessionId: Int, currentSessionId: Int, pendingDeviceAddress: String,
                currentDeviceAddress: String, pendingConnectionGeneration: Int,
                currentConnectionGeneration: Int, pendingImageSha256: String, currentImageSha256: String,
                acknowledgedOffset: Int, totalBytes: Int) {
        self.pendingSessionId = pendingSessionId
        self.currentSessionId = currentSessionId
        self.pendingDeviceAddress = pendingDeviceAddress
        self.currentDeviceAddress = currentDeviceAddress
        self.pendingConnectionGeneration = pendingConnectionGeneration
        self.currentConnectionGeneration = currentConnectionGeneration
        self.pendingImageSha256 = pendingImageSha256
        self.currentImageSha256 = currentImageSha256
        self.acknowledgedOffset = acknowledgedOffset
        self.totalBytes = totalBytes
    }
}

/// Resume is deliberately local to one uninterrupted BLE connection and one immutable image.
public enum FirmwareResumePolicy {
    /// Kotlin twin: `FirmwareResumePolicy.rejectionReason`.
    public static func rejectionReason(_ binding: FirmwareResumeBinding) -> String? {
        if binding.pendingSessionId != binding.currentSessionId {
            return "The paused firmware session is no longer current"
        }
        if binding.pendingDeviceAddress.caseInsensitiveCompare(binding.currentDeviceAddress) != .orderedSame {
            return "The connected strap no longer matches the paused firmware session"
        }
        if binding.pendingConnectionGeneration != binding.currentConnectionGeneration {
            return "The Bluetooth connection changed; restart the firmware transfer from the beginning"
        }
        if binding.pendingImageSha256.caseInsensitiveCompare(binding.currentImageSha256) != .orderedSame {
            return "The selected image changed; restart the firmware transfer from the beginning"
        }
        if !(0...binding.totalBytes).contains(binding.acknowledgedOffset) {
            return "The saved firmware offset is outside the selected image"
        }
        if binding.acknowledgedOffset != binding.totalBytes
            && binding.acknowledgedOffset % FirmwareImageParser.chunkSize != 0 {
            return "The saved firmware offset is not an acknowledged chunk boundary"
        }
        return nil
    }
}

// MARK: - Errors

public struct FirmwareTransferException: Error {
    public let message: String
    public init(_ message: String) { self.message = message }
}

/// An ambiguous transport failure for one data chunk — the only failure the engine retries.
public struct FirmwareRetryableTransportException: Error {
    public let message: String
    public init(_ message: String) { self.message = message }
}

public struct FirmwareTransferPausedException: Error {
    public let acknowledgedOffset: Int
    public let attempts: Int
    public let message: String
    public init(acknowledgedOffset: Int, attempts: Int, message: String) {
        self.acknowledgedOffset = acknowledgedOffset
        self.attempts = attempts
        self.message = message
    }
}

/// Cancellation raised by the transport when the session is torn down (disconnect / cancel).
public struct FirmwareCancellationException: Error {
    public let message: String
    public init(_ message: String) { self.message = message }
}

// The Kotlin exceptions carry their text as `Throwable.message`, which the Android UI shows verbatim.
// `LocalizedError` is the Swift equivalent: without it `localizedDescription` falls back to Foundation's
// generic "The operation couldn't be completed" text, which names neither the command nor the timeout.
extension FirmwareTransferException: LocalizedError {
    public var errorDescription: String? { message }
}

extension FirmwareRetryableTransportException: LocalizedError {
    public var errorDescription: String? { message }
}

extension FirmwareTransferPausedException: LocalizedError {
    public var errorDescription: String? { message }
}

extension FirmwareCancellationException: LocalizedError {
    public var errorDescription: String? { message }
}

// MARK: - Transport + engine

public protocol FirmwareTransferTransport {
    /// Send one framed command and await a correlated final response. `accept` recovers VERIFY's final
    /// result. Throws `FirmwareRetryableTransportException` for an ambiguous chunk failure, any other
    /// error for a permanent one.
    /// Kotlin twin: `FirmwareTransferTransport.exchange`.
    func exchange(command: Int, payload: [UInt8], timeoutMs: Int,
                  accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse
}

/// Transport-independent OTA transaction. Only ambiguous transport failures for a data chunk are
/// retried; a rejected command is permanent, and exhausting the bounded retry budget pauses at the last
/// acknowledged offset. Twin of the Kotlin `FirmwareTransferEngine`.
public final class FirmwareTransferEngine {
    private let transport: FirmwareTransferTransport
    /// Injectable so tests need not sleep; production leaves it at the real 500 ms.
    private let sleep: (Int) async throws -> Void

    public init(transport: FirmwareTransferTransport,
                sleep: @escaping (Int) async throws -> Void = { ms in
                    try await Task.sleep(nanoseconds: UInt64(ms) * 1_000_000)
                }) {
        self.transport = transport
        self.sleep = sleep
    }

    /// Kotlin twin: `FirmwareTransferEngine.transfer`.
    @discardableResult
    public func transfer(
        image: ValidatedFirmwareImage,
        initial: FirmwareUpdateState,
        startOffset: Int = 0,
        prepareSlot: Bool = true,
        publish: (FirmwareUpdateState) -> Void
    ) async throws -> FirmwareUpdateState {
        precondition(startOffset >= 0 && startOffset <= image.bytes.count, "Invalid firmware start offset \(startOffset)")
        precondition(!prepareSlot || startOffset == 0, "A newly prepared slot must start at offset 0")
        precondition(startOffset == image.bytes.count || startOffset % FirmwareImageParser.chunkSize == 0,
                     "Firmware resume offset \(startOffset) is not an acknowledged chunk boundary")
        try await sleep(FirmwareCommand.preTransferDelayMs)
        try await quiesceStrap()
        if prepareSlot {
            try requireAccepted(
                await exchange(FirmwareCommand.prepare, [1], FirmwareCommand.commandTimeoutMs),
                expectedTail: 0, step: "prepare")
        }
        var state = initial
        var offset = startOffset
        while offset < image.bytes.count {
            let count = min(FirmwareImageParser.chunkSize, image.bytes.count - offset)
            var payload = [UInt8](repeating: 0, count: 6 + count)
            payload[0] = 1
            payload[1] = UInt8(offset & 0xff)
            payload[2] = UInt8((offset >> 8) & 0xff)
            payload[3] = UInt8((offset >> 16) & 0xff)
            payload[4] = UInt8((offset >> 24) & 0xff)
            payload[5] = UInt8(count)
            for i in 0..<count { payload[6 + i] = image.bytes[offset + i] }
            var attempts = 0
            while true {
                attempts += 1
                do {
                    try requireAccepted(
                        await exchange(FirmwareCommand.write, payload, FirmwareCommand.commandTimeoutMs),
                        expectedTail: 0, step: "write at offset \(offset)")
                    break
                } catch let retryable as FirmwareRetryableTransportException {
                    if attempts >= FirmwareCommand.maxChunkAttempts {
                        throw FirmwareTransferPausedException(
                            acknowledgedOffset: offset,
                            attempts: attempts,
                            message: "Firmware transfer paused at offset \(offset) after \(attempts) attempts: \(retryable.message)")
                    }
                    state = FirmwareUpdateTransitions.retrying(
                        state, acknowledged: offset, attempt: attempts + 1, maximum: FirmwareCommand.maxChunkAttempts)
                    publish(state)
                }
            }
            offset += count
            state = FirmwareUpdateTransitions.writing(state, acknowledged: offset)
            publish(state)
        }
        state = FirmwareUpdateTransitions.remoteValidating(state)
        publish(state)
        let verified = try await exchange(FirmwareCommand.verify, [1], FirmwareCommand.verifyTimeoutMs)
        if verified.result != 1 || verified.body.first != 1 {
            throw FirmwareTransferException(rejectionMessage("firmware remote image validation failed", verified))
        }
        state = FirmwareUpdateTransitions.ready(state)
        publish(state)
        return state
    }

    /// Kotlin twin: `FirmwareTransferEngine.activate`.
    @discardableResult
    public func activate() async throws -> FirmwareWireResponse {
        let response = try await exchange(FirmwareCommand.activate, [1], FirmwareCommand.commandTimeoutMs)
        if response.result != 1 || response.body.count < 2 || response.body[0] != 1 || response.body[1] != 1 {
            throw FirmwareTransferException(rejectionMessage("firmware activation/reset", response))
        }
        return response
    }

    /// Kotlin twin: `FirmwareTransferEngine.exchange`.
    private func exchange(_ command: Int, _ payload: [UInt8], _ timeoutMs: Int) async throws -> FirmwareWireResponse {
        try await transport.exchange(command: command, payload: payload, timeoutMs: timeoutMs) {
            FirmwareResponseMatcher.isFinal(command: command, $0)
        }
    }

    /// Kotlin twin: `FirmwareTransferEngine.quiesceStrap`.
    private func quiesceStrap() async throws {
        let steps: [(Int, [UInt8], String)] = [
            (FirmwareCommand.stopRealtimeHr, [0], "stop realtime HR"),
            (FirmwareCommand.stopImu, [1, 0], "stop IMU streaming"),
            (FirmwareCommand.abortHistory, [], "abort history transfer"),
        ]
        for (command, payload, step) in steps {
            let response = try await exchange(command, payload, FirmwareCommand.commandTimeoutMs)
            if response.result != 1 {
                throw FirmwareTransferException(rejectionMessage(step, response))
            }
        }
    }

    /// Kotlin twin: `FirmwareTransferEngine.requireAccepted`.
    private func requireAccepted(_ response: FirmwareWireResponse, expectedTail: Int, step: String) throws {
        if response.result != 1 || response.body.count < 2
            || response.body[0] != 1 || Int(response.body[1]) != expectedTail {
            throw FirmwareTransferException(rejectionMessage("firmware \(step)", response))
        }
    }

    /// Kotlin twin: `FirmwareTransferEngine.rejectionMessage`.
    private func rejectionMessage(_ step: String, _ response: FirmwareWireResponse) -> String {
        let detail = response.body.count > 1 ? Int(response.body[1]) : nil
        let detailText: String
        if response.command == FirmwareCommand.prepare, detail == 10 {
            detailText = "prepare state (10)"
        } else if response.command == FirmwareCommand.write, detail == 3 {
            detailText = "invalid slot (3)"
        } else if response.command == FirmwareCommand.write, detail == 4 {
            detailText = "range/overflow (4)"
        } else if response.command == FirmwareCommand.write, detail == 11 {
            detailText = "flash/write state (11)"
        } else if let detail {
            detailText = "detail=\(detail)"
        } else {
            detailText = "detail unavailable"
        }
        return "The strap rejected \(step): result=\(response.result), \(detailText), body=\(response.body.hexLower)"
    }
}
