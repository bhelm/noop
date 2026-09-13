package com.noop.ble

import com.noop.protocol.Crc
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.DataFormatException
import java.util.zip.Inflater

/** The two container types whose layout and CRC predicates are retained from the strap firmware. */
enum class FirmwareImageFormat(val containerType: Long, val displayName: String) {
    BIN_RAW(1, "Raw BIN (experimental)"),
    ZBIN_COMPRESSED(5, "Compressed ZBIN (OTA format)"),
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

internal data class FirmwareWireResponse(
    val command: Int,
    val originSequence: Int,
    val result: Int,
    val body: ByteArray,
)

/** Decode the WHOOP 5/MG command-response envelope used by the firmware transaction. */
internal object FirmwareWhoop5ResponseDecoder {
    private const val COMMAND_RESPONSE = 0x24
    private const val PUFFIN_COMMAND_RESPONSE = 0x26
    private const val BODY_START = 13

    fun decode(frame: ByteArray): FirmwareWireResponse? {
        if (frame.size < 20 || frame[0] != 0xaa.toByte()) return null
        val declaredLength = frame.u16le(2)
        if (declaredLength < 4 || declaredLength + 8 != frame.size) return null
        val payloadEnd = frame.size - 4
        if (Crc.crc16Modbus(frame, 0, 6) != frame.u16le(6)) return null
        if (Crc.crc32(frame, 8, payloadEnd) != frame.u32le(payloadEnd)) return null

        val responseType = frame[8].toInt() and 0xff
        if (responseType != COMMAND_RESPONSE && responseType != PUFFIN_COMMAND_RESPONSE) return null
        val command = frame[10].toInt() and 0xff
        val bodyLength = when (command) {
            FirmwareTransferEngine.VERIFY_COMMAND -> 1
            FirmwareTransferEngine.PREPARE_COMMAND,
            FirmwareTransferEngine.WRITE_COMMAND,
            FirmwareTransferEngine.ACTIVATE_COMMAND -> 2
            else -> return null
        }
        val unpaddedInnerLength = 5 + bodyLength
        val paddedInnerLength = (unpaddedInnerLength + 3) and -4
        if (payloadEnd - 8 != paddedInnerLength || BODY_START + bodyLength > payloadEnd) return null
        return FirmwareWireResponse(
            command = command,
            originSequence = frame[11].toInt() and 0xff,
            result = frame[12].toInt() and 0xff,
            body = frame.copyOfRange(BODY_START, BODY_START + bodyLength),
        )
    }
}

internal data class FirmwareResponseKey(
    val pendingSessionId: Int,
    val currentSessionId: Int,
    val expectedCommand: Int,
    val expectedSequence: Int,
    val actualCommand: Int,
    val actualSequence: Int,
    val sameDevice: Boolean,
)

internal object FirmwareResponseMatcher {
    fun correlated(key: FirmwareResponseKey): Boolean =
        key.pendingSessionId == key.currentSessionId && key.sameDevice &&
            key.expectedCommand == key.actualCommand && key.expectedSequence == key.actualSequence

    /** VERIFY is asynchronous; body[0] == 1 is the recovered final-result discriminator. */
    fun isFinal(command: Int, response: FirmwareWireResponse): Boolean =
        command != FirmwareTransferEngine.VERIFY_COMMAND ||
            (response.result != 2 && response.body.firstOrNull()?.toInt() == 1)
}

internal object FirmwareUpdateAdmission {
    /** A 220-byte data chunk produces a 244-byte puffin frame, requiring ATT MTU 247 with its 3-byte header. */
    fun busyReason(
        backfilling: Boolean,
        writeInFlight: Boolean,
        retryPending: Boolean,
        queuedWrites: Int,
        negotiatedMtu: Int = Int.MAX_VALUE,
        requiredMtu: Int = 247,
        cccdInFlight: Boolean = false,
        queuedCccds: Int = 0,
    ): String? {
        val mtuLabel = if (negotiatedMtu > 0) negotiatedMtu.toString() else "not negotiated"
        return when {
            backfilling || writeInFlight || retryPending || queuedWrites > 0 ->
                "Bluetooth is busy with another strap operation. Wait for it to finish, then reselect the image."
            negotiatedMtu < requiredMtu ->
                "The Bluetooth MTU is $mtuLabel; firmware chunks require MTU $requiredMtu. Reconnect and reselect the image."
            cccdInFlight || queuedCccds > 0 ->
                "Bluetooth notification setup is still finishing. Wait for it to complete, then reselect the image."
            else -> null
        }
    }
}

/** Keep ordinary BLE queue behavior intact while fail-closing session-bound OTA writes. */
internal object FirmwareWriteQueuePolicy {
    fun belongsToCurrentSession(firmwareSessionId: Int?, currentSessionId: Int?): Boolean =
        firmwareSessionId == null || firmwareSessionId == currentSessionId

    fun mayRetryAfterAmbiguousRejection(firmwareSessionId: Int?): Boolean = firmwareSessionId == null
}

internal object FirmwareActivationObservation {
    const val DISCONNECT_TIMEOUT_MS = 30_000L
    const val RECONNECT_TIMEOUT_MS = 60_000L

    fun sessionIsCurrent(observedSessionId: Int, currentSessionId: Int?): Boolean =
        observedSessionId == currentSessionId

    fun canAcceptReportedVersion(stage: FirmwareUpdateStage): Boolean =
        stage == FirmwareUpdateStage.RECONNECTING
}

internal fun interface FirmwareTransferTransport {
    suspend fun exchange(
        command: Int,
        payload: ByteArray,
        timeoutMs: Long,
        accept: (FirmwareWireResponse) -> Boolean,
    ): FirmwareWireResponse
}

/**
 * Transport-independent OTA transaction. The BLE client supplies correlation/timeouts; tests supply a
 * deterministic fake. Every failure stops the plan and there is deliberately no retry or resume branch.
 */
internal class FirmwareTransferEngine(private val transport: FirmwareTransferTransport) {
    companion object {
        const val PREPARE_COMMAND = 142
        const val WRITE_COMMAND = 143
        const val ACTIVATE_COMMAND = 144
        const val VERIFY_COMMAND = 83
        const val COMMAND_TIMEOUT_MS = 8_000L
        const val VERIFY_TIMEOUT_MS = 30_000L
    }

    suspend fun transfer(
        image: ValidatedFirmwareImage,
        initial: FirmwareUpdateState,
        publish: (FirmwareUpdateState) -> Unit,
    ): FirmwareUpdateState {
        requireAccepted(
            exchange(PREPARE_COMMAND, byteArrayOf(1), COMMAND_TIMEOUT_MS),
            expectedTail = 0,
            step = "prepare",
        )
        var state = initial
        var offset = 0
        while (offset < image.bytes.size) {
            val count = minOf(FirmwareImageParser.CHUNK_SIZE, image.bytes.size - offset)
            val payload = ByteArray(6 + count)
            payload[0] = 1
            payload[1] = (offset and 0xff).toByte()
            payload[2] = ((offset ushr 8) and 0xff).toByte()
            payload[3] = ((offset ushr 16) and 0xff).toByte()
            payload[4] = ((offset ushr 24) and 0xff).toByte()
            payload[5] = count.toByte()
            image.bytes.copyInto(payload, 6, offset, offset + count)
            requireAccepted(
                exchange(WRITE_COMMAND, payload, COMMAND_TIMEOUT_MS),
                expectedTail = 0,
                step = "write at offset $offset",
            )
            offset += count
            state = FirmwareUpdateTransitions.writing(state, offset)
            publish(state)
        }
        state = FirmwareUpdateTransitions.remoteValidating(state)
        publish(state)
        val verified = exchange(VERIFY_COMMAND, byteArrayOf(1), VERIFY_TIMEOUT_MS)
        if (verified.result != 1 || verified.body.firstOrNull()?.toInt() != 1) {
            throw FirmwareTransferException("The strap reported that remote image validation failed")
        }
        return FirmwareUpdateTransitions.ready(state).also(publish)
    }

    suspend fun activate(): FirmwareWireResponse {
        val response = exchange(ACTIVATE_COMMAND, byteArrayOf(1), COMMAND_TIMEOUT_MS)
        if (response.result != 1 || response.body.size < 2 ||
            response.body[0].toInt() != 1 || response.body[1].toInt() != 1
        ) throw FirmwareTransferException("The strap rejected the activation/reset request")
        return response
    }

    private suspend fun exchange(command: Int, payload: ByteArray, timeoutMs: Long): FirmwareWireResponse =
        transport.exchange(command, payload, timeoutMs) { FirmwareResponseMatcher.isFinal(command, it) }

    private fun requireAccepted(response: FirmwareWireResponse, expectedTail: Int, step: String) {
        if (response.result != 1 || response.body.size < 2 ||
            response.body[0].toInt() != 1 || response.body[1].toInt() != expectedTail
        ) throw FirmwareTransferException("The strap rejected firmware $step")
    }
}

internal class FirmwareTransferException(message: String) : Exception(message)

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
    const val MAX_INFLATED_IMAGE_BYTES = 16 * 1024 * 1024 // Host memory policy, not proven strap capacity.

    private const val GZIP_FIXED_HEADER_SIZE = 10
    private const val GZIP_TRAILER_SIZE = 8
    private const val GZIP_FLAG_HEADER_CRC = 0x02
    private const val GZIP_FLAG_EXTRA = 0x04
    private const val GZIP_FLAG_NAME = 0x08
    private const val GZIP_FLAG_COMMENT = 0x10
    private const val GZIP_RESERVED_FLAGS = 0xe0

    private sealed interface InflationResult {
        data class Valid(val bytes: ByteArray) : InflationResult
        data class Invalid(val reason: String) : InflationResult
    }

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

        val type = input.u32le(12)
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

        val version = input.versionString()
        if (format == FirmwareImageFormat.ZBIN_COMPRESSED) {
            val inflated = when (val result = inflateSingleGzipMember(input)) {
                is InflationResult.Valid -> result.bytes
                is InflationResult.Invalid -> return FirmwareImageValidation.Invalid(result.reason)
            }
            validateNestedRawImage(inflated, version)?.let {
                return FirmwareImageValidation.Invalid(it)
            }
        }
        val note = when (format) {
            FirmwareImageFormat.ZBIN_COMPRESSED ->
                "Vendor OTA container with a structurally valid nested raw image. CRCs do not prove device compatibility or authentication."
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

    private fun inflateSingleGzipMember(container: ByteArray): InflationResult {
        val start = HEADER_SIZE
        val end = container.size
        if (end - start < GZIP_FIXED_HEADER_SIZE + GZIP_TRAILER_SIZE) {
            return InflationResult.Invalid("Compressed payload is too short to contain a complete gzip member")
        }
        if (container[start].toInt() and 0xff != 0x1f || container[start + 1].toInt() and 0xff != 0x8b) {
            return InflationResult.Invalid("Compressed payload does not start with a gzip header")
        }
        if (container[start + 2].toInt() and 0xff != 8) {
            return InflationResult.Invalid("Compressed payload uses an unsupported gzip compression method")
        }

        val flags = container[start + 3].toInt() and 0xff
        if (flags and GZIP_RESERVED_FLAGS != 0) {
            return InflationResult.Invalid("Compressed payload has invalid reserved gzip flags")
        }
        var cursor = start + GZIP_FIXED_HEADER_SIZE

        fun requireAvailable(count: Int, part: String): String? =
            if (count < 0 || cursor > end - count) "Compressed payload has a truncated gzip $part" else null

        if (flags and GZIP_FLAG_EXTRA != 0) {
            requireAvailable(2, "extra-field length")?.let { return InflationResult.Invalid(it) }
            val extraLength = container.u16le(cursor)
            cursor += 2
            requireAvailable(extraLength, "extra field")?.let { return InflationResult.Invalid(it) }
            cursor += extraLength
        }
        if (flags and GZIP_FLAG_NAME != 0) {
            cursor = container.findGzipZeroTerminator(cursor, end)
                ?: return InflationResult.Invalid("Compressed payload has a truncated gzip file name")
        }
        if (flags and GZIP_FLAG_COMMENT != 0) {
            cursor = container.findGzipZeroTerminator(cursor, end)
                ?: return InflationResult.Invalid("Compressed payload has a truncated gzip comment")
        }
        if (flags and GZIP_FLAG_HEADER_CRC != 0) {
            requireAvailable(2, "header CRC")?.let { return InflationResult.Invalid(it) }
            val storedHeaderCrc = container.u16le(cursor)
            val computedHeaderCrc = container.crc32(start, cursor).toInt() and 0xffff
            if (storedHeaderCrc != computedHeaderCrc) {
                return InflationResult.Invalid("Compressed payload gzip header CRC mismatch")
            }
            cursor += 2
        }
        if (cursor > end - GZIP_TRAILER_SIZE) {
            return InflationResult.Invalid("Compressed payload has no complete gzip data and trailer")
        }

        val inflater = Inflater(true)
        val output = ByteArrayOutputStream(minOf(64 * 1024, MAX_INFLATED_IMAGE_BYTES))
        val inflatedCrc = CRC32()
        val buffer = ByteArray(8192)
        var inflatedSize = 0
        val deflateBytesRead: Long
        try {
            inflater.setInput(container, cursor, end - cursor)
            while (!inflater.finished()) {
                val count = inflater.inflate(buffer)
                if (count > 0) {
                    if (inflatedSize > MAX_INFLATED_IMAGE_BYTES - count) {
                        return InflationResult.Invalid(
                            "Compressed payload exceeds the app's $MAX_INFLATED_IMAGE_BYTES-byte inflated host-policy limit",
                        )
                    }
                    output.write(buffer, 0, count)
                    inflatedCrc.update(buffer, 0, count)
                    inflatedSize += count
                } else if (inflater.finished()) {
                    break
                } else if (inflater.needsDictionary()) {
                    return InflationResult.Invalid("Compressed payload gzip stream requires an unsupported dictionary")
                } else if (inflater.needsInput()) {
                    return InflationResult.Invalid("Compressed payload has a truncated gzip deflate stream")
                } else {
                    return InflationResult.Invalid("Compressed payload gzip inflater made no progress")
                }
            }
            deflateBytesRead = inflater.bytesRead
        } catch (_: DataFormatException) {
            return InflationResult.Invalid("Compressed payload contains invalid gzip deflate data")
        } finally {
            inflater.end()
        }

        val trailerStart = cursor + deflateBytesRead.toInt()
        if (trailerStart > end - GZIP_TRAILER_SIZE) {
            return InflationResult.Invalid("Compressed payload has a truncated gzip trailer")
        }
        val storedInflatedCrc = container.u32le(trailerStart)
        if (storedInflatedCrc != inflatedCrc.value) {
            return InflationResult.Invalid(
                "Compressed payload gzip CRC mismatch: stored ${storedInflatedCrc.hex8()}, computed ${inflatedCrc.value.hex8()}",
            )
        }
        val storedInflatedSize = container.u32le(trailerStart + 4)
        if (storedInflatedSize != inflatedSize.toLong()) {
            return InflationResult.Invalid(
                "Compressed payload gzip size mismatch: stored $storedInflatedSize, inflated $inflatedSize",
            )
        }

        val trailingStart = trailerStart + GZIP_TRAILER_SIZE
        val trailingCount = end - trailingStart
        if (trailingCount !in 0..3 ||
            (trailingStart until end).any { container[it] != 0.toByte() }
        ) {
            return InflationResult.Invalid(
                "Compressed payload has trailing data after its single gzip member",
            )
        }
        return InflationResult.Valid(output.toByteArray())
    }

    private fun validateNestedRawImage(raw: ByteArray, outerVersion: String): String? {
        if (raw.size < HEADER_SIZE + 4) {
            return "Nested raw image is shorter than the 512-byte header and payload"
        }
        if (raw.size % 4 != 0) return "Nested raw image length must be a multiple of 4 bytes"

        val declaredPayload = raw.u32le(4)
        if (declaredPayload > Int.MAX_VALUE.toLong()) return "Nested raw declared payload length is too large"
        val payloadLength = declaredPayload.toInt()
        if (payloadLength <= 0 || payloadLength != raw.size - HEADER_SIZE) {
            return "Nested raw declared payload length $payloadLength does not match ${raw.size - HEADER_SIZE} bytes"
        }
        if (payloadLength % 4 != 0) return "Nested raw payload length must be a multiple of 4 bytes"
        if (raw.u32le(12) != FirmwareImageFormat.BIN_RAW.containerType) {
            return "Nested firmware container must have raw type 1"
        }

        val storedPayloadCrc = raw.u32le(0)
        val computedPayloadCrc = raw.crc32(HEADER_SIZE, raw.size)
        if (storedPayloadCrc != computedPayloadCrc) {
            return "Nested raw payload CRC mismatch: stored ${storedPayloadCrc.hex8()}, computed ${computedPayloadCrc.hex8()}"
        }
        val storedHeaderCrc = raw.u32le(504)
        val computedHeaderCrc = raw.crc32(8, 504)
        if (storedHeaderCrc != computedHeaderCrc) {
            return "Nested raw header CRC mismatch: stored ${storedHeaderCrc.hex8()}, computed ${computedHeaderCrc.hex8()}"
        }

        val nestedVersion = raw.versionString()
        if (nestedVersion != outerVersion) {
            return "Nested raw version $nestedVersion does not match outer version $outerVersion"
        }
        return null
    }

    private fun ByteArray.findGzipZeroTerminator(from: Int, until: Int): Int? {
        var index = from
        while (index < until) {
            if (this[index] == 0.toByte()) return index + 1
            index++
        }
        return null
    }

    private fun ByteArray.versionString(): String = listOf(0x7c, 0x80, 0x84, 0x88)
        .joinToString(".") { u32le(it).toString() }
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

private fun ByteArray.u16le(offset: Int): Int =
    (this[offset].toInt() and 0xff) or ((this[offset + 1].toInt() and 0xff) shl 8)

private fun ByteArray.crc32(from: Int, until: Int): Long = CRC32().run {
    update(this@crc32, from, until - from)
    value
}

private fun Long.hex8(): String = "%08x".format(Locale.ROOT, this)
private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }
