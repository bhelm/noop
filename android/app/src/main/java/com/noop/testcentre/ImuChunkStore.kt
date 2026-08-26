package com.noop.testcentre

import android.content.Context
import com.noop.data.ImuChunkEntity
import com.noop.data.StreamPersistence
import com.noop.data.WhoopRepository
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.json.JSONObject

/** Materializes immutable, portable IMU archives from the bounded SQLite rolling cache. */
class ImuChunkStore(private val context: Context, private val repository: WhoopRepository) {
    private val directory = File(context.filesDir, "imu-chunks").apply { mkdirs() }

    suspend fun pin(deviceId: String, from: Long, to: Long): List<ImuChunkEntity> {
        require(from <= to)
        val existing = repository.imuChunks(deviceId, from, to)
            .filter { it.pinnedUntil == Long.MAX_VALUE && File(context.filesDir, it.relativePath).isFile }
        if (existing.any { it.startTs <= from && it.endTs >= to }) return existing
        val limit = (to - from + 1).coerceIn(1, MAX_EXPORT_SECONDS).toInt()
        val rows = repository.rawImuSamples(deviceId, from, to, limit)
        if (rows.isEmpty()) return existing

        val id = UUID.randomUUID().toString()
        val final = File(directory, "$id.imuc")
        val temp = File(directory, "$id.tmp")
        ZipOutputStream(BufferedOutputStream(FileOutputStream(temp))).use { zip ->
            zip.putNextEntry(ZipEntry("manifest.json"))
            zip.write(JSONObject().apply {
                put("format", "NOOPIMU")
                put("version", FORMAT_VERSION)
                put("sample_rate_hz", SAMPLE_RATE)
                put("axes", listOf("ax", "ay", "az", "gx", "gy", "gz"))
                put("layout", "column-major-int16-le")
                put("row_count", rows.size)
                put("start_ts", rows.first().first)
                put("end_ts", rows.last().first)
            }.toString(2).toByteArray())
            zip.closeEntry()
            zip.putNextEntry(ZipEntry("samples.bin"))
            DataOutputStream(zip).let { payload ->
                for ((ts, columns) in rows) {
                    val packed = StreamPersistence.packImuColumns(columns)
                    payload.writeLong(ts)
                    payload.writeInt(packed.size)
                    payload.write(packed)
                }
            }
            zip.closeEntry()
        }
        check(temp.renameTo(final)) { "Could not commit IMU chunk" }
        val entity = ImuChunkEntity(
            id = id, deviceId = deviceId, startTs = rows.first().first, endTs = rows.last().first,
            sampleCount = rows.size * SAMPLE_RATE, sampleRate = SAMPLE_RATE,
            formatVersion = FORMAT_VERSION, codec = "zip-deflate",
            relativePath = final.relativeTo(context.filesDir).path, byteSize = final.length(),
            sha256 = sha256(final), createdAt = System.currentTimeMillis() / 1_000L,
            pinnedUntil = Long.MAX_VALUE,
        )
        repository.upsertImuChunk(entity)
        return coverage(deviceId, from, to)
    }

    suspend fun coverage(deviceId: String, from: Long, to: Long): List<ImuChunkEntity> =
        repository.imuChunks(deviceId, from, to).filter { File(context.filesDir, it.relativePath).isFile }

    fun file(chunk: ImuChunkEntity) = File(context.filesDir, chunk.relativePath)

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        const val FORMAT_VERSION = 1
        const val SAMPLE_RATE = 100
        const val MAX_EXPORT_SECONDS = 7 * 24 * 60 * 60L
    }
}
