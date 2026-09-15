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
// 142/143/83/144 (prepare, write, verify, activate) are sent only from here. The quiesce step's 3/106/20
// also exist on the normal path, but this file writes them past the send() allowlist: 20 deliberately
// without the `backfilling` condition that gates it there (admission already refuses a transfer while this
// app is offloading, so here it is the unconditional quiesce step of the update order), and 106 without
// the raw-capture condition. Nothing is sent automatically, and activation is a separate, explicitly
// confirmed step. Firmware frames are written WITH response, as on Android: the next frame waits for the
// previous frame's write completion (Android's `writeInFlight`), and delivery is judged by the strap's
// correlated COMMAND_RESPONSE; an unanswered chunk is the retryable-timeout case. The completion of a
// firmware frame is routed to `noteFirmwareWriteCompleted` before `didWriteValueFor` reaches its
// bond/handshake branches, so a firmware write can never re-run the 5/MG post-bond path.
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
        /// The framed command until it is handed to CoreBluetooth; nil once written or dropped unsent.
        var unsentFrame: Data?
        init(sessionId: Int, command: Int, sequence: Int, accept: @escaping (FirmwareWireResponse) -> Bool) {
            self.sessionId = sessionId
            self.command = command
            self.sequence = sequence
            self.accept = accept
        }
    }

    /// The transport the engine drives; hops each call onto the manager's main actor. Bound to the one
    /// session its engine was started for, so a frame from an engine that outlived its session is refused.
    private struct Transport: FirmwareTransferTransport {
        unowned let manager: BLEManager
        let sessionId: Int
        func exchange(command: Int, payload: [UInt8], timeoutMs: Int,
                      accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse {
            try await manager.firmwareExchange(sessionId: sessionId, command: command, payload: payload,
                                               timeoutMs: timeoutMs, accept: accept)
        }
    }

    // MARK: - Public API (Test Centre)

    /// Select and validate a local container. This method never touches Bluetooth. Twin of the Android
    /// `selectFirmwareImage`, which Android calls on a background dispatcher: inflating a .zbin and hashing
    /// up to 16 MiB would stall the UI, so the pure parse runs off the main actor here too.
    public func selectFirmwareImage(fileName: String, bytes: [UInt8]) async {
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
        firmwareSelectionGeneration &+= 1
        let generation = firmwareSelectionGeneration
        setFirmwareUpdateState(FirmwareUpdateState(status: "Validating selected image"))
        let parsed = await Task.detached(priority: .userInitiated) {
            FirmwareImageParser.parse(fileName: fileName, input: bytes)
        }.value
        // A clear or a newer selection while the parse ran supersedes this result.
        guard generation == firmwareSelectionGeneration else { return }
        switch parsed {
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
        firmwareSelectionGeneration &+= 1
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
        if firmwareRawCaptureActive {
            setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: "Stop the active ground-truth IMU capture before starting a firmware transfer"))
            return
        }
        if let busy = firmwareBusyReason() {
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
        // Blank counts as unknown, as Android's `takeIf { it.isNotBlank() }`.
        let fw = state.strapFirmware.flatMap {
            $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0
        } ?? "unknown firmware"
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
        restoreAfterFirmwareExclusive()
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
        if firmwareRawCaptureActive {
            var next = firmwareUpdateState
            next.error = "Stop the active ground-truth IMU capture before resuming the firmware transfer"
            setFirmwareUpdateState(next)
            return
        }
        if let busy = firmwareBusyReason() {
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
        let engine = firmwareEngine(session)
        firmwareTransferTask?.cancel()
        firmwareTransferTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                _ = try await engine.activate()
                // The ACK and a disconnect can both land while this task waits to resume. If the drop was
                // handled first, noteFirmwareDisconnected cancelled this task and moved the stage on to
                // RECONNECTING; continuing would rewind it and arm a "did not disconnect" timer that
                // contradicts the drop.
                guard !Task.isCancelled, self.firmwareSession?.id == session.id,
                      self.firmwareUpdateState.stage == .activationRequested else { return }
                // Reconnect handshakes must run, so the command exclusion ends after the activation ACK.
                self.firmwareUpdateExclusive = false
                self.setFirmwareUpdateState(FirmwareUpdateTransitions.activationRequested(self.firmwareUpdateState))
                // Normally this very task, which has nothing left to await; cancelling it keeps the rule
                // that no replaced task survives its slot. The new unstructured task does not inherit it.
                self.firmwareTransferTask?.cancel()
                self.firmwareTransferTask = Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: UInt64(FirmwareActivationObservation.disconnectTimeoutMs) * 1_000_000)
                    guard let self, !Task.isCancelled else { return }
                    self.finishFirmwareFailure(session, reason: "Activation was accepted, but the strap did not disconnect within 30 seconds; boot outcome is unknown")
                }
            } catch is FirmwareCancellationException {
                // A disconnect after sending activation is represented by RECONNECTING, never as a retry.
            } catch {
                self.finishFirmwareFailure(session, reason: (error as? LocalizedError)?.errorDescription
                    ?? "Activation response timed out")
            }
        }
    }

    // MARK: - Transfer driver

    /// Orders the engine's progress states onto the main actor. The engine publishes synchronously but runs
    /// off the main actor, so each state hops back in its own task, and those tasks carry no ordering
    /// guarantee. A state is applied only if it is newer than the last one applied and the run has not
    /// ended, so a late hop can neither rewind progress nor overwrite the paused/failed/ready state the
    /// run ended on. Android publishes synchronously and needs none of this.
    private final class FirmwarePublishOrder {
        /// Written only by the engine's publish calls, which are sequential.
        var issued = 0
        /// Main actor only.
        var applied = 0
        /// Main actor only: set when the run's outcome is handled.
        var closed = false
    }

    private func runFirmwareTransfer(session: FirmwareSession, startOffset: Int = 0, prepareSlot: Bool = true) {
        let engine = firmwareEngine(session)
        let image = session.image
        let order = FirmwarePublishOrder()
        firmwareTransferTask?.cancel()
        firmwareTransferTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let finalState = try await engine.transfer(image: image, initial: self.firmwareUpdateState,
                                                           startOffset: startOffset, prepareSlot: prepareSlot) { next in
                    order.issued += 1
                    let position = order.issued
                    Task { @MainActor [weak self] in
                        guard let self, !order.closed, position > order.applied,
                              self.firmwareSession?.id == session.id else { return }
                        order.applied = position
                        session.acknowledgedOffset = next.bytesAcknowledged
                        self.setFirmwareUpdateState(next)
                    }
                }
                // The final state is applied here, synchronously, so no hop still queued can land after it.
                order.closed = true
                guard self.firmwareSession?.id == session.id else { return }
                session.acknowledgedOffset = finalState.bytesAcknowledged
                self.setFirmwareUpdateState(finalState)
            } catch is FirmwareCancellationException {
                order.closed = true
                // cancelFirmwareUpdate or the disconnect path already published the terminal state.
            } catch let paused as FirmwareTransferPausedException {
                order.closed = true
                self.pauseFirmwareTransfer(session, paused)
            } catch {
                order.closed = true
                // Timeouts outside the chunk loop (3/106/20/142/83) arrive here as the retryable transport
                // error; like Android's `t.message`, show its own text, never Foundation's generic one.
                self.finishFirmwareFailure(session, reason: (error as? LocalizedError)?.errorDescription
                    ?? "Firmware transfer failed")
            }
        }
    }

    private func firmwareEngine(_ session: FirmwareSession) -> FirmwareTransferEngine {
        FirmwareTransferEngine(transport: Transport(manager: self, sessionId: session.id))
    }

    // MARK: - Transport: write one command, await its correlated reply

    func firmwareExchange(sessionId: Int, command: Int, payload: [UInt8], timeoutMs: Int,
                          accept: @escaping (FirmwareWireResponse) -> Bool) async throws -> FirmwareWireResponse {
        // Android's drain-time check that an unsent frame still belongs to the live session runs here, so no
        // pending slot opens for an inactive session, and again in `drainFirmwareWrite`, which hands it over.
        guard FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: sessionId,
                                                               currentSessionId: firmwareSession?.id) else {
            firmwareLog("Dropped an unsent firmware frame from an inactive update session")
            throw FirmwareCancellationException("Firmware session is no longer active")
        }
        guard let session = firmwareSession, firmwareUpdateExclusive else {
            throw FirmwareCancellationException("Firmware session is no longer active")
        }
        guard session.deviceAddress == firmwarePeripheral?.identifier.uuidString, firmwareDeviceEligible(),
              let ch = firmwareCmdCharacteristic, let p = firmwarePeripheral, p.state == .connected else {
            throw FirmwareTransferException("The connected strap changed or is no longer ready")
        }
        // Android ends the session without a retry when its stack refuses a firmware write synchronously.
        // CoreBluetooth's writeValue returns no refusal; the one it would silently make — a characteristic
        // that takes no acknowledged write — is checked here instead, before anything is sent.
        guard ch.properties.contains(.write) else {
            throw FirmwareTransferException(
                "The command characteristic does not accept acknowledged writes; firmware command \(command) was not sent")
        }
        // Exactly one firmware exchange at a time: a second would overwrite the pending slot and orphan the
        // first continuation. Twin of Android's `check(firmwarePending == nil)`, which also throws.
        guard firmwarePending == nil, firmwarePendingContinuation == nil else {
            throw FirmwareTransferException("Another firmware command is awaiting a response")
        }
        let rawSeq = firmwareNextSequence()
        let pending = PendingFirmwareResponse(sessionId: session.id, command: command, sequence: Int(rawSeq), accept: accept)
        pending.unsentFrame = Data(puffinCommandFrame(cmd: UInt8(command), seq: rawSeq, payload: payload))
        firmwarePending = pending
        firmwareLog("→ firmware command \(command) (\(payload.count) body bytes)")

        // Race the correlated reply against the command timeout. The clock starts here, as Android's does at
        // enqueue, so a frame still held behind the previous frame's unconfirmed write spends that wait inside
        // the same budget; if it runs out, the frame is dropped unsent, as Android purges it from its queue.
        let timeoutTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(timeoutMs) * 1_000_000)
            guard let self, !Task.isCancelled else { return }
            if self.firmwarePending === pending, !pending.settled {
                pending.settled = true
                pending.unsentFrame = nil
                self.firmwarePending = nil
                self.firmwarePendingContinuation?.resume(throwing: FirmwareRetryableTransportException(
                    "No response to firmware command \(command) within \(timeoutMs)ms"))
                self.firmwarePendingContinuation = nil
            }
        }
        defer { timeoutTask.cancel() }
        return try await withCheckedThrowingContinuation { continuation in
            firmwarePendingContinuation = continuation
            drainFirmwareWrite()
        }
    }

    /// Hand the pending firmware frame to CoreBluetooth once no earlier firmware write still awaits its
    /// completion. The firmware half of Android's `drainWriteQueue`, with `firmwareWriteInFlight` as its
    /// `writeInFlight`; a frame whose exchange already ended (timeout, cancel, drop) is never sent.
    private func drainFirmwareWrite() {
        guard !firmwareWriteInFlight, let pending = firmwarePending, !pending.settled,
              let frame = pending.unsentFrame else { return }
        guard FirmwareWriteQueuePolicy.belongsToCurrentSession(firmwareSessionId: pending.sessionId,
                                                               currentSessionId: firmwareSession?.id) else {
            pending.unsentFrame = nil
            firmwareLog("Dropped an unsent firmware frame from an inactive update session")
            return
        }
        guard let ch = firmwareCmdCharacteristic, let p = firmwarePeripheral, p.state == .connected else { return }
        pending.unsentFrame = nil
        firmwareWriteInFlight = true
        // WITH response, as Android writes these frames. The MTU was checked at admission so the 244-byte
        // chunk frame fits one ATT write (no long write). Android's write path is validated on a 5/MG
        // (update 50.42.1.0); this iOS path mirrors it but has not itself been run on a strap.
        p.writeValue(frame, for: ch, type: .withResponse)
    }

    /// The completion of the firmware frame in flight, routed here by `didWriteValueFor` ahead of its
    /// bond/handshake branches. Frees the slot for the next frame. A failed completion is only logged, with
    /// the line every other failed acknowledged write gets: the frame's correlated reply then does not come,
    /// and the command timeout turns that into the engine's bounded chunk retry, or a stop for any other
    /// command — how Android's `onCharacteristicWrite` leaves a failed firmware write.
    func noteFirmwareWriteCompleted(error: Error?) {
        firmwareWriteInFlight = false
        if let error {
            firmwareLog("Confirmed write failed: \(error.localizedDescription)\(BLEManager.bleErrorSuffix(error))")
        }
        drainFirmwareWrite()
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
        restoreAfterFirmwareExclusive()
    }

    /// `restoreRealtime` is false only for a drop before activation: the link is gone, so there is no
    /// stream to hand back and the reconnect path re-arms it. Twin of the Android parameter.
    func finishFirmwareFailure(_ session: FirmwareSession, reason: String, restoreRealtime: Bool = true) {
        guard FirmwareActivationObservation.sessionIsCurrent(observedSessionId: session.id, currentSessionId: firmwareSession?.id) else { return }
        resumeFirmwarePending(throwing: FirmwareCancellationException(reason))
        firmwareUpdateExclusive = false
        firmwareSession = nil
        // A drop mid-transfer lands here while the engine may still be sleeping before its next command;
        // cancel it rather than leave it to run into the session check. Called from inside the task, it only
        // marks a task that has nothing left to await.
        firmwareTransferTask?.cancel()
        firmwareTransferTask = nil
        setFirmwareUpdateState(FirmwareUpdateTransitions.failed(firmwareUpdateState, reason: reason))
        firmwareLog("Firmware update stopped: \(reason)")
        if restoreRealtime { restoreAfterFirmwareExclusive() }
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
                // The same strap can be back without a decoded version: the 5/MG hello decoder fails closed on
                // a layout it does not know (#1634), plausibly right after a firmware change. That is a
                // reconnect with an unknown version, not a failure to reconnect.
                if self.state.connected, let p = self.firmwarePeripheral, p.state == .connected,
                   session.deviceAddress == p.identifier.uuidString {
                    self.noteFirmwareReconnectedWithoutVersion(session)
                } else {
                    self.finishFirmwareFailure(session, reason: "The strap did not reconnect within 60 seconds after activation; boot outcome remains unknown")
                }
            }
        case .deviceReconnected:
            break
        default:
            finishFirmwareFailure(session, reason: "The strap disconnected before activation completed; transfer will not resume automatically",
                                  restoreRealtime: false)
        }
    }

    /// The reconnect wait ran out with the same strap connected again but no firmware version decoded.
    /// Ends the session like `noteFirmwareReportedVersion`, with a status that names no version.
    private func noteFirmwareReconnectedWithoutVersion(_ session: FirmwareSession) {
        guard FirmwareActivationObservation.sessionIsCurrent(observedSessionId: session.id, currentSessionId: firmwareSession?.id),
              FirmwareActivationObservation.canAcceptReportedVersion(firmwareUpdateState.stage) else { return }
        firmwareTransferTask = nil
        firmwarePending = nil
        firmwareUpdateExclusive = false
        firmwareSession = nil
        setFirmwareUpdateState(FirmwareUpdateTransitions.reconnected(firmwareUpdateState, reportedVersion: nil))
        firmwareLog("Firmware update: the strap reconnected after activation but reported no firmware version within 60 seconds; the running version is unconfirmed")
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

    /// Why a transfer may not start or resume on this link now, or nil. Feeds the shared admission with
    /// this link's stand-ins for Android's write and CCCD queues (see the counters in BLEManager): an
    /// unconfirmed firmware frame or other acknowledged write, and unconfirmed notify-state changes.
    private func firmwareBusyReason() -> String? {
        FirmwareUpdateAdmission.busyReason(
            backfilling: state.backfilling,
            writeInFlight: firmwareWriteInFlight,
            retryPending: false,
            queuedWrites: firmwareOtherWritesOutstanding,
            negotiatedMtu: negotiatedFirmwareMtu(),
            requiredMtu: 247,
            cccdInFlight: false,
            queuedCccds: firmwarePendingNotifyChanges)
    }

    /// The negotiated ATT MTU: the unacknowledged-write maximum is ATT_MTU - 3 (CoreBluetooth adds no
    /// header to it), so re-add the 3 bytes to compare against the 247 the 244-byte chunk frame needs. The
    /// frames go out WITH response, but that maximum reports the 512-byte long-write limit instead and so
    /// cannot tell whether a frame fits one ATT write. iOS cannot request a larger MTU; below 247 the
    /// admission refuses every transfer with the MTU message.
    private func negotiatedFirmwareMtu() -> Int {
        guard let p = firmwarePeripheral else { return 0 }
        return p.maximumWriteValueLength(for: .withoutResponse) + 3
    }

    /// The connection generation the firmware session binds to. Reuses the manager's own connect counter.
    private var firmwareConnectionGeneration: Int { firmwareConnectGenerationValue }
}
