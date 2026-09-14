package com.noop.ble

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.CRC32
import java.util.zip.GZIPOutputStream

class FirmwareImageStructureTest {
    @Test
    fun `constructed zbin contains one valid raw image and preserves selected bytes`() {
        val selected = zbin(rawImage())

        val result = FirmwareImageParser.parse("constructed.zbin", selected)

        assertTrue(result is FirmwareImageValidation.Valid)
        assertTrue((result as FirmwareImageValidation.Valid).image.bytes.contentEquals(selected))
    }

    @Test
    fun `gzip may finish with an empty final block after an exact inflate buffer`() {
        val raw = rawImage(payloadSize = 8192 - FirmwareImageParser.HEADER_SIZE)
        val selected = outerContainer(gzipWithEmptyFinalStoredBlock(raw))

        val result = FirmwareImageParser.parse("exact-buffer.zbin", selected)

        assertTrue(
            (result as? FirmwareImageValidation.Invalid)?.reason,
            result is FirmwareImageValidation.Valid,
        )
    }

    @Test
    fun `damaged gzip trailer fails after outer payload crc is repaired`() {
        val bytes = zbin(rawImage())
        val gzipTrailerCrcByte = bytes.size - trailingZeroCount(bytes) - 8
        bytes[gzipTrailerCrcByte] = (bytes[gzipTrailerCrcByte].toInt() xor 1).toByte()
        repairOuterPayloadCrc(bytes)

        val result = FirmwareImageParser.parse("damaged.zbin", bytes)

        assertInvalidContains(result, "gzip")
    }

    @Test
    fun `nested raw payload and header crc failures are rejected`() {
        val badPayload = rawImage().also {
            it[FirmwareImageParser.HEADER_SIZE + 3] =
                (it[FirmwareImageParser.HEADER_SIZE + 3].toInt() xor 1).toByte()
        }
        val badHeader = rawImage().also { it[32] = (it[32].toInt() xor 1).toByte() }

        assertInvalidContains(FirmwareImageParser.parse("bad-payload.zbin", zbin(badPayload)), "payload CRC")
        assertInvalidContains(FirmwareImageParser.parse("bad-header.zbin", zbin(badHeader)), "header CRC")
    }

    @Test
    fun `outer and nested versions must match`() {
        val bytes = zbin(rawImage(version = listOf(49, 42, 1, 0)), version = listOf(50, 42, 1, 0))

        val result = FirmwareImageParser.parse("mismatch.zbin", bytes)

        assertInvalidContains(result, "version")
    }

    @Test
    fun `inflated image is bounded by app host policy`() {
        val oversizedRaw = rawImage(
            payloadSize = FirmwareImageParser.MAX_INFLATED_IMAGE_BYTES - FirmwareImageParser.HEADER_SIZE + 4,
        )

        val result = FirmwareImageParser.parse("oversized.zbin", zbin(oversizedRaw))

        assertInvalidContains(result, "inflated")
    }

    @Test
    fun `concatenated gzip member is rejected instead of hidden after the firmware image`() {
        val raw = rawImage()
        val gzipPayload = gzip(raw) + gzip(raw)

        val result = FirmwareImageParser.parse("concatenated.zbin", outerContainer(gzipPayload))

        assertInvalidContains(result, "trailing")
    }

    @Test
    fun `raw type one remains an accepted experimental input`() {
        val result = FirmwareImageParser.parse("research.bin", rawImage())

        assertTrue(result is FirmwareImageValidation.Valid)
        val info = (result as FirmwareImageValidation.Valid).image.info
        assertTrue(info.compatibilityNote.contains("Research raw form"))
    }

    @Test
    fun `retained original zbin passes structural validation when supplied externally`() {
        val path = System.getenv("NOOP_FIRMWARE_FIXTURE")
        assumeTrue(!path.isNullOrBlank())

        val file = File(path!!)
        val result = FirmwareImageParser.parse(file.name, file.readBytes())

        assertTrue(result is FirmwareImageValidation.Valid)
    }

    private fun assertInvalidContains(result: FirmwareImageValidation, expected: String) {
        assertTrue(result is FirmwareImageValidation.Invalid)
        assertTrue(
            "expected rejection mentioning '$expected', got '${(result as? FirmwareImageValidation.Invalid)?.reason}'",
            (result as FirmwareImageValidation.Invalid).reason.contains(expected, ignoreCase = true),
        )
    }

    private fun rawImage(
        payloadSize: Int = 440,
        version: List<Int> = listOf(50, 42, 1, 0),
    ): ByteArray {
        require(payloadSize > 0 && payloadSize % 4 == 0)
        val bytes = ByteArray(FirmwareImageParser.HEADER_SIZE + payloadSize)
        bytes.putU32(4, payloadSize)
        bytes.putU32(8, 5)
        bytes.putU32(12, FirmwareImageFormat.BIN_RAW.containerType)
        version.forEachIndexed { index, component -> bytes.putU32(0x7c + index * 4, component) }
        bytes.putU32(0, crc(bytes, FirmwareImageParser.HEADER_SIZE, bytes.size))
        bytes.putU32(504, crc(bytes, 8, 504))
        bytes.putU32(508, bytes.u32(0))
        return bytes
    }

    private fun zbin(
        raw: ByteArray,
        version: List<Int> = listOf(50, 42, 1, 0),
    ): ByteArray = outerContainer(gzip(raw), version)

    private fun outerContainer(
        unpaddedPayload: ByteArray,
        version: List<Int> = listOf(50, 42, 1, 0),
    ): ByteArray {
        val padding = (4 - unpaddedPayload.size % 4) % 4
        val payload = unpaddedPayload + ByteArray(padding)
        val bytes = ByteArray(FirmwareImageParser.HEADER_SIZE + payload.size)
        payload.copyInto(bytes, FirmwareImageParser.HEADER_SIZE)
        bytes.putU32(4, payload.size)
        bytes.putU32(8, 5)
        bytes.putU32(12, FirmwareImageFormat.ZBIN_COMPRESSED.containerType)
        version.forEachIndexed { index, component -> bytes.putU32(0x7c + index * 4, component) }
        bytes.putU32(0, crc(bytes, FirmwareImageParser.HEADER_SIZE, bytes.size))
        bytes.putU32(504, crc(bytes, 8, 504))
        bytes.putU32(508, bytes.u32(0))
        return bytes
    }

    private fun gzip(bytes: ByteArray): ByteArray = ByteArrayOutputStream().use { output ->
        GZIPOutputStream(output).use { it.write(bytes) }
        output.toByteArray()
    }

    private fun gzipWithEmptyFinalStoredBlock(bytes: ByteArray): ByteArray = ByteArrayOutputStream().use { output ->
        require(bytes.size <= 0xffff)
        output.write(byteArrayOf(0x1f, 0x8b.toByte(), 8, 0, 0, 0, 0, 0, 0, 0xff.toByte()))
        output.write(0) // Non-final stored DEFLATE block, byte-aligned.
        output.write(bytes.size and 0xff)
        output.write((bytes.size ushr 8) and 0xff)
        val inverseLength = bytes.size xor 0xffff
        output.write(inverseLength and 0xff)
        output.write((inverseLength ushr 8) and 0xff)
        output.write(bytes)
        output.write(1) // Final empty stored block.
        output.write(byteArrayOf(0, 0, 0xff.toByte(), 0xff.toByte()))
        output.writeU32(crc(bytes, 0, bytes.size))
        output.writeU32(bytes.size.toLong())
        output.toByteArray()
    }

    private fun repairOuterPayloadCrc(bytes: ByteArray) {
        bytes.putU32(0, crc(bytes, FirmwareImageParser.HEADER_SIZE, bytes.size))
    }

    private fun trailingZeroCount(bytes: ByteArray): Int {
        var count = 0
        var index = bytes.lastIndex
        while (index >= FirmwareImageParser.HEADER_SIZE && bytes[index] == 0.toByte() && count < 3) {
            count++
            index--
        }
        return count
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

    private fun ByteArrayOutputStream.writeU32(value: Long) {
        write(value.toInt() and 0xff)
        write((value ushr 8).toInt() and 0xff)
        write((value ushr 16).toInt() and 0xff)
        write((value ushr 24).toInt() and 0xff)
    }
}
