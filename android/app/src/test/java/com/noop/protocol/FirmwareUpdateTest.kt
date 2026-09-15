package com.noop.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.CRC32
import java.util.zip.GZIPOutputStream
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking

class FirmwareUpdateTest {
    @Test
    fun `type is read from offset 12 and original zbin shape validates`() {
        val bytes = image(type = 5, extensionMarkerAt8 = 5)
        val result = FirmwareImageParser.parse("original.zbin", bytes)
        assertTrue(result is FirmwareImageValidation.Valid)
        assertEquals(
            FirmwareImageFormat.ZBIN_COMPRESSED,
            (result as FirmwareImageValidation.Valid).image.info.format,
        )
    }

    @Test
    fun `type selector at offset 12 accepts raw bin while unresolved word 8 remains five`() {
        val result = FirmwareImageParser.parse("research.bin", image(type = 1))
        assertTrue(result is FirmwareImageValidation.Valid)
        val info = (result as FirmwareImageValidation.Valid).image.info
        assertEquals(FirmwareImageFormat.BIN_RAW, info.format)
        assertTrue(info.compatibilityNote.contains("not established"))
    }

    @Test
    fun `payload corruption fails closed`() {
        val bytes = image(type = 5)
        bytes[FirmwareImageParser.HEADER_SIZE + 3] = (bytes[FirmwareImageParser.HEADER_SIZE + 3].toInt() xor 1).toByte()
        val result = FirmwareImageParser.parse("bad.zbin", bytes)
        assertTrue(result is FirmwareImageValidation.Invalid)
        assertTrue((result as FirmwareImageValidation.Invalid).reason.contains("Payload CRC mismatch"))
    }

    @Test
    fun `header corruption fails closed`() {
        val bytes = image(type = 5)
        bytes[32] = (bytes[32].toInt() xor 1).toByte()
        val result = FirmwareImageParser.parse("bad.zbin", bytes)
        assertTrue(result is FirmwareImageValidation.Invalid)
        assertTrue((result as FirmwareImageValidation.Invalid).reason.contains("Header CRC mismatch"))
    }

    @Test
    fun `declared length and actual bytes must agree`() {
        val bytes = image(type = 5)
        bytes.putU32(4, 4)
        val result = FirmwareImageParser.parse("bad.zbin", bytes)
        assertTrue(result is FirmwareImageValidation.Invalid)
        assertTrue((result as FirmwareImageValidation.Invalid).reason.contains("Declared payload length"))
    }

    @Test
    fun `unsupported type and misleading extension are rejected`() {
        assertTrue(FirmwareImageParser.parse("alternate.zbin", image(type = 3)) is FirmwareImageValidation.Invalid)
        assertTrue(FirmwareImageParser.parse("compressed.bin", image(type = 5)) is FirmwareImageValidation.Invalid)
    }

    @Test
    fun `activation is gated by remote verification for the same state`() {
        val info = validInfo()
        val selected = FirmwareUpdateTransitions.selected(info, eligible = true)
        assertTrue(selected.canStart)
        assertFalse(selected.canActivate)
        val beginning = FirmwareUpdateTransitions.begin(selected, "WHOOP 5/MG · 50.42.1.0 · AA:BB")
        assertTrue(beginning.lockedDeviceLabel.orEmpty().contains("AA:BB"))
        val writing = FirmwareUpdateTransitions.writing(beginning, 220)
        assertFalse(writing.canActivate)
        val validating = FirmwareUpdateTransitions.remoteValidating(writing)
        assertFalse(validating.canActivate)
        val ready = FirmwareUpdateTransitions.ready(validating)
        assertTrue(ready.canActivate)
        assertFalse(FirmwareUpdateTransitions.failed(ready, "disconnect").canActivate)
        assertFalse(FirmwareUpdateTransitions.cancelled(ready).canActivate)
    }

    @Test
    fun `engine sends complete image in 220 byte chunks including final slice`() = runBlocking {
        val selected = parsedImage(type = 5)
        val calls = mutableListOf<Pair<Int, ByteArray>>()
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, payload, _, accept ->
            calls += command to payload.copyOf()
            val response = when (command) {
                FirmwareTransferEngine.VERIFY_COMMAND -> FirmwareWireResponse(command, 0, 1, byteArrayOf(1))
                else -> FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 0))
            }
            check(accept(response))
            response
        })
        val initial = FirmwareUpdateTransitions.begin(
            FirmwareUpdateTransitions.selected(selected.info, true), "WHOOP 5/MG",
        )
        val end = engine.transfer(selected, initial) {}
        assertEquals(FirmwareUpdateStage.READY_TO_ACTIVATE, end.stage)
        val writes = calls.filter { it.first == FirmwareTransferEngine.WRITE_COMMAND }
        val expectedWrites = (selected.bytes.size + FirmwareImageParser.CHUNK_SIZE - 1) /
            FirmwareImageParser.CHUNK_SIZE
        assertEquals(expectedWrites, writes.size)
        assertEquals(220, writes.first().second[5].toInt() and 0xff)
        val expectedLastOffset = (expectedWrites - 1) * FirmwareImageParser.CHUNK_SIZE
        assertEquals(selected.bytes.size - expectedLastOffset, writes.last().second[5].toInt() and 0xff)
        assertEquals(expectedLastOffset.toLong(), writes.last().second.u32(1))
    }

    @Test
    fun `fresh transfer quiesces strap in official order before opening slot`() = runBlocking {
        val selected = parsedImage(type = 5)
        val calls = mutableListOf<Pair<Int, ByteArray>>()
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, payload, _, accept ->
            calls += command to payload.copyOf()
            acceptedResponse(command).also { check(accept(it)) }
        })

        engine.transfer(selected, begun(selected)) {}

        assertEquals(
            listOf(
                FirmwareTransferEngine.STOP_REALTIME_HR_COMMAND,
                FirmwareTransferEngine.STOP_IMU_COMMAND,
                FirmwareTransferEngine.ABORT_HISTORY_COMMAND,
                FirmwareTransferEngine.PREPARE_COMMAND,
            ),
            calls.take(4).map { it.first },
        )
        assertTrue(calls[0].second.contentEquals(byteArrayOf(0)))
        assertTrue(calls[1].second.contentEquals(byteArrayOf(1, 0)))
        assertTrue(calls[2].second.isEmpty())
        assertTrue(calls[3].second.contentEquals(byteArrayOf(1)))
    }

    @Test
    fun `quiesce rejection stops before prepare and preserves raw device detail`() = runBlocking {
        val selected = parsedImage(type = 5)
        val calls = mutableListOf<Int>()
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            calls += command
            val response = if (command == FirmwareTransferEngine.STOP_IMU_COMMAND) {
                FirmwareWireResponse(command, 0, 0x7f, byteArrayOf(1, 0x2a))
            } else acceptedResponse(command)
            check(accept(response))
            response
        })

        val failure = runCatching { engine.transfer(selected, begun(selected)) {} }.exceptionOrNull()

        assertTrue(failure is FirmwareTransferException)
        assertTrue(failure?.message.orEmpty().contains("result=127"))
        assertTrue(failure?.message.orEmpty().contains("body=012a"))
        assertFalse(calls.contains(FirmwareTransferEngine.PREPARE_COMMAND))
    }

    @Test
    fun `lost chunk acknowledgement retries identical offset and counts progress once`() = runBlocking {
        val selected = parsedImage(type = 5)
        val writePayloads = mutableListOf<ByteArray>()
        val progress = mutableListOf<Int>()
        var firstWrite = true
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, payload, _, accept ->
            if (command == FirmwareTransferEngine.WRITE_COMMAND) {
                writePayloads += payload.copyOf()
                if (firstWrite) {
                    firstWrite = false
                    throw FirmwareRetryableTransportException("response timeout")
                }
            }
            acceptedResponse(command).also { check(accept(it)) }
        })

        val end = engine.transfer(selected, begun(selected)) { progress += it.bytesAcknowledged }

        assertEquals(FirmwareUpdateStage.READY_TO_ACTIVATE, end.stage)
        assertEquals(2, writePayloads.count { it.u32(1) == 0L })
        assertTrue(writePayloads[0].contentEquals(writePayloads[1]))
        assertEquals(1, progress.count { it == 220 })
    }

    @Test
    fun `retry exhaustion pauses at last acknowledged offset after seven attempts`() = runBlocking {
        val selected = parsedImage(type = 5)
        var writes = 0
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            if (command == FirmwareTransferEngine.WRITE_COMMAND) {
                writes++
                throw FirmwareRetryableTransportException("response timeout")
            }
            acceptedResponse(command).also { check(accept(it)) }
        })

        val failure = runCatching { engine.transfer(selected, begun(selected)) {} }.exceptionOrNull()

        assertTrue(failure is FirmwareTransferPausedException)
        assertEquals(0, (failure as FirmwareTransferPausedException).acknowledgedOffset)
        assertEquals(FirmwareTransferEngine.MAX_CHUNK_ATTEMPTS, failure.attempts)
        assertEquals(7, writes)
    }

    @Test
    fun `resume on same slot skips prepare keeps acknowledged progress and never activates`() = runBlocking {
        val selected = parsedImage(type = 5)
        val calls = mutableListOf<Pair<Int, ByteArray>>()
        val progress = mutableListOf<Int>()
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, payload, _, accept ->
            calls += command to payload.copyOf()
            acceptedResponse(command).also { check(accept(it)) }
        })
        val paused = FirmwareUpdateTransitions.paused(
            FirmwareUpdateTransitions.writing(begun(selected), 440),
            acknowledged = 440,
            reason = "response timeout",
        )

        val end = engine.transfer(
            image = selected,
            initial = FirmwareUpdateTransitions.resuming(paused),
            startOffset = 440,
            prepareSlot = false,
            publish = { progress += it.bytesAcknowledged },
        )

        assertEquals(FirmwareUpdateStage.READY_TO_ACTIVATE, end.stage)
        assertFalse(calls.any { it.first == FirmwareTransferEngine.PREPARE_COMMAND })
        assertFalse(calls.any { it.first == FirmwareTransferEngine.ACTIVATE_COMMAND })
        assertEquals(440L, calls.first { it.first == FirmwareTransferEngine.WRITE_COMMAND }.second.u32(1))
        assertTrue(progress.none { it < 440 })
    }

    @Test
    fun `explicit write rejection is not retried and reports known or numeric detail`() = runBlocking {
        val selected = parsedImage(type = 5)
        var writes = 0
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            val response = if (command == FirmwareTransferEngine.WRITE_COMMAND) {
                writes++
                FirmwareWireResponse(command, 0, 0, byteArrayOf(1, 11))
            } else acceptedResponse(command)
            check(accept(response))
            response
        })

        val failure = runCatching { engine.transfer(selected, begun(selected)) {} }.exceptionOrNull()

        assertEquals(1, writes)
        assertTrue(failure is FirmwareTransferException)
        assertTrue(failure?.message.orEmpty().contains("flash/write state (11)"))
        assertTrue(failure?.message.orEmpty().contains("result=0"))
    }

    @Test
    fun `resume binding rejects stale session device connection image and invalid offset`() {
        val valid = FirmwareResumeBinding(
            pendingSessionId = 9,
            currentSessionId = 9,
            pendingDeviceAddress = "AA:BB",
            currentDeviceAddress = "aa:bb",
            pendingConnectionGeneration = 12,
            currentConnectionGeneration = 12,
            pendingImageSha256 = "abcd",
            currentImageSha256 = "abcd",
            acknowledgedOffset = 440,
            totalBytes = 952,
        )
        assertEquals(null, FirmwareResumePolicy.rejectionReason(valid))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(currentSessionId = 10))!!.contains("session"))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(currentDeviceAddress = "CC:DD"))!!.contains("strap"))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(currentConnectionGeneration = 13))!!.contains("connection"))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(currentImageSha256 = "ef01"))!!.contains("image"))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(acknowledgedOffset = 953))!!.contains("offset"))
        assertTrue(FirmwareResumePolicy.rejectionReason(valid.copy(acknowledgedOffset = 221))!!.contains("boundary"))
    }

    @Test
    fun `transfer entry point rejects prepare with offset and resume between acknowledged boundaries`() = runBlocking {
        val selected = parsedImage(type = 5)
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, _ ->
            acceptedResponse(command)
        })

        assertTrue(
            runCatching {
                engine.transfer(selected, begun(selected), startOffset = 220, prepareSlot = true) {}
            }.exceptionOrNull() is IllegalArgumentException,
        )
        assertTrue(
            runCatching {
                engine.transfer(selected, begun(selected), startOffset = 221, prepareSlot = false) {}
            }.exceptionOrNull() is IllegalArgumentException,
        )
    }

    @Test
    fun `verify ignores an optional pending response and propagates final remote failure`() = runBlocking {
        val selected = parsedImage(type = 5)
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            val candidates = if (command == FirmwareTransferEngine.VERIFY_COMMAND) {
                listOf(
                    FirmwareWireResponse(command, 0, 2, byteArrayOf(1)),
                    FirmwareWireResponse(command, 0, 0, byteArrayOf(1)),
                )
            } else listOf(FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 0)))
            candidates.first(accept)
        })
        val initial = FirmwareUpdateTransitions.begin(
            FirmwareUpdateTransitions.selected(selected.info, true), "WHOOP 5/MG",
        )
        val failure = runCatching { engine.transfer(selected, initial) {} }.exceptionOrNull()
        assertTrue(failure is FirmwareTransferException)
        assertTrue(failure?.message.orEmpty().contains("remote image validation failed"))
    }

    @Test
    fun `correlation rejects stale session command and sequence even across wrap`() {
        val base = FirmwareResponseKey(
            pendingSessionId = 9,
            currentSessionId = 9,
            expectedCommand = 143,
            expectedSequence = 0,
            actualCommand = 143,
            actualSequence = 0,
            sameDevice = true,
        )
        assertTrue(FirmwareResponseMatcher.correlated(base))
        assertFalse(FirmwareResponseMatcher.correlated(base.copy(pendingSessionId = 8)))
        assertFalse(FirmwareResponseMatcher.correlated(base.copy(actualSequence = 255)))
        assertFalse(FirmwareResponseMatcher.correlated(base.copy(actualCommand = 142)))
        assertFalse(FirmwareResponseMatcher.correlated(base.copy(sameDevice = false)))
    }

    @Test
    fun `whoop5 decoder accepts crc valid firmware responses and removes wire padding`() {
        val cases = listOf(
            Triple(FirmwareTransferEngine.STOP_REALTIME_HR_COMMAND, byteArrayOf(), 1),
            Triple(FirmwareTransferEngine.ABORT_HISTORY_COMMAND, byteArrayOf(), 1),
            Triple(FirmwareTransferEngine.STOP_IMU_COMMAND, byteArrayOf(1, 0), 1),
            Triple(FirmwareTransferEngine.PREPARE_COMMAND, byteArrayOf(1, 0), 1),
            Triple(FirmwareTransferEngine.WRITE_COMMAND, byteArrayOf(1, 0), 1),
            Triple(FirmwareTransferEngine.VERIFY_COMMAND, byteArrayOf(1), 1),
            Triple(FirmwareTransferEngine.ACTIVATE_COMMAND, byteArrayOf(1, 1), 1),
        )
        for ((command, body, result) in cases) {
            val decoded = FirmwareWhoop5ResponseDecoder.decode(
                whoop5ResponseFrame(command, originSequence = 0xa5, result = result, body = body),
            )
            assertTrue("command $command should decode", decoded != null)
            assertEquals(command, decoded!!.command)
            assertEquals(0xa5, decoded.originSequence)
            assertEquals(result, decoded.result)
            assertTrue(body.contentEquals(decoded.body))
        }
        val puffinAlias = FirmwareWhoop5ResponseDecoder.decode(
            whoop5ResponseFrame(144, 0x33, 1, byteArrayOf(1, 1), type = 0x26),
        )
        assertEquals(FirmwareTransferEngine.ACTIVATE_COMMAND, puffinAlias?.command)

        val pending = FirmwareWhoop5ResponseDecoder.decode(
            whoop5ResponseFrame(83, 0x44, 2, byteArrayOf(1)),
        )!!
        assertFalse(FirmwareResponseMatcher.isFinal(FirmwareTransferEngine.VERIFY_COMMAND, pending))
    }

    @Test
    fun `whoop5 decoder rejects malformed length truncation crc and nonresponse frames`() {
        val valid = whoop5ResponseFrame(142, originSequence = 7, result = 1, body = byteArrayOf(1, 0))
        assertEquals(null, FirmwareWhoop5ResponseDecoder.decode(valid.copyOf(valid.size - 1)))
        assertEquals(null, FirmwareWhoop5ResponseDecoder.decode(valid + byteArrayOf(0)))
        assertEquals(null, FirmwareWhoop5ResponseDecoder.decode(valid.copyOf().also { it[2]++ }))
        assertEquals(null, FirmwareWhoop5ResponseDecoder.decode(valid.copyOf().also { it[13] = 2 }))
        assertEquals(
            null,
            FirmwareWhoop5ResponseDecoder.decode(
                whoop5ResponseFrame(142, 7, 1, byteArrayOf(1, 0), type = 0x23),
            ),
        )
    }

    @Test
    fun `exclusive session acquisition refuses queued or in flight unrelated writes`() {
        assertEquals(null, FirmwareUpdateAdmission.busyReason(false, false, false, 0))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, false, 1)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, true, false, 0)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, true, 0)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(true, false, false, 0)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, false, 0, 23)!!.contains("MTU"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, false, 0, 247, cccdInFlight = true)!!.contains("notification"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, false, 0, 247, queuedCccds = 1)!!.contains("notification"))
    }

    @Test
    fun `firmware queue drops stale session writes and never retries ambiguous rejection`() {
        assertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(null, null))
        assertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(null, 12))
        assertTrue(FirmwareWriteQueuePolicy.belongsToCurrentSession(12, 12))
        assertFalse(FirmwareWriteQueuePolicy.belongsToCurrentSession(11, 12))
        assertFalse(FirmwareWriteQueuePolicy.belongsToCurrentSession(12, null))
        assertTrue(FirmwareWriteQueuePolicy.mayRetryAfterAmbiguousRejection(null))
        assertFalse(FirmwareWriteQueuePolicy.mayRetryAfterAmbiguousRejection(12))
    }

    @Test
    fun `activation observation is bounded and stale timer cannot terminate a new session`() {
        assertEquals(30_000L, FirmwareActivationObservation.DISCONNECT_TIMEOUT_MS)
        assertEquals(60_000L, FirmwareActivationObservation.RECONNECT_TIMEOUT_MS)
        assertTrue(FirmwareActivationObservation.sessionIsCurrent(12, 12))
        assertFalse(FirmwareActivationObservation.sessionIsCurrent(12, 13))
        assertFalse(FirmwareActivationObservation.sessionIsCurrent(12, null))
        assertFalse(FirmwareActivationObservation.canAcceptReportedVersion(FirmwareUpdateStage.ACTIVATION_REQUESTED))
        assertTrue(FirmwareActivationObservation.canAcceptReportedVersion(FirmwareUpdateStage.RECONNECTING))
    }

    @Test
    fun `transport cancellation stops plan before verify and cannot unlock activation`() = runBlocking {
        val selected = parsedImage(type = 5)
        var calls = 0
        var last = FirmwareUpdateTransitions.begin(
            FirmwareUpdateTransitions.selected(selected.info, true), "WHOOP 5/MG",
        )
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            calls++
            if (command == FirmwareTransferEngine.WRITE_COMMAND) throw CancellationException("disconnect")
            acceptedResponse(command).also { check(accept(it)) }
        })
        val failure = runCatching { engine.transfer(selected, last) { last = it } }.exceptionOrNull()
        assertTrue(failure is CancellationException)
        assertFalse(last.canActivate)
        assertEquals(1, calls.let { total -> total - 4 })
    }

    @Test
    fun `activation accepts only explicit accepted reset response`() = runBlocking {
        val rejected = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, _ ->
            FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 0))
        })
        assertTrue(runCatching { rejected.activate() }.exceptionOrNull() is FirmwareTransferException)

        val accepted = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, _ ->
            FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 1))
        })
        assertEquals(1, accepted.activate().result)
    }

    @Test
    fun `disconnect after activation remains unknown until same device reports again`() {
        val ready = FirmwareUpdateTransitions.ready(
            FirmwareUpdateTransitions.remoteValidating(
                FirmwareUpdateTransitions.begin(FirmwareUpdateTransitions.selected(validInfo(), true), "WHOOP 5/MG"),
            ),
        )
        val sent = FirmwareUpdateTransitions.activationRequested(ready)
        val reconnecting = FirmwareUpdateTransitions.reconnecting(sent)
        assertEquals(FirmwareUpdateStage.RECONNECTING, reconnecting.stage)
        assertTrue(reconnecting.status.contains("Waiting for it to reconnect"))
        val reconnected = FirmwareUpdateTransitions.reconnected(reconnecting, "50.42.1.0")
        assertEquals(FirmwareUpdateStage.DEVICE_RECONNECTED, reconnected.stage)
        assertTrue(reconnected.status.contains("reconnected"))
    }

    @Test
    fun `retained original fixture validates when supplied externally`() {
        val path = System.getenv("NOOP_FIRMWARE_FIXTURE")
        assumeTrue(!path.isNullOrBlank())
        val file = File(path!!)
        val result = FirmwareImageParser.parse(file.name, file.readBytes())
        assertTrue(result is FirmwareImageValidation.Valid)
        assertEquals(FirmwareImageFormat.ZBIN_COMPRESSED, (result as FirmwareImageValidation.Valid).image.info.format)
    }

    @Test
    fun `retained raw fixture validates separately when supplied externally`() {
        val path = System.getenv("NOOP_RAW_FIRMWARE_FIXTURE")
        assumeTrue(!path.isNullOrBlank())
        val file = File(path!!)
        val result = FirmwareImageParser.parse(file.name, file.readBytes())
        assertTrue(result is FirmwareImageValidation.Valid)
        assertEquals(FirmwareImageFormat.BIN_RAW, (result as FirmwareImageValidation.Valid).image.info.format)
    }

    private fun validInfo() = FirmwareImageInfo(
        fileName = "original.zbin",
        byteCount = 1024,
        format = FirmwareImageFormat.ZBIN_COMPRESSED,
        version = "50.42.1.0",
        payloadLength = 512,
        payloadCrc32 = "00000000",
        headerCrc32 = "00000000",
        sha256 = "00",
        compatibilityNote = "test",
    )

    private fun parsedImage(type: Int): ValidatedFirmwareImage {
        val extension = if (type == 5) "zbin" else "bin"
        return (FirmwareImageParser.parse("fixture.$extension", image(type)) as FirmwareImageValidation.Valid).image
    }

    private fun begun(image: ValidatedFirmwareImage): FirmwareUpdateState = FirmwareUpdateTransitions.begin(
        FirmwareUpdateTransitions.selected(image.info, true), "WHOOP 5/MG",
    )

    private fun acceptedResponse(command: Int): FirmwareWireResponse = when (command) {
        FirmwareTransferEngine.VERIFY_COMMAND -> FirmwareWireResponse(command, 0, 1, byteArrayOf(1))
        FirmwareTransferEngine.ACTIVATE_COMMAND -> FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 1))
        else -> FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 0))
    }

    private fun image(type: Int, extensionMarkerAt8: Int = 5): ByteArray {
        val payload = if (type == FirmwareImageFormat.ZBIN_COMPRESSED.containerType.toInt()) {
            val compressed = ByteArrayOutputStream().use { output ->
                GZIPOutputStream(output).use { it.write(image(FirmwareImageFormat.BIN_RAW.containerType.toInt())) }
                output.toByteArray()
            }
            compressed + ByteArray((4 - compressed.size % 4) % 4)
        } else {
            ByteArray(440) { ((it * 17) and 0xff).toByte() }
        }
        val bytes = ByteArray(FirmwareImageParser.HEADER_SIZE + payload.size)
        payload.copyInto(bytes, FirmwareImageParser.HEADER_SIZE)
        bytes.putU32(4, payload.size)
        bytes.putU32(8, extensionMarkerAt8)
        bytes.putU32(12, type)
        bytes.putU32(0x7c, 50)
        bytes.putU32(0x80, 42)
        bytes.putU32(0x84, 1)
        bytes.putU32(0x88, 0)
        bytes.putU32(0, crc(bytes, FirmwareImageParser.HEADER_SIZE, bytes.size))
        bytes.putU32(504, crc(bytes, 8, 504))
        bytes.putU32(508, bytes.u32(0))
        return bytes
    }

    private fun whoop5ResponseFrame(
        command: Int,
        originSequence: Int,
        result: Int,
        body: ByteArray,
        type: Int = 0x24,
    ): ByteArray {
        val inner0 = byteArrayOf(
            type.toByte(), 0x51, command.toByte(), originSequence.toByte(), result.toByte(),
        ) + body
        val inner = inner0 + ByteArray((4 - inner0.size % 4) % 4)
        val declaredLength = inner.size + 4
        val frame = ByteArray(declaredLength + 8)
        frame[0] = 0xaa.toByte()
        frame[1] = 1
        frame[2] = declaredLength.toByte()
        frame[3] = (declaredLength ushr 8).toByte()
        frame[4] = 0
        frame[5] = 1
        val headerCrc = crc16Modbus(frame, 0, 6)
        frame[6] = headerCrc.toByte()
        frame[7] = (headerCrc ushr 8).toByte()
        inner.copyInto(frame, 8)
        frame.putU32(frame.size - 4, crc(frame, 8, frame.size - 4))
        return frame
    }

    private fun crc16Modbus(bytes: ByteArray, from: Int, until: Int): Int {
        var value = 0xffff
        for (index in from until until) {
            value = value xor (bytes[index].toInt() and 0xff)
            repeat(8) {
                value = if ((value and 1) == 1) (value ushr 1) xor 0xa001 else value ushr 1
            }
        }
        return value and 0xffff
    }

    private fun crc(bytes: ByteArray, from: Int, until: Int): Long = CRC32().run {
        update(bytes, from, until - from)
        value
    }

    private fun ByteArray.putU32(offset: Int, value: Int) = putU32(offset, value.toLong())
    private fun ByteArray.putU32(offset: Int, value: Long) {
        this[offset] = value.toByte()
        this[offset + 1] = (value ushr 8).toByte()
        this[offset + 2] = (value ushr 16).toByte()
        this[offset + 3] = (value ushr 24).toByte()
    }
    private fun ByteArray.u32(offset: Int): Long =
        (this[offset].toLong() and 0xff) or ((this[offset + 1].toLong() and 0xff) shl 8) or
            ((this[offset + 2].toLong() and 0xff) shl 16) or ((this[offset + 3].toLong() and 0xff) shl 24)
}
