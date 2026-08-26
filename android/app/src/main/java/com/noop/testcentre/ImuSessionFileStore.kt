package com.noop.testcentre

import android.content.Context
import com.noop.protocol.Whoop5RawImu
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.zip.Deflater

/** Routes realtime and delayed history IMU frames into persisted session time windows. */
class ImuSessionFileStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("imu-session-windows", Context.MODE_PRIVATE)
    private val directory = File(context.filesDir, "ground-truth").apply { mkdirs() }

    fun start(id: String, deviceId: String, fromMs: Long) = synchronized(lock) {
        prefs.edit().putStringSet("ids", prefs.getStringSet("ids", emptySet()).orEmpty() + id)
            .putString("$id.device", deviceId).putLong("$id.from", fromMs / 1_000L).remove("$id.to").apply()
    }

    fun complete(id: String, toMs: Long) = synchronized(lock) {
        if (id in prefs.getStringSet("ids", emptySet()).orEmpty())
            prefs.edit().putLong("$id.to", toMs / 1_000L).apply()
    }

    fun register(id: String, deviceId: String, fromMs: Long, toMs: Long) = synchronized(lock) {
        start(id, deviceId, fromMs); complete(id, toMs)
    }

    fun remove(id: String) = synchronized(lock) {
        prefs.edit().putStringSet("ids", prefs.getStringSet("ids", emptySet()).orEmpty() - id)
            .remove("$id.device").remove("$id.from").remove("$id.to").apply()
    }

    /**
     * Append one independently-deflated wire frame. The timestamp header makes the stream scanable and
     * idempotent without inflating older records; delayed history may therefore fill gaps in any order.
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
            val deflater = Deflater()
            val compressed = ByteArray(frame.size + 64)
            deflater.setInput(frame); deflater.finish()
            val compressedSize = deflater.deflate(compressed); deflater.end()
            DataOutputStream(BufferedOutputStream(FileOutputStream(file, true))).use {
                it.writeLong(ts); it.writeLong(receivedAtMs); it.writeInt(frame.size); it.writeInt(compressedSize)
                it.write(compressed, 0, compressedSize)
            }
            timestamps += ts
            writes++
        }
        return writes
    }

    private fun timestamps(file: File): MutableSet<Long> = seen.getOrPut(file.absolutePath) {
        val result = mutableSetOf<Long>()
        if (!file.exists()) return@getOrPut result
        java.io.DataInputStream(file.inputStream().buffered()).use { input ->
            while (input.available() >= 24) {
                val ts = input.readLong(); input.readLong(); input.readInt()
                val compressed = input.readInt()
                if (compressed < 0 || compressed > 1_048_576 || input.available() < compressed) break
                result += ts
                input.skipBytes(compressed)
            }
        }
        result
    }

    companion object {
        private val lock = Any()
        private val seen = mutableMapOf<String, MutableSet<Long>>()
    }
}
