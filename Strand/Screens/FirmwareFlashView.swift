import SwiftUI
import UniformTypeIdentifiers
import StrandDesign
import WhoopProtocol

/// The Test Centre firmware-update body. File selection and local validation are deliberately separate
/// from transfer, and transfer is deliberately separate from activation. A local CRC match proves that
/// the selected bytes are internally intact; it does not authenticate the publisher or prove that the
/// firmware is compatible with the connected strap. Feature-level twin of the Android
/// `FirmwareFlashContent` (com.noop.ui). Shared macOS + iOS; only design tokens and the shared file
/// importer are used.
struct FirmwareFlashView: View {
    @ObservedObject var ble: BLEManager
    let hasActiveDevice: Bool
    let hasWhoop5MgEvidence: Bool
    let connected: Bool
    let encryptedBond: Bool
    let reportedFirmware: String?

    @State private var pickerOpen = false
    @State private var fileReadError: String?
    @State private var showActivationConfirmation = false

    private var state: FirmwareUpdateState { ble.firmwareUpdateState }
    private var uiBusy: Bool { pickerOpen }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("The update process is safeguarded: NOOP checks the image before the transfer, and the strap checks it again itself before activation. The risk is the same as with a regular firmware update.")
                .font(StrandFont.footnote).foregroundStyle(StrandPalette.statusWarning)
                .fixedSize(horizontal: false, vertical: true)
            Text("For a normal original update, extract the vendor ZIP and select its .zbin file. A .bin file is the decompressed research form.")
                .font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)

            readiness

            NoopButton(state.image == nil
                       ? "Choose .zbin or .bin image"
                       : "Choose a different image",
                       systemImage: "folder", kind: .secondary, fullWidth: true) {
                fileReadError = nil
                pickerOpen = true
            }
            .disabled(!FirmwareFlashUiPolicy.canChooseFile(state.stage, uiBusy: uiBusy))

            if let error = fileReadError {
                Text(error).font(StrandFont.footnote).foregroundStyle(StrandPalette.statusCritical)
            }

            imageFacts
            statusBlock

            NoopButton("Transfer image to strap", systemImage: "play.fill", kind: .primary, fullWidth: true) {
                ble.startFirmwareTransfer()
            }
            .disabled(!(state.canStart && !uiBusy))

            if state.stage == .paused {
                Text("The transfer can resume only while this exact Bluetooth connection remains active and the selected image is unchanged. After a disconnect, start again from the beginning.")
                    .font(StrandFont.footnote).foregroundStyle(StrandPalette.statusWarning)
                    .fixedSize(horizontal: false, vertical: true)
                NoopButton("Resume on this connection", systemImage: "play.fill", kind: .primary, fullWidth: true) {
                    ble.resumeFirmwareTransfer()
                }
                .disabled(!(state.canResume && !uiBusy))
            }

            if state.stage == .readyToActivate {
                Text("The strap acknowledged the complete transfer and returned a successful remote validation result. Activation is still separate.")
                    .font(StrandFont.footnote).foregroundStyle(StrandPalette.statusPositive)
                    .fixedSize(horizontal: false, vertical: true)
                NoopButton("Review and activate…", kind: .destructive, fullWidth: true) {
                    showActivationConfirmation = true
                }
                .disabled(!(state.canActivate && !uiBusy))
            }

            if state.canCancel {
                NoopButton("Stop this update session", systemImage: "stop.fill", kind: .secondary, fullWidth: true) {
                    ble.cancelFirmwareUpdate()
                }
                .disabled(uiBusy)
                Text("Stopping prevents further sends from NOOP. It does not roll back bytes the strap has already acknowledged.")
                    .font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if FirmwareFlashUiPolicy.canClear(state.stage, uiBusy: uiBusy) {
                NoopButton("Clear image and status", kind: .tertiary, fullWidth: true) {
                    fileReadError = nil
                    ble.clearFirmwareImage()
                }
            }
        }
        .fileImporter(isPresented: $pickerOpen, allowedContentTypes: [.data, .item], allowsMultipleSelection: false) { result in
            pickerOpen = false
            handleImport(result)
        }
        .onChangeCompat(of: state.stage) { newStage in
            if newStage != .readyToActivate { showActivationConfirmation = false }
        }
        .alert("Activate the staged firmware?", isPresented: $showActivationConfirmation) {
            Button("Not now", role: .cancel) { showActivationConfirmation = false }
            Button("Activate firmware", role: .destructive) {
                showActivationConfirmation = false
                ble.activateVerifiedFirmware()
            }
            .disabled(!state.canActivate)
        } message: {
            Text(activationMessage)
        }
    }

    // MARK: - Readiness

    @ViewBuilder private var readiness: some View {
        let (text, color): (String, Color) = {
            if !hasActiveDevice {
                return (String(localized: "Select a WHOOP 5/MG as the active device before transfer. Local image inspection remains available."), StrandPalette.statusWarning)
            }
            if !hasWhoop5MgEvidence {
                return (String(localized: "Transfer is supported only with positive WHOOP 5/MG evidence. WHOOP 4.0 and non-WHOOP devices are blocked."), StrandPalette.statusWarning)
            }
            if !connected {
                return (String(localized: "The active WHOOP 5/MG is disconnected. Connect it before transfer."), StrandPalette.statusWarning)
            }
            if !encryptedBond {
                return (String(localized: "The strap is connected, but its encrypted WHOOP 5/MG session is not ready."), StrandPalette.statusWarning)
            }
            let fw = reportedFirmware ?? String(localized: "unknown")
            return (String(format: String(localized: "Active WHOOP 5/MG is connected and paired · reported firmware %@"), fw), StrandPalette.statusPositive)
        }()
        Text(text).font(StrandFont.footnote).foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: - Image facts

    @ViewBuilder private var imageFacts: some View {
        if let image = state.image {
            Divider().overlay(StrandPalette.hairline)
            VStack(alignment: .leading, spacing: 5) {
                Text(verbatim: image.fileName).font(StrandFont.subhead).foregroundStyle(StrandPalette.textPrimary)
                Text("Local header, length, payload CRC and header CRC checks passed.")
                    .font(StrandFont.footnote).foregroundStyle(StrandPalette.statusPositive)
                fact(String(localized: "Format"), formatLabel(image.format))
                fact(String(localized: "Image size"), formatFirmwareBytes(image.byteCount))
                versionPair(image: image)
                fact(String(localized: "Payload size"), formatFirmwareBytes(image.payloadLength))
                fact(String(localized: "Payload CRC32"), image.payloadCrc32, mono: true)
                fact(String(localized: "Header CRC32"), image.headerCrc32, mono: true)
                fact(String(localized: "SHA-256"), image.sha256, mono: true)
                Text(compatLabel(image.format))
                    .font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func formatLabel(_ format: FirmwareImageFormat) -> String {
        switch format {
        case .zbinCompressed: return String(localized: "Vendor OTA .zbin · compressed type 5")
        case .binRaw: return String(localized: "Raw research .bin · type 1")
        }
    }

    private func compatLabel(_ format: FirmwareImageFormat) -> String {
        switch format {
        case .zbinCompressed: return String(localized: "The strap’s regular update format.")
        case .binRaw: return String(localized: "Research raw form. Installation of this decompressed type is not established.")
        }
    }

    // MARK: - Status

    @ViewBuilder private var statusBlock: some View {
        if state.stage != .empty {
            Divider().overlay(StrandPalette.hairline)
            VStack(alignment: .leading, spacing: 6) {
                Text("Update status").font(StrandFont.subhead).foregroundStyle(StrandPalette.textPrimary)
                Text(verbatim: state.status).font(StrandFont.footnote).foregroundStyle(StrandPalette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let error = state.error {
                    Text(verbatim: error).font(StrandFont.footnote).foregroundStyle(StrandPalette.statusCritical)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let label = state.lockedDeviceLabel {
                    fact(String(localized: "Locked session"), label)
                }
                if FirmwareFlashUiPolicy.showsProgress(state.stage) {
                    ProgressView(value: Double(state.progress))
                        .tint(StrandPalette.accent)
                    Text(verbatim: String(format: String(localized: "%1$@ / %2$@ acknowledged · %3$lld%%"),
                                          formatFirmwareBytes(state.bytesAcknowledged),
                                          formatFirmwareBytes(state.totalBytes),
                                          Int(state.progress * 100)))
                        .font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                    if state.stage == .remoteValidating {
                        Text("All image bytes were acknowledged. The strap is checking the staged image; activation has not started.")
                            .font(StrandFont.footnote).foregroundStyle(StrandPalette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    // MARK: - Versions

    @ViewBuilder private func versionPair(image: FirmwareImageInfo) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Current strap").font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                Text(verbatim: reportedFirmware ?? String(localized: "unknown"))
                    .font(StrandFont.subhead.monospaced()).foregroundStyle(StrandPalette.textSecondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                Text("Selected image").font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                Text(verbatim: image.version)
                    .font(StrandFont.subhead.monospaced()).foregroundStyle(StrandPalette.textSecondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        versionNote(compareFirmwareVersions(current: reportedFirmware, target: image.version))
    }

    @ViewBuilder private func versionNote(_ relation: FirmwareVersionRelation) -> some View {
        let (message, color): (String, Color) = {
            switch relation {
            case .downgrade: return (String(localized: "Downgrade: the selected image is older than the firmware currently reported by the strap. Transfer and activation remain available for this test."), StrandPalette.statusWarning)
            case .same: return (String(localized: "This firmware version is already reported as installed. You can intentionally transfer and activate it again."), StrandPalette.textSecondary)
            case .upgrade: return (String(localized: "The selected image version is newer than the firmware currently reported by the strap."), StrandPalette.statusPositive)
            case .incomparable: return (String(localized: "The current or selected version is unknown or malformed, so their order cannot be determined."), StrandPalette.statusWarning)
            }
        }()
        Text(message).font(StrandFont.footnote).foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var activationMessage: String {
        let identity = String(format: String(localized: "Image: %1$@\nCurrent strap: %2$@\nSelected image: %3$@\nDevice session: %4$@"),
                              state.image?.fileName ?? "?",
                              reportedFirmware ?? String(localized: "unknown"),
                              state.image?.version ?? "?",
                              state.lockedDeviceLabel ?? String(localized: "unknown device"))
        let warning = String(localized: "The strap restarts on the new firmware and briefly disconnects. It then reconnects and reports its version. Keep the strap nearby and charged until it is connected again.")
        return identity + "\n\n" + warning
    }

    // MARK: - Helpers

    @ViewBuilder private func fact(_ label: String, _ value: String, mono: Bool = false) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(verbatim: label).font(StrandFont.footnote).foregroundStyle(StrandPalette.textTertiary)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(verbatim: value)
                .font(mono ? StrandFont.footnote.monospaced() : StrandFont.footnote)
                .foregroundStyle(StrandPalette.textSecondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func handleImport(_ result: Result<[URL], Error>) {
        switch result {
        case .failure:
            fileReadError = String(localized: "The selected image could not be read.")
        case .success(let urls):
            guard let url = urls.first else { return }
            // Fail closed before reading the replacement: a read failure may never leave a previously
            // validated image armed behind a newly displayed file name.
            ble.clearFirmwareImage()
            fileReadError = nil
            let needsScope = url.startAccessingSecurityScopedResource()
            defer { if needsScope { url.stopAccessingSecurityScopedResource() } }
            do {
                let data = try Data(contentsOf: url)
                try validateFirmwareDocumentSize(byteCount: data.count)
                ble.selectFirmwareImage(fileName: url.lastPathComponent, bytes: [UInt8](data))
            } catch is FirmwareImageTooLargeError {
                fileReadError = String(localized: "The selected image exceeds the app’s 16 MiB safety limit.")
            } catch is EmptyFirmwareImageError {
                fileReadError = String(localized: "The selected image is empty.")
            } catch {
                fileReadError = String(localized: "The selected image could not be read.")
            }
        }
    }
}
