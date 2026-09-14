import Foundation
import CoreBluetooth
import WhoopProtocol

// The WHOOP 5/MG firmware-update transaction, driven from the Test Centre firmware card. This is the
// iOS/macOS twin of the firmware section of `WhoopBleClient.kt` (com.noop.ble). The pure logic — image
// validation, the transfer state machine, response decoding, resume/admission policy — lives in
// WhoopProtocol (`FirmwareImageParser`, `FirmwareTransferEngine`, …); this file is only the CoreBluetooth
// glue: it frames each firmware command through `puffinCommandFrame`, writes it to the puffin command
// characteristic, and correlates the strap's reply arriving on the notify channel.
//
// Nothing here sends a command the app does not already send elsewhere, nothing is sent automatically,
// and activation is a separate, explicitly confirmed step. Firmware writes go out WITHOUT response so
// they never re-enter the WHOOP4-style bond/handshake path in `didWriteValueFor`; delivery is judged
// solely by the strap's correlated COMMAND_RESPONSE, and an unanswered chunk is the retryable-timeout
// case, exactly as on Android.
extension BLEManager {

    /// One firmware update session, bound to the exact connection and image it started on.
    final class FirmwareSession {
        let id: Int
        let deviceAddress: String
        let connectionGeneration: Int
        let image: ValidatedFirmwareImage
        var acknowledgedOffset: Int = 0
        init(id: Int, deviceAddress: String, connectionGeneration: Int, image: ValidatedFirmwareImage) {
            self.id = id
            self.deviceAddress = deviceAddress
            self.connectionGeneration = connectionGeneration
            self.image = image
        }
    }

    /// The one firmware command awaiting its correlated response.
    final class PendingFirmwareResponse {
        let sessionId: Int
        let command: Int
        let sequence: Int
        let accept: (FirmwareWireResponse) -> Bool
        var settled = false
        init(sessionId: Int, command: Int, sequence: Int, accept: @escaping (FirmwareWireResponse) -> Bool) {
            self.sessionId = sessionId
            self.command = command
            self.sequence = sequence
            self.accept = accept
        }
    }

    /// The transport the engine drives; hops each call onto the manager's main actor.
    private struct Transport: FirmwareTransferTransport {
        unowned let manager: BLEManager
        func exchange(command: Int, payload: [UInt8], timeoutMs: Int,
                      accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse {
            try await manager.firmwareExchange(command: command, payload: payload, timeoutMs: timeoutMs, accept: accept)
        }
    }

    // MARK: - Public API (Test Centre)

    /// Select and validate a local container. This method never touches Bluetooth. Twin of the Android
    /// `selectFirmwareImage`.
    public func selectFirmwareImage(fileName: String, bytes: [UInt8]) {
        if firmwareUpdateExclusive || firmwareTransferTask != nil
            || firmwareUpdateState.stage == .paused {
            var next = firmwareUpdateState
            next.error = "Cancel the current update session before choosing another image"
            setFirmwareUpdateState(next)
            return
        }
        // Invalidate first: a malformed replacement may never leave an older valid image armed.
        selectedFirmwareImage = nil
        firmwareSession = nil
        firmwarePending = nil
        setFirmwareUpdateState(FirmwareUpdateState(status: "Validating selected image"))
        switch FirmwareImageParser.parse(fileName: fileName, input: bytes) {
        case .invalid(let reason):
            setFirmwareUpdateState(FirmwareUpdateTransitions.failed(FirmwareUpdateState(), reason: reason))
        case .valid(let image):
            selectedFirmwareImage = image
            setFirmwareUpdateState(FirmwareUpdateTransitions.selected(image.info, eligible: firmwareDeviceEligible()))
        }
    }

    /// Drop the selected image and reset the card. Cancels an active session first. Twin of `clearFirmwareImage`.
    public func clearFirmwareImage() {
        if firmwareUpdateState.canCancel {
            cancelFirmwareUpdate()
            return
        }
        if firmwareUpdateState.stage == .activationRequested || firmwareUpdateState.stage == .reconnecting {
            return
        }
        selectedFirmwareImage = nil
        firmwareSession = nil
        firmwarePending = nil
        firmwareUpdateExclusive = false
        setFirmwareUpdateState(FirmwareUpdateState())
    }

    /// Begin a fresh prepare/write/verify transaction at offset zero. Twin of `startFirmwareTransfer`.
    public func startFirmwareTransfer() {
        guard let image = selectedFirmwareImage, firmwareUpdateState.stage == .imageReady else {
            setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: "Choose and validate an image before starting"))
            return
        }
        guard firmwareDeviceEligible() else {
            refreshFirmwareUpdateEligibility()
            return
        }
        if let busy = FirmwareUpdateAdmission.busyReason(
            backfilling: state.backfilling,
            writeInFlight: false,
            retryPending: false,
            queuedWrites: 0,
            negotiatedMtu: negotiatedFirmwareMtu(),
            requiredMtu: 247) {
            setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: busy))
            return
        }
        guard let address = firmwarePeripheral?.identifier.uuidString else {
            setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: "Connected device identity is unavailable"))
            return
        }
        firmwareUpdateExclusive = true
        firmwareSessionCounter += 1
        let session = FirmwareSession(id: firmwareSessionCounter, deviceAddress: address,
                                      connectionGeneration: firmwareConnectionGeneration, image: image)
        firmwareSession = session
        let fw = state.strapFirmware?.isEmpty == false ? state.strapFirmware! : "unknown firmware"
        setFirmwareUpdateState(FirmwareUpdateTransitions.begin(firmwareUpdateState, deviceLabel: "WHOOP 5/MG · \(fw) · \(address)"))
        runFirmwareTransfer(session: session)
    }

    /// Cancel future update commands. A command already handed to the OS stack cannot be recalled. Twin
    /// of `cancelFirmwareUpdate`.
    public func cancelFirmwareUpdate() {
        guard firmwareUpdateState.canCancel else { return }
        firmwareTransferTask?.cancel()
        firmwareTransferTask = nil
        resumeFirmwarePending(throwing: FirmwareCancellationException("Firmware update cancelled"))
        firmwareSession = nil
        firmwareUpdateExclusive = false
        setFirmwareUpdateState(FirmwareUpdateTransitions.cancelled(firmwareUpdateState))
    }

    /// Resume only the paused slot on this exact, uninterrupted connection. Twin of `resumeFirmwareTransfer`.
    public func resumeFirmwareTransfer() {
        guard let session = firmwareSession, let currentImage = selectedFirmwareImage,
              firmwareUpdateState.stage == .paused else { return }
        let address = firmwarePeripheral?.identifier.uuidString ?? ""
        let binding = FirmwareResumeBinding(
            pendingSessionId: session.id,
            currentSessionId: firmwareSession?.id ?? -1,
            pendingDeviceAddress: session.deviceAddress,
            currentDeviceAddress: address,
            pendingConnectionGeneration: session.connectionGeneration,
            currentConnectionGeneration: firmwareConnectionGeneration,
            pendingImageSha256: session.image.info.sha256,
            currentImageSha256: currentImage.info.sha256,
            acknowledgedOffset: session.acknowledgedOffset,
            totalBytes: session.image.bytes.count)
        let rejection = FirmwareResumePolicy.rejectionReason(binding)
            ?? (firmwareDeviceEligible() ? nil : "The strap is no longer ready on the paused connection")
        if let rejection {
            var next = firmwareUpdateState
            next.error = rejection
            setFirmwareUpdateState(next)
            return
        }
        if let busy = FirmwareUpdateAdmission.busyReason(
            backfilling: state.backfilling, writeInFlight: false, retryPending: false, queuedWrites: 0,
            negotiatedMtu: negotiatedFirmwareMtu(), requiredMtu: 247) {
            var next = firmwareUpdateState
            next.error = busy
            setFirmwareUpdateState(next)
            return
        }
        firmwareUpdateExclusive = true
        setFirmwareUpdateState(FirmwareUpdateTransitions.resuming(firmwareUpdateState))
        runFirmwareTransfer(session: session, startOffset: session.acknowledgedOffset, prepareSlot: false)
    }

    /// Explicit second phase. The UI confirmation is required before this entry point is called. Twin of
    /// `activateVerifiedFirmware`.
    public func activateVerifiedFirmware() {
        guard let session = firmwareSession, firmwareUpdateState.stage == .readyToActivate else { return }
        guard firmwareDeviceEligible(), session.deviceAddress == firmwarePeripheral?.identifier.uuidString else {
            finishFirmwareFailure(session, reason: "The verified session no longer belongs to the connected strap")
            return
        }
        var next = firmwareUpdateState
        next.stage = .activationRequested
        next.status = "Sending the explicit activation/reset request"
        next.error = nil
        setFirmwareUpdateState(next)
        let engine = firmwareEngine()
        firmwareTransferTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                _ = try await engine.activate()
                guard self.firmwareSession?.id == session.id else { return }
                // Reconnect handshakes must run, so the command exclusion ends after the activation ACK.
                self.firmwareUpdateExclusive = false
                self.setFirmwareUpdateState(FirmwareUpdateTransitions.activationRequested(self.firmwareUpdateState))
                self.firmwareTransferTask = Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: UInt64(FirmwareActivationObservation.disconnectTimeoutMs) * 1_000_000)
                    guard let self, !Task.isCancelled else { return }
                    self.finishFirmwareFailure(session, reason: "Activation was accepted, but the strap did not disconnect within 30 seconds; boot outcome is unknown")
                }
            } catch is FirmwareCancellationException {
                // A disconnect after sending activation is represented by RECONNECTING, never as a retry.
            } catch {
                self.finishFirmwareFailure(session, reason: (error as? FirmwareTransferException)?.message
                    ?? error.localizedDescription)
            }
        }
    }

    // MARK: - Transfer driver

    private func runFirmwareTransfer(session: FirmwareSession, startOffset: Int = 0, prepareSlot: Bool = true) {
        let engine = firmwareEngine()
        let image = session.image
        firmwareTransferTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                _ = try await engine.transfer(image: image, initial: self.firmwareUpdateState,
                                              startOffset: startOffset, prepareSlot: prepareSlot) { next in
                    // The engine's async body runs off the main actor, so hop back before touching
                    // published state or the session. Tasks submitted to the main actor keep their order,
                    // so the progress readout advances monotonically.
                    Task { @MainActor [weak self] in
                        guard let self, self.firmwareSession?.id == session.id else { return }
                        session.acknowledgedOffset = next.bytesAcknowledged
                        self.setFirmwareUpdateState(next)
                    }
                }
            } catch is FirmwareCancellationException {
                // cancelFirmwareUpdate or the disconnect path already published the terminal state.
            } catch let paused as FirmwareTransferPausedException {
                self.pauseFirmwareTransfer(session, paused)
            } catch let failure as FirmwareTransferException {
                self.finishFirmwareFailure(session, reason: failure.message)
            } catch {
                self.finishFirmwareFailure(session, reason: error.localizedDescription)
            }
        }
    }

    private func firmwareEngine() -> FirmwareTransferEngine {
        FirmwareTransferEngine(transport: Transport(manager: self))
    }

    // MARK: - Transport: write one command, await its correlated reply

    func firmwareExchange(command: Int, payload: [UInt8], timeoutMs: Int,
                          accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse {
        guard let session = firmwareSession, firmwareUpdateExclusive else {
            throw FirmwareCancellationException("Firmware session is no longer active")
        }
        guard session.deviceAddress == firmwarePeripheral?.identifier.uuidString, firmwareDeviceEligible(),
              let ch = firmwareCmdCharacteristic, let p = firmwarePeripheral else {
            throw FirmwareTransferException("The connected strap changed or is no longer ready")
        }
        let rawSeq = firmwareNextSequence()
        let sequence = Int(rawSeq)
        let pending = PendingFirmwareResponse(sessionId: session.id, command: command, sequence: sequence, accept: accept)
        firmwarePending = pending
        let frame = puffinCommandFrame(cmd: UInt8(command), seq: rawSeq, payload: payload)
        // Without response: firmware delivery is judged by the strap's correlated reply, and a WITH-response
        // write would re-enter the 5/MG bond/handshake branch in didWriteValueFor. MTU is pre-checked so
        // the 244-byte chunk frame fits one unacknowledged write.
        p.writeValue(Data(frame), for: ch, type: .withoutResponse)
        firmwareLog("→ firmware command \(command) (\(payload.count) body bytes)")

        // Race the correlated reply against the command timeout.
        let timeoutTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(timeoutMs) * 1_000_000)
            guard let self, !Task.isCancelled else { return }
            if self.firmwarePending === pending, !pending.settled {
                pending.settled = true
                self.firmwarePending = nil
                self.firmwarePendingContinuation?.resume(throwing: FirmwareRetryableTransportException(
                    "No response to firmware command \(command) within \(timeoutMs)ms"))
                self.firmwarePendingContinuation = nil
            }
        }
        defer { timeoutTask.cancel() }
        return try await withCheckedThrowingContinuation { continuation in
            firmwarePendingContinuation = continuation
        }
    }

    /// Route only the correlated response for the one firmware command currently in flight. Twin of
    /// `handleFirmwareCommandResponse`.
    func handleFirmwareCommandResponse(_ frame: [UInt8]) {
        guard firmwareSelectedFamily == .whoop5,
              let pending = firmwarePending, let session = firmwareSession, !pending.settled else { return }
        guard let response = FirmwareWhoop5ResponseDecoder.decode(frame) else { return }
        let sameDevice = session.deviceAddress == firmwarePeripheral?.identifier.uuidString
        guard FirmwareResponseMatcher.correlated(FirmwareResponseKey(
            pendingSessionId: pending.sessionId,
            currentSessionId: session.id,
            expectedCommand: pending.command,
            expectedSequence: pending.sequence,
            actualCommand: response.command,
            actualSequence: response.originSequence,
            sameDevice: sameDevice)) else { return }
        guard pending.accept(response) else { return }
        pending.settled = true
        firmwarePending = nil
        firmwarePendingContinuation?.resume(returning: response)
        firmwarePendingContinuation = nil
    }

    private func resumeFirmwarePending(throwing error: Error) {
        if let pending = firmwarePending { pending.settled = true }
        firmwarePending = nil
        firmwarePendingContinuation?.resume(throwing: error)
        firmwarePendingContinuation = nil
    }

    // MARK: - Terminal transitions

    private func pauseFirmwareTransfer(_ session: FirmwareSession, _ paused: FirmwareTransferPausedException) {
        guard FirmwareActivationObservation.sessionIsCurrent(observedSessionId: session.id, currentSessionId: firmwareSession?.id) else { return }
        session.acknowledgedOffset = paused.acknowledgedOffset
        firmwareTransferTask = nil
        firmwarePending = nil
        firmwareUpdateExclusive = false
        setFirmwareUpdateState(FirmwareUpdateTransitions.paused(firmwareUpdateState, acknowledged: paused.acknowledgedOffset, reason: paused.message))
        firmwareLog("Firmware transfer paused at offset \(paused.acknowledgedOffset) after \(paused.attempts) attempts")
    }

    func finishFirmwareFailure(_ session: FirmwareSession, reason: String) {
        guard FirmwareActivationObservation.sessionIsCurrent(observedSessionId: session.id, currentSessionId: firmwareSession?.id) else { return }
        resumeFirmwarePending(throwing: FirmwareCancellationException(reason))
        firmwareUpdateExclusive = false
        firmwareSession = nil
        firmwareTransferTask = nil
        setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: reason))
        firmwareLog("Firmware update stopped: \(reason)")
    }

    // MARK: - Connection observers

    func firmwareDeviceEligible() -> Bool {
        firmwareSelectedFamily == .whoop5 && state.connected && state.encryptedBond
            && firmwarePeripheral != nil && firmwareCmdCharacteristic != nil
    }

    func refreshFirmwareUpdateEligibility() {
        setFirmwareUpdateState(FirmwareUpdateTransitions.eligibility(firmwareUpdateState, eligible: firmwareDeviceEligible()))
    }

    /// A drop mid-transfer fails the session; a drop after an activation request is the expected reboot
    /// and becomes the bounded reconnect wait. Twin of `noteFirmwareDisconnected`.
    func noteFirmwareDisconnected() {
        guard let session = firmwareSession else {
            setFirmwareUpdateState(FirmwareUpdateTransitions.eligibility(firmwareUpdateState, eligible: false))
            return
        }
        switch firmwareUpdateState.stage {
        case .activationRequested, .reconnecting:
            resumeFirmwarePending(throwing: FirmwareCancellationException("Link changed after activation request"))
            firmwareTransferTask?.cancel()
            firmwareUpdateExclusive = false
            setFirmwareUpdateState(FirmwareUpdateTransitions.reconnecting(firmwareUpdateState))
            firmwareTransferTask = Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(FirmwareActivationObservation.reconnectTimeoutMs) * 1_000_000)
                guard let self, !Task.isCancelled else { return }
                self.finishFirmwareFailure(session, reason: "The strap did not reconnect within 60 seconds after activation; boot outcome remains unknown")
            }
        case .deviceReconnected:
            break
        default:
            finishFirmwareFailure(session, reason: "The strap disconnected before activation completed; transfer will not resume automatically")
        }
    }

    /// The strap reappeared after an activation and reported its version. Twin of `noteFirmwareReportedVersion`.
    func noteFirmwareReportedVersion(_ version: String) {
        guard let session = firmwareSession else {
            refreshFirmwareUpdateEligibility()
            return
        }
        guard session.deviceAddress == firmwarePeripheral?.identifier.uuidString else { return }
        guard FirmwareActivationObservation.canAcceptReportedVersion(firmwareUpdateState.stage) else { return }
        firmwareTransferTask?.cancel()
        firmwareTransferTask = nil
        firmwarePending = nil
        firmwareUpdateExclusive = false
        firmwareSession = nil
        setFirmwareUpdateState(FirmwareUpdateTransitions.reconnected(firmwareUpdateState, reportedVersion: version))
    }

    // MARK: - Helpers

    /// The negotiated ATT MTU for an unacknowledged write (CoreBluetooth adds no 3-byte header to the
    /// caller's max, so re-add it to compare against the 247-byte requirement the 244-byte chunk needs).
    private func negotiatedFirmwareMtu() -> Int {
        guard let p = firmwarePeripheral else { return 0 }
        return p.maximumWriteValueLength(for: .withoutResponse) + 3
    }

    /// The connection generation the firmware session binds to. Reuses the manager's own connect counter.
    private var firmwareConnectionGeneration: Int { firmwareConnectGenerationValue }
}
