package com.noop.testcentre

import android.content.Context
import com.noop.protocol.Whoop5RawImu
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.zip.Deflater
import java.util.zip.Inflater

/** Routes realtime and delayed history IMU frames into persisted session time windows. */
class ImuSessionFileStore(private val context: Context) {
    data class Stats(val bytes: Long, val coveredSeconds: Int, val firstTs: Long?)
    private val prefs = context.getSharedPreferences("imu-session-windows", Context.MODE_PRIVATE)
    private val directory = File(context.filesDir, "ground-truth").apply { mkdirs() }

    fun start(id: String, deviceId: String, fromMs: Long) = synchronized(lock) {
        prefs.edit().putStringSet("ids", prefs.getStringSet("ids", emptySet()).orEmpty() + id)
            .putString("$id.device", deviceId).putLong("$id.from", fromMs / 1_000L).remove("$id.to").apply()
    }

    fun complete(id: String, toMs: Long) = synchronized(lock) {
        flush(id)
        if (id in prefs.getStringSet("ids", emptySet()).orEmpty())
            prefs.edit().putLong("$id.to", toMs / 1_000L).apply()
    }

    fun register(id: String, deviceId: String, fromMs: Long, toMs: Long) = synchronized(lock) {
        start(id, deviceId, fromMs); complete(id, toMs)
    }

    fun remove(id: String) = synchronized(lock) {
        pending.remove(id)
        prefs.edit().putStringSet("ids", prefs.getStringSet("ids", emptySet()).orEmpty() - id)
            .remove("$id.device").remove("$id.from").remove("$id.to").apply()
    }

    fun prepareForRead(id: String) = synchronized(lock) { flush(id) }

    fun stats(id: String, from: Long, to: Long): Stats = synchronized(lock) {
        val file = File(directory, "realtime-imu-$id.imus")
        // Do not flush merely for UI telemetry: that would turn the intended 30-frame compression
        // blocks into one-frame blocks. Pending raw bytes are a close live size estimate until flush.
        val pendingBytes = pending[id].orEmpty().sumOf { it.frame.size.toLong() + 20L }
        val inWindow = timestamps(file).filter { it in from..to }
        Stats((file.takeIf(File::isFile)?.length() ?: 0L) + pendingBytes,
            inWindow.size, inWindow.minOrNull())
    }

    /**
     * Queue a wire frame for a 30-second compressed block. Delayed history may append older blocks later;
     * strap timestamps, not file order, are authoritative.
     */
    fun append(deviceId: String, frame: ByteArray, receivedAtMs: Long = System.currentTimeMillis()): Int = synchronized(lock) {
        val ts = Whoop5RawImu.decode(frame)?.baseTs ?: return 0
        var writes = 0
        for (id in prefs.getStringSet("ids", emptySet()).orEmpty()) {
            if (prefs.getString("$id.device", null) != deviceId) continue
            val from = prefs.getLong("$id.from", Long.MAX_VALUE)
            val to = if (prefs.contains("$id.to")) prefs.getLong("$id.to", Long.MIN_VALUE) else Long.MAX_VALUE
            if (ts !in from..to) continue
            val file = File(directory, "realtime-imu-$id.imus")
            val timestamps = timestamps(file)
            if (ts in timestamps) continue
            timestamps += ts
            pending.getOrPut(id) { mutableListOf() } += Record(ts, receivedAtMs, frame.copyOf())
            if (pending[id]!!.size >= BLOCK_FRAMES) flush(id)
            writes++
        }
        return writes
    }

    private fun timestamps(file: File): MutableSet<Long> = seen.getOrPut(file.absolutePath) {
        val result = mutableSetOf<Long>()
        if (!file.exists()) return@getOrPut result
        java.io.DataInputStream(file.inputStream().buffered()).use { input ->
            while (input.available() >= 12) {
                val count = input.readInt(); val rawSize = input.readInt(); val compressedSize = input.readInt()
                if (count !in 1..BLOCK_FRAMES || rawSize !in 1..MAX_BLOCK_BYTES ||
                    compressedSize !in 1..MAX_BLOCK_BYTES || input.available() < compressedSize) break
                val compressed = ByteArray(compressedSize); input.readFully(compressed)
                val raw = inflate(compressed, rawSize) ?: break
                java.io.DataInputStream(raw.inputStream()).use { block ->
                    for (index in 0 until count) {
                        result += block.readLong(); block.readLong()
                        val length = block.readInt(); if (length !in 1..MAX_FRAME_BYTES) break
                        block.skipBytes(length)
                    }
                }
            }
        }
        result
    }

    private fun flush(id: String) {
        val records = pending.remove(id).orEmpty()
        if (records.isEmpty()) return
        val rawBytes = java.io.ByteArrayOutputStream()
        DataOutputStream(rawBytes).use { out -> records.forEach { record ->
            out.writeLong(record.ts); out.writeLong(record.receivedAtMs)
            out.writeInt(record.frame.size); out.write(record.frame)
        }}
        val raw = rawBytes.toByteArray()
        val deflater = Deflater(); deflater.setInput(raw); deflater.finish()
        val compressed = ByteArray(raw.size + 128); val size = deflater.deflate(compressed); deflater.end()
        DataOutputStream(BufferedOutputStream(FileOutputStream(File(directory, "realtime-imu-$id.imus"), true))).use {
            it.writeInt(records.size); it.writeInt(raw.size); it.writeInt(size); it.write(compressed, 0, size)
        }
    }

    private fun inflate(compressed: ByteArray, size: Int): ByteArray? {
        val inflater = Inflater(); inflater.setInput(compressed)
        val raw = ByteArray(size); val written = runCatching { inflater.inflate(raw) }.getOrDefault(0); inflater.end()
        return raw.takeIf { written == size }
    }

    private data class Record(val ts: Long, val receivedAtMs: Long, val frame: ByteArray)

    companion object {
        private val lock = Any()
        private val seen = mutableMapOf<String, MutableSet<Long>>()
        private val pending = mutableMapOf<String, MutableList<Record>>()
        private const val BLOCK_FRAMES = 30
        private const val MAX_FRAME_BYTES = 1_048_576
        private const val MAX_BLOCK_BYTES = BLOCK_FRAMES * (MAX_FRAME_BYTES + 20)
    }
}
