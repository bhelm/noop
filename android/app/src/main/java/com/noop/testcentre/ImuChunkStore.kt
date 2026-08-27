package com.noop.testcentre

import android.content.Context
import com.noop.data.ImuChunkEntity
import com.noop.data.StreamPersistence
import com.noop.data.WhoopRepository
import com.noop.protocol.Whoop5RawImu
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.Inflater
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.json.JSONObject
import org.json.JSONArray

/** Materializes immutable archives from a session file; SQLite is only a short history staging cache. */
class ImuChunkStore(private val context: Context, private val repository: WhoopRepository) {
    private val directory = File(context.filesDir, "imu-chunks").apply { mkdirs() }

    suspend fun pin(sessionId: String, deviceId: String, from: Long, to: Long): List<ImuChunkEntity> {
        require(from <= to)
        ImuSessionFileStore(context).prepareForRead(sessionId)
        val existing = repository.imuChunks(deviceId, from, to)
            .filter { isOwnedChunk(sessionId, it.id) &&
                it.pinnedUntil == Long.MAX_VALUE &&
                File(context.filesDir, it.relativePath).isFile }
        existing.firstOrNull { it.startTs == from && it.endTs == to &&
            it.sampleCount >= (it.endTs - it.startTs + 1) * it.sampleRate }?.let { exact ->
            deleteOwned(sessionId, setOf(exact.id))
            return listOf(exact)
        }
        val rows = readSessionRows(sessionId, from, to)
        if (rows.isEmpty()) return boundedExisting(existing, from, to)
        val startTs = rows.minOf { it.first }
        val endTs = rows.maxOf { it.first }

        val id = chunkPrefix(sessionId) + UUID.randomUUID().toString()
        val final = File(directory, "$id.imuc")
        val temp = File(directory, "$id.tmp")
        ZipOutputStream(BufferedOutputStream(FileOutputStream(temp))).use { zip ->
            zip.putNextEntry(ZipEntry("manifest.json"))
            zip.write(JSONObject().apply {
                put("format", "NOOPIMU")
                put("version", FORMAT_VERSION)
                put("sample_rate_hz", SAMPLE_RATE)
                put("axes", JSONArray(listOf("ax", "ay", "az", "gx", "gy", "gz")))
                put("layout", "column-major-int16-le")
                put("ordering", "unspecified")
                put("timestamp_source", "strap")
                put("row_count", rows.size)
                put("start_ts", startTs)
                put("end_ts", endTs)
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
            id = id, deviceId = deviceId, startTs = startTs, endTs = endTs,
            sampleCount = rows.size * SAMPLE_RATE, sampleRate = SAMPLE_RATE,
            formatVersion = FORMAT_VERSION, codec = "zip-deflate",
            relativePath = final.relativeTo(context.filesDir).path, byteSize = final.length(),
            sha256 = sha256(final), createdAt = System.currentTimeMillis() / 1_000L,
            pinnedUntil = Long.MAX_VALUE,
        )
        repository.upsertImuChunk(entity)
        deleteOwned(sessionId, setOf(id))
        return coverage(sessionId, deviceId, from, to)
    }

    suspend fun coverage(sessionId: String, deviceId: String, from: Long, to: Long): List<ImuChunkEntity> =
        repository.imuChunks(deviceId, from, to).filter {
            it.id.startsWith(chunkPrefix(sessionId)) && it.startTs >= from && it.endTs <= to &&
                File(context.filesDir, it.relativePath).isFile
        }

    suspend fun deleteOwned(
        sessionId: String,
        keepIds: Set<String> = emptySet(),
    ): Boolean {
        val prefix = chunkPrefix(sessionId)
        val catalogIds = runCatching { repository.imuChunksByIdPrefix(prefix).map { it.id } }
            .getOrElse { return false }
        val fileIds = directory.listFiles { file ->
            file.extension == "imuc" && isOwnedChunk(sessionId, file.nameWithoutExtension)
        }.orEmpty().map { it.nameWithoutExtension }
        return (catalogIds + fileIds).toSet().filterNot { it in keepIds }.map { id ->
            val file = File(directory, "$id.imuc")
            deleteRetrySafe(
                fileExists = file.exists(),
                deleteFile = file::delete,
                deleteCatalog = { repository.deleteImuChunk(id) },
            )
        }.all { it }
    }

    suspend fun deleteDevice(deviceId: String): Boolean {
        val chunks = runCatching { repository.imuChunksForDevice(deviceId) }.getOrElse { return false }
        for (chunk in chunks) {
            val file = file(chunk)
            if (file.exists() && !file.delete()) return false
        }
        return runCatching { repository.deleteImuChunksFor(deviceId) }.isSuccess
    }

    fun file(chunk: ImuChunkEntity) = File(context.filesDir, chunk.relativePath)

    private fun readSessionRows(sessionId: String, from: Long, to: Long): List<Pair<Long, ShortArray>> {
        val source = File(context.filesDir, "ground-truth/realtime-imu-$sessionId.imus")
        if (!source.isFile) return emptyList()
        val rows = ArrayList<Pair<Long, ShortArray>>()
        DataInputStream(source.inputStream().buffered()).use { input ->
            while (true) {
                try {
                    val count = input.readInt(); val rawSize = input.readInt(); val compressedLength = input.readInt()
                    if (count !in 1..BLOCK_FRAMES || rawSize !in 1..MAX_BLOCK_BYTES ||
                        compressedLength !in 1..MAX_BLOCK_BYTES) break
                    val compressed = ByteArray(compressedLength); input.readFully(compressed)
                    val inflater = Inflater(); inflater.setInput(compressed)
                    val raw = ByteArray(rawSize); val inflated = inflater.inflate(raw); inflater.end()
                    if (inflated != rawSize) continue
                    DataInputStream(raw.inputStream()).use { block -> for (index in 0 until count) {
                        val ts = block.readLong(); block.readLong(); val length = block.readInt()
                        if (length !in 1..MAX_FRAME_BYTES) break
                        val frame = ByteArray(length); block.readFully(frame)
                        val decoded = Whoop5RawImu.decode(frame) ?: continue
                        if (ts == decoded.baseTs && ts in from..to)
                            Whoop5RawImu.rawColumns(frame)?.let { rows += ts to it }
                    }}
                } catch (_: EOFException) { break }
            }
        }
        return rows
    }

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
        private fun chunkPrefix(sessionId: String) = "$sessionId--"
        internal fun isOwnedChunk(sessionId: String, chunkId: String) = chunkId.startsWith(chunkPrefix(sessionId))
        internal fun boundedExisting(chunks: List<ImuChunkEntity>, from: Long, to: Long) =
            chunks.filter { it.startTs >= from && it.endTs <= to }
        internal suspend fun deleteRetrySafe(
            fileExists: Boolean,
            deleteFile: () -> Boolean,
            deleteCatalog: suspend () -> Unit,
        ): Boolean {
            if (fileExists && !deleteFile()) return false
            return runCatching { deleteCatalog() }.isSuccess
        }
        const val FORMAT_VERSION = 1
        const val SAMPLE_RATE = 100
        const val MAX_EXPORT_SECONDS = 7 * 24 * 60 * 60L
        const val MAX_FRAME_BYTES = 1024 * 1024
        const val BLOCK_FRAMES = 30
        const val MAX_BLOCK_BYTES = BLOCK_FRAMES * (MAX_FRAME_BYTES + 20)
    }
}
