package com.noop.ble

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.zip.CRC32
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
        val writing = FirmwareUpdateTransitions.writing(
            FirmwareUpdateTransitions.begin(selected, "WHOOP 5/MG"), 220,
        )
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
        assertEquals(5, writes.size)
        assertEquals(220, writes.first().second[5].toInt() and 0xff)
        assertEquals(72, writes.last().second[5].toInt() and 0xff)
        assertEquals(880, writes.last().second.u32(1).toInt())
    }

    @Test
    fun `verify ignores an interim response and propagates final remote failure`() = runBlocking {
        val selected = parsedImage(type = 5)
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, accept ->
            val candidates = if (command == FirmwareTransferEngine.VERIFY_COMMAND) {
                listOf(
                    FirmwareWireResponse(command, 0, 1, byteArrayOf(0)),
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
    fun `exclusive session acquisition refuses queued or in flight unrelated writes`() {
        assertEquals(null, FirmwareUpdateAdmission.busyReason(false, false, false, 0))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, false, 1)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, true, false, 0)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(false, false, true, 0)!!.contains("Bluetooth is busy"))
        assertTrue(FirmwareUpdateAdmission.busyReason(true, false, false, 0)!!.contains("Bluetooth is busy"))
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
    }

    @Test
    fun `transport cancellation stops plan before verify and cannot unlock activation`() = runBlocking {
        val selected = parsedImage(type = 5)
        var calls = 0
        var last = FirmwareUpdateTransitions.begin(
            FirmwareUpdateTransitions.selected(selected.info, true), "WHOOP 5/MG",
        )
        val engine = FirmwareTransferEngine(FirmwareTransferTransport { command, _, _, _ ->
            calls++
            if (calls == 3) throw CancellationException("disconnect")
            FirmwareWireResponse(command, 0, 1, byteArrayOf(1, 0))
        })
        val failure = runCatching { engine.transfer(selected, last) { last = it } }.exceptionOrNull()
        assertTrue(failure is CancellationException)
        assertFalse(last.canActivate)
        assertTrue(calls < 7)
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
        assertTrue(reconnecting.status.contains("no transfer will be resumed"))
        val reconnected = FirmwareUpdateTransitions.reconnected(reconnecting, "50.42.1.0")
        assertEquals(FirmwareUpdateStage.DEVICE_RECONNECTED, reconnected.stage)
        assertTrue(reconnected.status.contains("does not prove"))
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

    private fun image(type: Int, extensionMarkerAt8: Int = 5): ByteArray {
        val payload = ByteArray(440) { ((it * 17) and 0xff).toByte() }
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
