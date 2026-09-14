import XCTest
import Foundation
@testable import WhoopProtocol

/// Swift twin of the Kotlin `FirmwareUpdateTest` engine/decoder/policy cases
/// (android/.../ble/FirmwareUpdateTest.kt).
final class FirmwareTransferTests: XCTestCase {

    /// A deterministic transport driven by a per-call handler. `sleep` in the engine is stubbed out so
    /// the tests do not wait the real 500 ms.
    private struct FakeTransport: FirmwareTransferTransport {
        let handler: (Int, [UInt8], (FirmwareWireResponse) -> Bool) throws -> FirmwareWireResponse
        func exchange(command: Int, payload: [UInt8], timeoutMs: Int,
                      accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse {
            try handler(command, payload, accept)
        }
    }

    private func engine(_ handler: @escaping (Int, [UInt8], (FirmwareWireResponse) -> Bool) throws -> FirmwareWireResponse) -> FirmwareTransferEngine {
        FirmwareTransferEngine(transport: FakeTransport(handler: handler), sleep: { _ in })
    }

    // MARK: State-machine gating

    func testActivationIsGatedByRemoteVerificationForTheSameState() {
        let info = validInfo()
        let selected = FirmwareUpdateTransitions.selected(info, eligible: true)
        XCTAssertTrue(selected.canStart)
        XCTAssertFalse(selected.canActivate)
        let beginning = FirmwareUpdateTransitions.begin(selected, deviceLabel: "WHOOP 5/MG · 50.42.1.0 · AA:BB")
        XCTAssertTrue((beginning.lockedDeviceLabel ?? "").contains("AA:BB"))
        let writing = FirmwareUpdateTransitions.writing(beginning, acknowledged: 220)
        XCTAssertFalse(writing.canActivate)
        let validating = FirmwareUpdateTransitions.remoteValidating(writing)
        XCTAssertFalse(validating.canActivate)
        let ready = FirmwareUpdateTransitions.ready(validating)
        XCTAssertTrue(ready.canActivate)
        XCTAssertFalse(FirmwareUpdateTransitions.failed(ready, reason: "disconnect").canActivate)
        XCTAssertFalse(FirmwareUpdateTransitions.cancelled(ready).canActivate)
    }

    func testEngineSendsCompleteImageIn220ByteChunksIncludingFinalSlice() async throws {
        let selected = try parsedImage(type: 5)
        var calls: [(Int, [UInt8])] = []
        let e = engine { command, payload, accept in
            calls.append((command, payload))
            let response = command == FirmwareCommand.verify
                ? FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1])
                : FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 0])
            XCTAssertTrue(accept(response))
            return response
        }
        let end = try await e.transfer(image: selected, initial: begun(selected)) { _ in }
        XCTAssertEqual(end.stage, .readyToActivate)
        let writes = calls.filter { $0.0 == FirmwareCommand.write }
        let expectedWrites = (selected.bytes.count + FirmwareImageParser.chunkSize - 1) / FirmwareImageParser.chunkSize
        XCTAssertEqual(writes.count, expectedWrites)
        XCTAssertEqual(Int(writes.first!.1[5]), 220)
        let expectedLastOffset = (expectedWrites - 1) * FirmwareImageParser.chunkSize
        XCTAssertEqual(Int(writes.last!.1[5]), selected.bytes.count - expectedLastOffset)
        XCTAssertEqual(Int(u32(writes.last!.1, 1)), expectedLastOffset)
    }

    func testFreshTransferQuiescesStrapInOfficialOrderBeforeOpeningSlot() async throws {
        let selected = try parsedImage(type: 5)
        var calls: [(Int, [UInt8])] = []
        let e = engine { command, payload, accept in
            calls.append((command, payload))
            let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
        }
        _ = try await e.transfer(image: selected, initial: begun(selected)) { _ in }
        XCTAssertEqual(calls.prefix(4).map { $0.0 },
                       [FirmwareCommand.stopRealtimeHr, FirmwareCommand.stopImu, FirmwareCommand.abortHistory, FirmwareCommand.prepare])
        XCTAssertEqual(calls[0].1, [0])
        XCTAssertEqual(calls[1].1, [1, 0])
        XCTAssertTrue(calls[2].1.isEmpty)
        XCTAssertEqual(calls[3].1, [1])
    }

    func testQuiesceRejectionStopsBeforePrepareAndPreservesRawDetail() async throws {
        let selected = try parsedImage(type: 5)
        var calls: [Int] = []
        let e = engine { command, _, accept in
            calls.append(command)
            let r = command == FirmwareCommand.stopImu
                ? FirmwareWireResponse(command: command, originSequence: 0, result: 0x7f, body: [1, 0x2a])
                : self.accepted(command)
            XCTAssertTrue(accept(r)); return r
        }
        let failure = await captureError { try await e.transfer(image: selected, initial: begun(selected)) { _ in } }
        let message = (failure as? FirmwareTransferException)?.message ?? ""
        XCTAssertTrue(message.contains("result=127"), message)
        XCTAssertTrue(message.contains("body=012a"), message)
        XCTAssertFalse(calls.contains(FirmwareCommand.prepare))
    }

    func testLostChunkAcknowledgementRetriesIdenticalOffsetAndCountsProgressOnce() async throws {
        let selected = try parsedImage(type: 5)
        var writePayloads: [[UInt8]] = []
        var progress: [Int] = []
        var firstWrite = true
        let e = engine { command, payload, accept in
            if command == FirmwareCommand.write {
                writePayloads.append(payload)
                if firstWrite {
                    firstWrite = false
                    throw FirmwareRetryableTransportException("response timeout")
                }
            }
            let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
        }
        let end = try await e.transfer(image: selected, initial: begun(selected)) { progress.append($0.bytesAcknowledged) }
        XCTAssertEqual(end.stage, .readyToActivate)
        XCTAssertEqual(writePayloads.filter { u32($0, 1) == 0 }.count, 2)
        XCTAssertEqual(writePayloads[0], writePayloads[1])
        XCTAssertEqual(progress.filter { $0 == 220 }.count, 1)
    }

    func testRetryExhaustionPausesAtLastAcknowledgedOffsetAfterSevenAttempts() async throws {
        let selected = try parsedImage(type: 5)
        var writes = 0
        let e = engine { command, _, accept in
            if command == FirmwareCommand.write {
                writes += 1
                throw FirmwareRetryableTransportException("response timeout")
            }
            let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
        }
        let failure = await captureError { try await e.transfer(image: selected, initial: begun(selected)) { _ in } }
        let paused = try XCTUnwrap(failure as? FirmwareTransferPausedException)
        XCTAssertEqual(paused.acknowledgedOffset, 0)
        XCTAssertEqual(paused.attempts, FirmwareCommand.maxChunkAttempts)
        XCTAssertEqual(writes, 7)
    }

    func testResumeOnSameSlotSkipsPrepareKeepsProgressAndNeverActivates() async throws {
        let selected = try parsedImage(type: 5)
        var calls: [(Int, [UInt8])] = []
        var progress: [Int] = []
        let e = engine { command, payload, accept in
            calls.append((command, payload))
            let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
        }
        let paused = FirmwareUpdateTransitions.paused(
            FirmwareUpdateTransitions.writing(begun(selected), acknowledged: 440),
            acknowledged: 440, reason: "response timeout")
        let end = try await e.transfer(image: selected, initial: FirmwareUpdateTransitions.resuming(paused),
                                       startOffset: 440, prepareSlot: false) { progress.append($0.bytesAcknowledged) }
        XCTAssertEqual(end.stage, .readyToActivate)
        XCTAssertFalse(calls.contains { $0.0 == FirmwareCommand.prepare })
        XCTAssertFalse(calls.contains { $0.0 == FirmwareCommand.activate })
        XCTAssertEqual(Int(u32(calls.first { $0.0 == FirmwareCommand.write }!.1, 1)), 440)
        XCTAssertTrue(progress.allSatisfy { $0 >= 440 })
    }

    func testExplicitWriteRejectionIsNotRetriedAndReportsKnownDetail() async throws {
        let selected = try parsedImage(type: 5)
        var writes = 0
        let e = engine { command, _, accept in
            let r: FirmwareWireResponse
            if command == FirmwareCommand.write {
                writes += 1
                r = FirmwareWireResponse(command: command, originSequence: 0, result: 0, body: [1, 11])
            } else {
                r = self.accepted(command)
            }
            XCTAssertTrue(accept(r)); return r
        }
        let failure = await captureError { try await e.transfer(image: selected, initial: begun(selected)) { _ in } }
        XCTAssertEqual(writes, 1)
        let message = (failure as? FirmwareTransferException)?.message ?? ""
        XCTAssertTrue(message.contains("flash/write state (11)"), message)
        XCTAssertTrue(message.contains("result=0"), message)
    }

    func testResumeBindingRejectsStaleSessionDeviceConnectionImageAndInvalidOffset() {
        let valid = FirmwareResumeBinding(
            pendingSessionId: 9, currentSessionId: 9,
            pendingDeviceAddress: "AA:BB", currentDeviceAddress: "aa:bb",
            pendingConnectionGeneration: 12, currentConnectionGeneration: 12,
            pendingImageSha256: "abcd", currentImageSha256: "abcd",
            acknowledgedOffset: 440, totalBytes: 952)
        XCTAssertNil(FirmwareResumePolicy.rejectionReason(valid))
        XCTAssertTrue(rejection(valid, { $0.currentSessionId = 10 }).contains("session"))
        XCTAssertTrue(rejection(valid, { $0.currentDeviceAddress = "CC:DD" }).contains("strap"))
        XCTAssertTrue(rejection(valid, { $0.currentConnectionGeneration = 13 }).contains("connection"))
        XCTAssertTrue(rejection(valid, { $0.currentImageSha256 = "ef01" }).contains("image"))
        XCTAssertTrue(rejection(valid, { $0.acknowledgedOffset = 953 }).contains("offset"))
        XCTAssertTrue(rejection(valid, { $0.acknowledgedOffset = 221 }).contains("boundary"))
    }

    func testVerifyIgnoresOptionalPendingResponseAndPropagatesFinalRemoteFailure() async throws {
        let selected = try parsedImage(type: 5)
        let e = engine { command, _, accept in
            let candidates = command == FirmwareCommand.verify
                ? [FirmwareWireResponse(command: command, originSequence: 0, result: 2, body: [1]),
                   FirmwareWireResponse(command: command, originSequence: 0, result: 0, body: [1])]
                : [FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 0])]
            return candidates.first(where: accept)!
        }
        let failure = await captureError { try await e.transfer(image: selected, initial: begun(selected)) { _ in } }
        let message = (failure as? FirmwareTransferException)?.message ?? ""
        XCTAssertTrue(message.contains("remote image validation failed"), message)
    }

    func testCorrelationRejectsStaleSessionCommandAndSequence() {
        let base = FirmwareResponseKey(pendingSessionId: 9, currentSessionId: 9, expectedCommand: 143,
                                       expectedSequence: 0, actualCommand: 143, actualSequence: 0, sameDevice: true)
        XCTAssertTrue(FirmwareResponseMatcher.correlated(base))
        XCTAssertFalse(FirmwareResponseMatcher.correlated(withKey(base) { $0.pendingSessionId = 8 }))
        XCTAssertFalse(FirmwareResponseMatcher.correlated(withKey(base) { $0.actualSequence = 255 }))
        XCTAssertFalse(FirmwareResponseMatcher.correlated(withKey(base) { $0.actualCommand = 142 }))
        XCTAssertFalse(FirmwareResponseMatcher.correlated(withKey(base) { $0.sameDevice = false }))
    }

    func testWhoop5DecoderAcceptsCrcValidResponsesAndRemovesPadding() {
        let cases: [(Int, [UInt8])] = [
            (FirmwareCommand.stopRealtimeHr, []),
            (FirmwareCommand.abortHistory, []),
            (FirmwareCommand.stopImu, [1, 0]),
            (FirmwareCommand.prepare, [1, 0]),
            (FirmwareCommand.write, [1, 0]),
            (FirmwareCommand.verify, [1]),
            (FirmwareCommand.activate, [1, 1]),
        ]
        for (command, body) in cases {
            let decoded = FirmwareWhoop5ResponseDecoder.decode(
                whoop5ResponseFrame(command: command, originSequence: 0xa5, result: 1, body: body))
            XCTAssertNotNil(decoded, "command \(command) should decode")
            XCTAssertEqual(decoded?.command, command)
            XCTAssertEqual(decoded?.originSequence, 0xa5)
            XCTAssertEqual(decoded?.result, 1)
            XCTAssertEqual(decoded?.body, body)
        }
        let puffinAlias = FirmwareWhoop5ResponseDecoder.decode(
            whoop5ResponseFrame(command: 144, originSequence: 0x33, result: 1, body: [1, 1], type: 0x26))
        XCTAssertEqual(puffinAlias?.command, FirmwareCommand.activate)

        let pending = FirmwareWhoop5ResponseDecoder.decode(
            whoop5ResponseFrame(command: 83, originSequence: 0x44, result: 2, body: [1]))!
        XCTAssertFalse(FirmwareResponseMatcher.isFinal(command: FirmwareCommand.verify, pending))
    }

    func testWhoop5DecoderRejectsMalformedFrames() {
        let valid = whoop5ResponseFrame(command: 142, originSequence: 7, result: 1, body: [1, 0])
        XCTAssertNil(FirmwareWhoop5ResponseDecoder.decode(Array(valid.dropLast())))
        XCTAssertNil(FirmwareWhoop5ResponseDecoder.decode(valid + [0]))
        var bumped = valid; bumped[2] = bumped[2] &+ 1
        XCTAssertNil(FirmwareWhoop5ResponseDecoder.decode(bumped))
        var badResult = valid; badResult[13] = 2
        XCTAssertNil(FirmwareWhoop5ResponseDecoder.decode(badResult))
        XCTAssertNil(FirmwareWhoop5ResponseDecoder.decode(
            whoop5ResponseFrame(command: 142, originSequence: 7, result: 1, body: [1, 0], type: 0x23)))
    }

    func testExclusiveSessionAcquisitionRefusesQueuedOrInFlightUnrelatedWrites() {
        XCTAssertNil(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: false, queuedWrites: 0))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: false, queuedWrites: 1)!.contains("Bluetooth is busy"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: true, retryPending: false, queuedWrites: 0)!.contains("Bluetooth is busy"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: true, queuedWrites: 0)!.contains("Bluetooth is busy"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: true, writeInFlight: false, retryPending: false, queuedWrites: 0)!.contains("Bluetooth is busy"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: false, queuedWrites: 0, negotiatedMtu: 23)!.contains("MTU"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: false, queuedWrites: 0, negotiatedMtu: 247, cccdInFlight: true)!.contains("notification"))
        XCTAssertTrue(FirmwareUpdateAdmission.busyReason(backfilling: false, writeInFlight: false, retryPending: false, queuedWrites: 0, negotiatedMtu: 247, queuedCccds: 1)!.contains("notification"))
    }

    func testFirmwareQueueDropsStaleSessionWritesAndNeverRetriesAmbiguousRejection() {
        XCTAssertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: nil, currentSessionId: nil))
        XCTAssertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: nil, currentSessionId: 12))
        XCTAssertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: 12, currentSessionId: 12))
        XCTAssertFalse(FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: 11, currentSessionId: 12))
        XCTAssertFalse(FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: 12, currentSessionId: nil))
        XCTAssertTrue(FirmwareWriteQueuePolicy.mayRetryAfterAmbiguousRejection(firmwareSessionId: nil))
        XCTAssertFalse(FirmwareWriteQueuePolicy.mayRetryAfterAmbiguousRejection(firmwareSessionId: 12))
    }

    func testActivationObservationIsBoundedAndStaleTimerCannotTerminateNewSession() {
        XCTAssertEqual(FirmwareActivationObservation.disconnectTimeoutMs, 30_000)
        XCTAssertEqual(FirmwareActivationObservation.reconnectTimeoutMs, 60_000)
        XCTAssertTrue(FirmwareActivationObservation.sessionIsCurrent(observedSessionId: 12, currentSessionId: 12))
        XCTAssertFalse(FirmwareActivationObservation.sessionIsCurrent(observedSessionId: 12, currentSessionId: 13))
        XCTAssertFalse(FirmwareActivationObservation.sessionIsCurrent(observedSessionId: 12, currentSessionId: nil))
        XCTAssertFalse(FirmwareActivationObservation.canAcceptReportedVersion(.activationRequested))
        XCTAssertTrue(FirmwareActivationObservation.canAcceptReportedVersion(.reconnecting))
    }

    func testTransportCancellationStopsPlanBeforeVerifyAndCannotUnlockActivation() async throws {
        let selected = try parsedImage(type: 5)
        var calls = 0
        var last = begun(selected)
        let e = engine { command, _, accept in
            calls += 1
            if command == FirmwareCommand.write { throw FirmwareCancellationException("disconnect") }
            let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
        }
        let failure = await captureError { try await e.transfer(image: selected, initial: last) { last = $0 } }
        XCTAssertTrue(failure is FirmwareCancellationException)
        XCTAssertFalse(last.canActivate)
        XCTAssertEqual(calls - 4, 1)
    }

    func testActivationAcceptsOnlyExplicitAcceptedResetResponse() async throws {
        let rejected = engine { command, _, _ in
            FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 0])
        }
        let failure = await captureError { _ = try await rejected.activate() }
        XCTAssertTrue(failure is FirmwareTransferException)

        let acceptedEngine = engine { command, _, _ in
            FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 1])
        }
        let response = try await acceptedEngine.activate()
        XCTAssertEqual(response.result, 1)
    }

    func testDisconnectAfterActivationRemainsUnknownUntilSameDeviceReportsAgain() {
        let ready = FirmwareUpdateTransitions.ready(
            FirmwareUpdateTransitions.remoteValidating(
                FirmwareUpdateTransitions.begin(FirmwareUpdateTransitions.selected(validInfo(), eligible: true), deviceLabel: "WHOOP 5/MG")))
        let sent = FirmwareUpdateTransitions.activationRequested(ready)
        let reconnecting = FirmwareUpdateTransitions.reconnecting(sent)
        XCTAssertEqual(reconnecting.stage, .reconnecting)
        XCTAssertTrue(reconnecting.status.contains("Waiting for it to reconnect"))
        let reconnected = FirmwareUpdateTransitions.reconnected(reconnecting, reportedVersion: "50.42.1.0")
        XCTAssertEqual(reconnected.stage, .deviceReconnected)
        XCTAssertTrue(reconnected.status.contains("reconnected"))
    }

    /// The iOS reconnect deadline: the same strap is back but no version decoded. The status must say it
    /// reconnected and name no version, never "did not reconnect".
    func testReconnectWithoutDecodedVersionReportsNoVersion() {
        let reconnecting = FirmwareUpdateTransitions.reconnecting(FirmwareUpdateTransitions.activationRequested(
            FirmwareUpdateTransitions.ready(FirmwareUpdateTransitions.begin(
                FirmwareUpdateTransitions.selected(validInfo(), eligible: true), deviceLabel: "WHOOP 5/MG"))))
        let reconnected = FirmwareUpdateTransitions.reconnected(reconnecting, reportedVersion: nil)
        XCTAssertEqual(reconnected.stage, .deviceReconnected)
        XCTAssertEqual(reconnected.status, "The strap reconnected.")
        XCTAssertNil(reconnected.error)
        XCTAssertFalse(reconnected.canCancel)
    }

    /// Timeouts outside the chunk loop (quiesce 3/106/20, prepare 142, verify 83, activate 144) are not
    /// retried and reach the UI as the thrown error. Its text must be the transport's own message, as
    /// Android shows `t.message`, not Foundation's generic "The operation couldn't be completed".
    func testFailuresOutsideTheChunkLoopCarryTheirOwnReadableText() async throws {
        let selected = try parsedImage(type: 5)
        for silent in [FirmwareCommand.stopRealtimeHr, FirmwareCommand.stopImu, FirmwareCommand.abortHistory,
                       FirmwareCommand.prepare, FirmwareCommand.verify] {
            let text = "No response to firmware command \(silent) within 8000ms"
            let e = engine { command, _, accept in
                if command == silent { throw FirmwareRetryableTransportException(text) }
                let r = self.accepted(command); XCTAssertTrue(accept(r)); return r
            }
            let failure = await captureError { try await e.transfer(image: selected, initial: self.begun(selected)) { _ in } }
            XCTAssertTrue(failure is FirmwareRetryableTransportException, "command \(silent)")
            XCTAssertEqual((failure as? LocalizedError)?.errorDescription, text)
            XCTAssertEqual(failure?.localizedDescription, text)
        }
        let activation = engine { command, _, _ in
            throw FirmwareRetryableTransportException("No response to firmware command \(command) within 8000ms")
        }
        let activationFailure = await captureError { _ = try await activation.activate() }
        XCTAssertEqual(activationFailure?.localizedDescription, "No response to firmware command 144 within 8000ms")

        XCTAssertEqual(FirmwareCancellationException("Firmware update cancelled").localizedDescription, "Firmware update cancelled")
        XCTAssertEqual(FirmwareTransferException("The connected strap changed or is no longer ready").localizedDescription,
                       "The connected strap changed or is no longer ready")
        XCTAssertEqual(FirmwareTransferPausedException(acknowledgedOffset: 220, attempts: 7, message: "paused at 220").localizedDescription,
                       "paused at 220")
    }

    func testTransferEntryPointRejectsPrepareWithOffsetAndResumeBetweenBoundaries() async throws {
        // The engine uses precondition() for these, which traps rather than throws; the Kotlin twin uses
        // require(). The BLE layer never forms these arguments (they are guarded by FirmwareResumePolicy),
        // so this test documents the invariant against the pure policy instead.
        let selected = try parsedImage(type: 5)
        XCTAssertNotNil(FirmwareResumePolicy.rejectionReason(FirmwareResumeBinding(
            pendingSessionId: 1, currentSessionId: 1, pendingDeviceAddress: "a", currentDeviceAddress: "a",
            pendingConnectionGeneration: 0, currentConnectionGeneration: 0,
            pendingImageSha256: "x", currentImageSha256: "x",
            acknowledgedOffset: 221, totalBytes: selected.bytes.count)))
    }

    // MARK: - Helpers

    private func captureError(_ body: () async throws -> Void) async -> Error? {
        do { try await body(); return nil } catch { return error }
    }

    private func validInfo() -> FirmwareImageInfo {
        FirmwareImageInfo(fileName: "original.zbin", byteCount: 1024, format: .zbinCompressed, version: "50.42.1.0",
                          payloadLength: 512, payloadCrc32: "00000000", headerCrc32: "00000000", sha256: "00",
                          compatibilityNote: "test")
    }

    private func parsedImage(type: Int) throws -> ValidatedFirmwareImage {
        let ext = type == 5 ? "zbin" : "bin"
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: "fixture.\(ext)", input: buildImage(type: type)) else {
            throw XCTSkip("fixture did not parse")
        }
        return image
    }

    private func begun(_ image: ValidatedFirmwareImage) -> FirmwareUpdateState {
        FirmwareUpdateTransitions.begin(FirmwareUpdateTransitions.selected(image.info, eligible: true), deviceLabel: "WHOOP 5/MG")
    }

    private func accepted(_ command: Int) -> FirmwareWireResponse {
        switch command {
        case FirmwareCommand.verify: return FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1])
        case FirmwareCommand.activate: return FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 1])
        default: return FirmwareWireResponse(command: command, originSequence: 0, result: 1, body: [1, 0])
        }
    }

    private func rejection(_ base: FirmwareResumeBinding, _ mutate: (inout MutableBinding) -> Void) -> String {
        var m = MutableBinding(base)
        mutate(&m)
        return FirmwareResumePolicy.rejectionReason(m.build()) ?? ""
    }

    private func withKey(_ base: FirmwareResponseKey, _ mutate: (inout MutableKey) -> Void) -> FirmwareResponseKey {
        var m = MutableKey(base)
        mutate(&m)
        return m.build()
    }

    /// A type-5 gzip-of-type-1 image (or a raw type-1) built identically to the Kotlin test's image().
    private func buildImage(type: Int) -> [UInt8] {
        if type == 5 {
            let raw = buildImage(type: 1)
            let compressed = gzipStored(raw)
            let padded = compressed + [UInt8](repeating: 0, count: (4 - compressed.count % 4) % 4)
            return container(payload: padded, type: 5)
        }
        var payload = [UInt8](repeating: 0, count: 440)
        for i in 0..<440 { payload[i] = UInt8((i * 17) & 0xff) }
        return container(payload: payload, type: type)
    }

    private func container(payload: [UInt8], type: Int) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: FirmwareImageParser.headerSize + payload.count)
        for i in 0..<payload.count { bytes[FirmwareImageParser.headerSize + i] = payload[i] }
        put(&bytes, 4, payload.count)
        put(&bytes, 8, 5)
        put(&bytes, 12, type)
        put(&bytes, 0x7c, 50); put(&bytes, 0x80, 42); put(&bytes, 0x84, 1); put(&bytes, 0x88, 0)
        put(&bytes, 0, Int(crc32(bytes, FirmwareImageParser.headerSize, bytes.count)))
        put(&bytes, 504, Int(crc32(bytes, 8, 504)))
        put(&bytes, 508, Int(u32(bytes, 0)))
        return bytes
    }

    private func gzipStored(_ bytes: [UInt8]) -> [UInt8] {
        var out: [UInt8] = [0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff]
        var offset = 0
        if bytes.isEmpty { out += [1, 0, 0, 0xff, 0xff] }
        while offset < bytes.count {
            let chunk = min(0xffff, bytes.count - offset)
            let final = offset + chunk >= bytes.count
            out.append(final ? 1 : 0)
            out.append(UInt8(chunk & 0xff)); out.append(UInt8((chunk >> 8) & 0xff))
            let inv = chunk ^ 0xffff
            out.append(UInt8(inv & 0xff)); out.append(UInt8((inv >> 8) & 0xff))
            out += bytes[offset..<(offset + chunk)]
            offset += chunk
        }
        let c = crc32(bytes)
        out += [UInt8(c & 0xff), UInt8((c >> 8) & 0xff), UInt8((c >> 16) & 0xff), UInt8((c >> 24) & 0xff)]
        let n = UInt32(bytes.count)
        out += [UInt8(n & 0xff), UInt8((n >> 8) & 0xff), UInt8((n >> 16) & 0xff), UInt8((n >> 24) & 0xff)]
        return out
    }

    private func whoop5ResponseFrame(command: Int, originSequence: Int, result: Int, body: [UInt8], type: Int = 0x24) -> [UInt8] {
        var inner: [UInt8] = [UInt8(type), 0x51, UInt8(command), UInt8(originSequence), UInt8(result)] + body
        inner += [UInt8](repeating: 0, count: (4 - inner.count % 4) % 4)
        let declaredLength = inner.count + 4
        var frame = [UInt8](repeating: 0, count: declaredLength + 8)
        frame[0] = 0xaa; frame[1] = 1
        frame[2] = UInt8(declaredLength & 0xff); frame[3] = UInt8((declaredLength >> 8) & 0xff)
        frame[4] = 0; frame[5] = 1
        let headerCrc = crc16Modbus(frame, 0, 6)
        frame[6] = UInt8(headerCrc & 0xff); frame[7] = UInt8((headerCrc >> 8) & 0xff)
        for i in 0..<inner.count { frame[8 + i] = inner[i] }
        let c = crc32(frame, 8, frame.count - 4)
        let tail = frame.count - 4
        frame[tail] = UInt8(c & 0xff); frame[tail + 1] = UInt8((c >> 8) & 0xff)
        frame[tail + 2] = UInt8((c >> 16) & 0xff); frame[tail + 3] = UInt8((c >> 24) & 0xff)
        return frame
    }

    private func put(_ bytes: inout [UInt8], _ offset: Int, _ value: Int) {
        bytes[offset] = UInt8(value & 0xff)
        bytes[offset + 1] = UInt8((value >> 8) & 0xff)
        bytes[offset + 2] = UInt8((value >> 16) & 0xff)
        bytes[offset + 3] = UInt8((value >> 24) & 0xff)
    }

    private func u32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        UInt32(bytes[offset]) | (UInt32(bytes[offset + 1]) << 8)
            | (UInt32(bytes[offset + 2]) << 16) | (UInt32(bytes[offset + 3]) << 24)
    }

    /// Small mutable mirrors so a test can copy-and-tweak the immutable structs (Kotlin `copy(...)`).
    private struct MutableBinding {
        var pendingSessionId, currentSessionId: Int
        var pendingDeviceAddress, currentDeviceAddress: String
        var pendingConnectionGeneration, currentConnectionGeneration: Int
        var pendingImageSha256, currentImageSha256: String
        var acknowledgedOffset, totalBytes: Int
        init(_ b: FirmwareResumeBinding) {
            pendingSessionId = b.pendingSessionId; currentSessionId = b.currentSessionId
            pendingDeviceAddress = b.pendingDeviceAddress; currentDeviceAddress = b.currentDeviceAddress
            pendingConnectionGeneration = b.pendingConnectionGeneration; currentConnectionGeneration = b.currentConnectionGeneration
            pendingImageSha256 = b.pendingImageSha256; currentImageSha256 = b.currentImageSha256
            acknowledgedOffset = b.acknowledgedOffset; totalBytes = b.totalBytes
        }
        func build() -> FirmwareResumeBinding {
            FirmwareResumeBinding(pendingSessionId: pendingSessionId, currentSessionId: currentSessionId,
                                  pendingDeviceAddress: pendingDeviceAddress, currentDeviceAddress: currentDeviceAddress,
                                  pendingConnectionGeneration: pendingConnectionGeneration, currentConnectionGeneration: currentConnectionGeneration,
                                  pendingImageSha256: pendingImageSha256, currentImageSha256: currentImageSha256,
                                  acknowledgedOffset: acknowledgedOffset, totalBytes: totalBytes)
        }
    }

    private struct MutableKey {
        var pendingSessionId, currentSessionId, expectedCommand, expectedSequence, actualCommand, actualSequence: Int
        var sameDevice: Bool
        init(_ k: FirmwareResponseKey) {
            pendingSessionId = k.pendingSessionId; currentSessionId = k.currentSessionId
            expectedCommand = k.expectedCommand; expectedSequence = k.expectedSequence
            actualCommand = k.actualCommand; actualSequence = k.actualSequence; sameDevice = k.sameDevice
        }
        func build() -> FirmwareResponseKey {
            FirmwareResponseKey(pendingSessionId: pendingSessionId, currentSessionId: currentSessionId,
                                expectedCommand: expectedCommand, expectedSequence: expectedSequence,
                                actualCommand: actualCommand, actualSequence: actualSequence, sameDevice: sameDevice)
        }
    }
}
