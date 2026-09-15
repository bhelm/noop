package com.noop.protocol

import kotlinx.coroutines.delay

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
            FirmwareTransferEngine.VERIFY -> 1
            FirmwareTransferEngine.STOP_REALTIME_HR,
            FirmwareTransferEngine.ABORT_HISTORY -> 0
            FirmwareTransferEngine.STOP_IMU,
            FirmwareTransferEngine.PREPARE,
            FirmwareTransferEngine.WRITE,
            FirmwareTransferEngine.ACTIVATE -> 2
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
        command != FirmwareTransferEngine.VERIFY ||
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

internal data class FirmwareResumeBinding(
    val pendingSessionId: Int,
    val currentSessionId: Int,
    val pendingDeviceAddress: String,
    val currentDeviceAddress: String,
    val pendingConnectionGeneration: Int,
    val currentConnectionGeneration: Int,
    val pendingImageSha256: String,
    val currentImageSha256: String,
    val acknowledgedOffset: Int,
    val totalBytes: Int,
)

/** Resume is deliberately local to one uninterrupted BLE connection and one immutable image. */
internal object FirmwareResumePolicy {
    fun rejectionReason(binding: FirmwareResumeBinding): String? = when {
        binding.pendingSessionId != binding.currentSessionId ->
            "The paused firmware session is no longer current"
        !binding.pendingDeviceAddress.equals(binding.currentDeviceAddress, ignoreCase = true) ->
            "The connected strap no longer matches the paused firmware session"
        binding.pendingConnectionGeneration != binding.currentConnectionGeneration ->
            "The Bluetooth connection changed; restart the firmware transfer from the beginning"
        !binding.pendingImageSha256.equals(binding.currentImageSha256, ignoreCase = true) ->
            "The selected image changed; restart the firmware transfer from the beginning"
        binding.acknowledgedOffset !in 0..binding.totalBytes ->
            "The saved firmware offset is outside the selected image"
        binding.acknowledgedOffset != binding.totalBytes &&
            binding.acknowledgedOffset % FirmwareImageParser.CHUNK_SIZE != 0 ->
            "The saved firmware offset is not an acknowledged chunk boundary"
        else -> null
    }
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
 * deterministic fake. Only ambiguous transport failures for a data chunk are retried. A rejected command
 * is permanent, and exhausting the bounded retry budget pauses at the last acknowledged offset.
 */
internal class FirmwareTransferEngine(private val transport: FirmwareTransferTransport) {
    companion object {
        const val PREPARE = 142
        const val WRITE = 143
        const val ACTIVATE = 144
        const val VERIFY = 83
        const val STOP_REALTIME_HR = 3
        const val STOP_IMU = 106
        const val ABORT_HISTORY = 20
        const val MAX_CHUNK_ATTEMPTS = 7
        const val PRE_TRANSFER_DELAY_MS = 500L
        const val COMMAND_TIMEOUT_MS = 8_000L
        const val VERIFY_TIMEOUT_MS = 30_000L
    }

    suspend fun transfer(
        image: ValidatedFirmwareImage,
        initial: FirmwareUpdateState,
        startOffset: Int = 0,
        prepareSlot: Boolean = true,
        publish: (FirmwareUpdateState) -> Unit,
    ): FirmwareUpdateState {
        require(startOffset in 0..image.bytes.size) { "Invalid firmware start offset $startOffset" }
        require(!prepareSlot || startOffset == 0) { "A newly prepared slot must start at offset 0" }
        require(startOffset == image.bytes.size || startOffset % FirmwareImageParser.CHUNK_SIZE == 0) {
            "Firmware resume offset $startOffset is not an acknowledged chunk boundary"
        }
        delay(PRE_TRANSFER_DELAY_MS)
        quiesceStrap()
        if (prepareSlot) {
            requireAccepted(
                exchange(PREPARE, byteArrayOf(1), COMMAND_TIMEOUT_MS),
                expectedTail = 0,
                step = "prepare",
            )
        }
        var state = initial
        var offset = startOffset
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
            var attempts = 0
            while (true) {
                attempts += 1
                try {
                    requireAccepted(
                        exchange(WRITE, payload, COMMAND_TIMEOUT_MS),
                        expectedTail = 0,
                        step = "write at offset $offset",
                    )
                    break
                } catch (retryable: FirmwareRetryableTransportException) {
                    if (attempts >= MAX_CHUNK_ATTEMPTS) {
                        throw FirmwareTransferPausedException(
                            acknowledgedOffset = offset,
                            attempts = attempts,
                            message = "Firmware transfer paused at offset $offset after $attempts attempts: " +
                                (retryable.message ?: "transport response unavailable"),
                        )
                    }
                    state = FirmwareUpdateTransitions.retrying(
                        state,
                        acknowledged = offset,
                        attempt = attempts + 1,
                        maximum = MAX_CHUNK_ATTEMPTS,
                    )
                    publish(state)
                }
            }
            offset += count
            state = FirmwareUpdateTransitions.writing(state, offset)
            publish(state)
        }
        state = FirmwareUpdateTransitions.remoteValidating(state)
        publish(state)
        val verified = exchange(VERIFY, byteArrayOf(1), VERIFY_TIMEOUT_MS)
        if (verified.result != 1 || verified.body.firstOrNull()?.toInt() != 1) {
            throw FirmwareTransferException(rejectionMessage("firmware remote image validation failed", verified))
        }
        return FirmwareUpdateTransitions.ready(state).also(publish)
    }

    suspend fun activate(): FirmwareWireResponse {
        val response = exchange(ACTIVATE, byteArrayOf(1), COMMAND_TIMEOUT_MS)
        if (response.result != 1 || response.body.size < 2 ||
            response.body[0].toInt() != 1 || response.body[1].toInt() != 1
        ) throw FirmwareTransferException(rejectionMessage("firmware activation/reset", response))
        return response
    }

    private suspend fun exchange(command: Int, payload: ByteArray, timeoutMs: Long): FirmwareWireResponse =
        transport.exchange(command, payload, timeoutMs) { FirmwareResponseMatcher.isFinal(command, it) }

    private suspend fun quiesceStrap() {
        listOf(
            Triple(STOP_REALTIME_HR, byteArrayOf(0), "stop realtime HR"),
            Triple(STOP_IMU, byteArrayOf(1, 0), "stop IMU streaming"),
            Triple(ABORT_HISTORY, byteArrayOf(), "abort history transfer"),
        ).forEach { (command, payload, step) ->
            val response = exchange(command, payload, COMMAND_TIMEOUT_MS)
            if (response.result != 1) {
                throw FirmwareTransferException(rejectionMessage(step, response))
            }
        }
    }

    private fun requireAccepted(response: FirmwareWireResponse, expectedTail: Int, step: String) {
        if (response.result != 1 || response.body.size < 2 ||
            response.body[0].toInt() != 1 || response.body[1].toInt() != expectedTail
        ) throw FirmwareTransferException(rejectionMessage("firmware $step", response))
    }

    private fun rejectionMessage(step: String, response: FirmwareWireResponse): String {
        val detail = response.body.getOrNull(1)?.toInt()?.and(0xff)
        val detailText = when {
            response.command == PREPARE && detail == 10 -> "prepare state (10)"
            response.command == WRITE && detail == 3 -> "invalid slot (3)"
            response.command == WRITE && detail == 4 -> "range/overflow (4)"
            response.command == WRITE && detail == 11 -> "flash/write state (11)"
            detail != null -> "detail=$detail"
            else -> "detail unavailable"
        }
        return "The strap rejected $step: result=${response.result}, $detailText, body=${response.body.toHex()}"
    }
}

internal class FirmwareTransferException(message: String) : Exception(message)
internal class FirmwareRetryableTransportException(message: String, cause: Throwable? = null) : Exception(message, cause)
internal class FirmwareTransferPausedException(
    val acknowledgedOffset: Int,
    val attempts: Int,
    message: String,
) : Exception(message)
