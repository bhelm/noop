package com.noop.protocol

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

internal fun ByteArray.u32le(offset: Int): Long =
    (this[offset].toLong() and 0xff) or
        ((this[offset + 1].toLong() and 0xff) shl 8) or
        ((this[offset + 2].toLong() and 0xff) shl 16) or
        ((this[offset + 3].toLong() and 0xff) shl 24)

internal fun ByteArray.u16le(offset: Int): Int =
    (this[offset].toInt() and 0xff) or ((this[offset + 1].toInt() and 0xff) shl 8)

private fun ByteArray.crc32(from: Int, until: Int): Long = CRC32().run {
    update(this@crc32, from, until - from)
    value
}

private fun Long.hex8(): String = "%08x".format(Locale.ROOT, this)
internal fun ByteArray.toHex(): String = joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }
