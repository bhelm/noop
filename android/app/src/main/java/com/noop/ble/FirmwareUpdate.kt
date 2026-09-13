package com.noop.ble

import java.security.MessageDigest
import java.util.Locale
import java.util.zip.CRC32

/** The two container types whose layout and CRC predicates are retained from the strap firmware. */
enum class FirmwareImageFormat(val containerType: Long, val displayName: String) {
    BIN_RAW(1, "Raw BIN (experimental)"),
    ZBIN_COMPRESSED(5, "Original compressed ZBIN"),
}

data class FirmwareImageInfo(
    val fileName: String,
    val byteCount: Int,
    val format: FirmwareImageFormat,
    val version: String,
    val payloadLength: Int,
    val payloadCrc32: String,
    val headerCrc32: String,
    val sha256: String,
    val compatibilityNote: String,
)

internal data class ValidatedFirmwareImage(
    val info: FirmwareImageInfo,
    /** Immutable session bytes. The parser always copies the caller-owned array. */
    val bytes: ByteArray,
)

sealed class FirmwareImageValidation {
    internal data class Valid(val image: ValidatedFirmwareImage) : FirmwareImageValidation()
    data class Invalid(val reason: String) : FirmwareImageValidation()
}

/**
 * Fail-closed parser for the 512-byte WHOOP update container.
 *
 * CRC32 is CRC-32/ISO-HDLC, the algorithm used by java.util.zip.CRC32 and by the audited validators.
 * CRC is integrity evidence only; it says nothing about signatures, compatibility, or successful boot.
 */
object FirmwareImageParser {
    const val HEADER_SIZE = 512
    const val CHUNK_SIZE = 220
    const val MAX_IMAGE_BYTES = 16 * 1024 * 1024 // App memory/input policy, not a device capacity claim.

    fun parse(fileName: String, input: ByteArray): FirmwareImageValidation {
        val extension = fileName.substringAfterLast('.', missingDelimiterValue = "").lowercase(Locale.ROOT)
        if (extension != "zbin" && extension != "bin") {
            return FirmwareImageValidation.Invalid("Choose a .zbin or .bin firmware container")
        }
        if (input.size < HEADER_SIZE + 4) {
            return FirmwareImageValidation.Invalid("Image is shorter than the 512-byte header and payload")
        }
        if (input.size > MAX_IMAGE_BYTES) {
            return FirmwareImageValidation.Invalid("Image exceeds the app's 16 MiB safety limit")
        }
        if (input.size % 4 != 0) {
            return FirmwareImageValidation.Invalid("Image length must be a multiple of 4 bytes")
        }

        val declaredPayload = input.u32le(4)
        if (declaredPayload > Int.MAX_VALUE.toLong()) {
            return FirmwareImageValidation.Invalid("Declared payload length is too large")
        }
        val payloadLength = declaredPayload.toInt()
        if (payloadLength <= 0 || payloadLength != input.size - HEADER_SIZE) {
            return FirmwareImageValidation.Invalid(
                "Declared payload length $payloadLength does not match ${input.size - HEADER_SIZE} bytes",
            )
        }
        if (payloadLength % 4 != 0) {
            return FirmwareImageValidation.Invalid("Payload length must be a multiple of 4 bytes")
        }

        val type = input.u32le(8)
        val format = FirmwareImageFormat.entries.firstOrNull { it.containerType == type }
            ?: return FirmwareImageValidation.Invalid("Unsupported firmware container type $type")
        val expectedExtension = if (format == FirmwareImageFormat.ZBIN_COMPRESSED) "zbin" else "bin"
        if (extension != expectedExtension) {
            return FirmwareImageValidation.Invalid(
                "The .$extension name does not match the container type (${format.displayName})",
            )
        }

        val storedPayloadCrc = input.u32le(0)
        val computedPayloadCrc = input.crc32(HEADER_SIZE, input.size)
        if (storedPayloadCrc != computedPayloadCrc) {
            return FirmwareImageValidation.Invalid(
                "Payload CRC mismatch: stored ${storedPayloadCrc.hex8()}, computed ${computedPayloadCrc.hex8()}",
            )
        }
        val storedHeaderCrc = input.u32le(504)
        val computedHeaderCrc = input.crc32(8, 504)
        if (storedHeaderCrc != computedHeaderCrc) {
            return FirmwareImageValidation.Invalid(
                "Header CRC mismatch: stored ${storedHeaderCrc.hex8()}, computed ${computedHeaderCrc.hex8()}",
            )
        }

        val version = listOf(0x7c, 0x80, 0x84, 0x88)
            .joinToString(".") { input.u32le(it).toString() }
        val note = when (format) {
            FirmwareImageFormat.ZBIN_COMPRESSED ->
                "Original compressed update form. CRCs do not prove device compatibility or authentication."
            FirmwareImageFormat.BIN_RAW ->
                "Research raw form. Installation of this decompressed type is not established."
        }
        val immutable = input.copyOf()
        val info = FirmwareImageInfo(
            fileName = fileName,
            byteCount = immutable.size,
            format = format,
            version = version,
            payloadLength = payloadLength,
            payloadCrc32 = storedPayloadCrc.hex8(),
            headerCrc32 = storedHeaderCrc.hex8(),
            sha256 = MessageDigest.getInstance("SHA-256").digest(immutable).toHex(),
            compatibilityNote = note,
        )
        return FirmwareImageValidation.Valid(ValidatedFirmwareImage(info, immutable))
    }
}

enum class FirmwareUpdateStage {
    EMPTY,
    IMAGE_READY,
    PREPARING,
    WRITING,
    REMOTE_VALIDATING,
    READY_TO_ACTIVATE,
    ACTIVATION_REQUESTED,
    RECONNECTING,
    DEVICE_RECONNECTED,
    FAILED,
    CANCELLED,
}

data class FirmwareUpdateState(
    val stage: FirmwareUpdateStage = FirmwareUpdateStage.EMPTY,
    val image: FirmwareImageInfo? = null,
    val bytesAcknowledged: Int = 0,
    val totalBytes: Int = image?.byteCount ?: 0,
    val status: String = "Choose an original .zbin update image",
    val error: String? = null,
    val log: List<String> = emptyList(),
    val deviceEligible: Boolean = false,
    val lockedDeviceLabel: String? = null,
) {
    val progress: Float
        get() = if (totalBytes <= 0) 0f else (bytesAcknowledged.toFloat() / totalBytes).coerceIn(0f, 1f)
    val canStart: Boolean get() = stage == FirmwareUpdateStage.IMAGE_READY && deviceEligible
    val canActivate: Boolean get() = stage == FirmwareUpdateStage.READY_TO_ACTIVATE
    val canCancel: Boolean get() = stage in setOf(
        FirmwareUpdateStage.PREPARING,
        FirmwareUpdateStage.WRITING,
        FirmwareUpdateStage.REMOTE_VALIDATING,
        FirmwareUpdateStage.READY_TO_ACTIVATE,
    )
}

/** Pure state transitions shared by the BLE integration and unit tests. */
internal object FirmwareUpdateTransitions {
    private const val MAX_LOG_LINES = 80

    fun selected(image: FirmwareImageInfo, eligible: Boolean): FirmwareUpdateState = FirmwareUpdateState(
        stage = FirmwareUpdateStage.IMAGE_READY,
        image = image,
        totalBytes = image.byteCount,
        status = if (eligible) "Image validated locally. Ready to transfer." else
            "Image validated. Connect and bond a WHOOP 5/MG strap to continue.",
        deviceEligible = eligible,
    ).withLog("Local header, length, payload CRC and header CRC verified")

    fun begin(state: FirmwareUpdateState, deviceLabel: String): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.PREPARING,
        bytesAcknowledged = 0,
        status = "Preparing the strap's update slot",
        error = null,
        lockedDeviceLabel = deviceLabel,
        deviceEligible = true,
    ).withLog("Exclusive update session acquired for $deviceLabel")

    fun writing(state: FirmwareUpdateState, acknowledged: Int): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.WRITING,
        bytesAcknowledged = acknowledged.coerceIn(0, state.totalBytes),
        status = "Writing firmware: ${acknowledged.coerceIn(0, state.totalBytes)} / ${state.totalBytes} bytes acknowledged",
    )

    fun remoteValidating(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.REMOTE_VALIDATING,
        bytesAcknowledged = state.totalBytes,
        status = "Transfer complete. Waiting for the strap's asynchronous integrity result.",
    ).withLog("All chunks acknowledged; remote validation requested")

    fun ready(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.READY_TO_ACTIVATE,
        status = "The strap accepted the transferred image integrity check. Activation still requires your confirmation.",
    ).withLog("Remote validation succeeded; activation unlocked for this session")

    fun activationRequested(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.ACTIVATION_REQUESTED,
        status = "Activation/reset request accepted. Waiting for the link to change; boot is not yet confirmed.",
    ).withLog("Activation/reset request accepted; this is not proof that the image booted")

    fun reconnecting(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.RECONNECTING,
        status = "The strap disconnected after activation. Waiting for a fresh connection; no transfer will be resumed.",
    ).withLog("Link dropped after activation request")

    fun reconnected(state: FirmwareUpdateState, reportedVersion: String?): FirmwareUpdateState {
        val suffix = reportedVersion?.let { " and reports firmware $it" } ?: ""
        return state.copy(
            stage = FirmwareUpdateStage.DEVICE_RECONNECTED,
            status = "The same strap reconnected$suffix. This does not prove which image booted.",
        ).withLog("Same device reconnected${reportedVersion?.let { "; reported version $it" } ?: ""}")
    }

    fun failed(state: FirmwareUpdateState, reason: String): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.FAILED,
        status = "Firmware update stopped",
        error = reason,
        deviceEligible = false,
    ).withLog("Stopped: $reason")

    fun cancelled(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.CANCELLED,
        status = "Update session cancelled. The app will not resume or activate it automatically.",
        error = null,
        deviceEligible = false,
    ).withLog("Cancelled by user; no automatic retry")

    fun eligibility(state: FirmwareUpdateState, eligible: Boolean): FirmwareUpdateState {
        if (state.stage != FirmwareUpdateStage.IMAGE_READY) return state
        return state.copy(
            deviceEligible = eligible,
            status = if (eligible) "Image validated locally. Ready to transfer." else
                "Image validated. Connect and bond a WHOOP 5/MG strap to continue.",
        )
    }

    private fun FirmwareUpdateState.withLog(line: String): FirmwareUpdateState =
        copy(log = (log + line).takeLast(MAX_LOG_LINES))
}

private fun ByteArray.u32le(offset: Int): Long =
    (this[offset].toLong() and 0xff) or
        ((this[offset + 1].toLong() and 0xff) shl 8) or
        ((this[offset + 2].toLong() and 0xff) shl 16) or
        ((this[offset + 3].toLong() and 0xff) shl 24)

private fun ByteArray.crc32(from: Int, until: Int): Long = CRC32().run {
    update(this@crc32, from, until - from)
    value
}

private fun Long.hex8(): String = "%08x".format(Locale.ROOT, this)
private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }
