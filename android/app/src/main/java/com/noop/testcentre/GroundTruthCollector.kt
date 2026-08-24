package com.noop.testcentre

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.content.FileProvider
import com.noop.BuildConfig
import com.noop.data.WhoopRepository
import com.noop.ingest.RawSensorExport
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** App-private, explicitly user-started manual ground-truth capture. No production metric reads this. */
class GroundTruthCollector private constructor(private val context: Context) {
    data class KeyInfo(
        val keyCode: Int,
        val keyName: String,
        val scanCode: Int,
        val deviceId: Int,
        val deviceName: String,
        val source: Int,
    )

    data class Snapshot(
        val active: Boolean,
        val sessionId: String?,
        val deviceId: String?,
        val startedAtMs: Long,
        val endedAtMs: Long,
        val steps: Int,
        val stairs: Int,
        val lastKind: String?,
        val lastKey: KeyInfo?,
        val noopStepsAtStart: Int?,
        val lastNoopSteps: Int?,
        val exported: Boolean,
    )

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private val directory = File(context.filesDir, "ground-truth").apply { mkdirs() }

    @Synchronized
    fun snapshot(): Snapshot = Snapshot(
        active = prefs.getBoolean("active", false),
        sessionId = prefs.getString("sessionId", null),
        deviceId = prefs.getString("deviceId", null),
        startedAtMs = prefs.getLong("startedAtMs", 0L),
        endedAtMs = prefs.getLong("endedAtMs", 0L),
        steps = prefs.getInt("steps", 0),
        stairs = prefs.getInt("stairs", 0),
        lastKind = prefs.getString("lastKind", null),
        lastKey = prefs.getString("lastKey", null)?.let(::decodeKey),
        noopStepsAtStart = prefs.getInt("noopStepsAtStart", Int.MIN_VALUE).takeUnless { it == Int.MIN_VALUE },
        lastNoopSteps = prefs.getInt("lastNoopSteps", Int.MIN_VALUE).takeUnless { it == Int.MIN_VALUE },
        exported = prefs.getBoolean("exported", false),
    )

    @Synchronized
    fun start(noopSteps: Int, deviceId: String, nowMs: Long = System.currentTimeMillis()): Snapshot {
        check(snapshot().sessionId == null || snapshot().exported) { "Export the previous session before starting another" }
        val id = nowMs.toString()
        prefs.edit().clear()
            .putBoolean("active", true)
            .putBoolean("exported", false)
            .putString("sessionId", id)
            .putString("deviceId", deviceId)
            .putLong("startedAtMs", nowMs)
            .putInt("steps", 0)
            .putInt("stairs", 0)
            .applyNoopStart(noopSteps)
            .applyNoop(noopSteps)
            .apply()
        append(id, event(nowMs, "start", 0, 0, noopSteps, null))
        return snapshot()
    }

    @Synchronized
    fun record(kind: String, key: KeyInfo, noopSteps: Int?, nowMs: Long = System.currentTimeMillis()): Snapshot {
        val before = snapshot()
        if (!before.active || before.sessionId == null) return before
        val steps = before.steps + if (kind == KIND_STEP || kind == KIND_STAIR) 1 else 0
        val stairs = before.stairs + if (kind == KIND_STAIR) 1 else 0
        append(before.sessionId, event(nowMs, kind, steps, stairs, noopSteps, key))
        prefs.edit()
            .putInt("steps", steps)
            .putInt("stairs", stairs)
            .putString("lastKind", kind)
            .putString("lastKey", encodeKey(key))
            .applyNoop(noopSteps)
            .apply()
        return snapshot()
    }

    /** Record an unmapped key for diagnosis without changing either ground-truth counter. */
    @Synchronized
    fun observeKey(key: KeyInfo, noopSteps: Int?, nowMs: Long = System.currentTimeMillis()): Snapshot {
        val before = snapshot()
        prefs.edit().putString("lastKey", encodeKey(key)).applyNoop(noopSteps).apply()
        if (before.active && before.sessionId != null) {
            append(before.sessionId, event(nowMs, KIND_KEY, before.steps, before.stairs, noopSteps, key))
        }
        return snapshot()
    }

    @Synchronized
    fun undo(noopSteps: Int?, nowMs: Long = System.currentTimeMillis()): Snapshot {
        val before = snapshot()
        if (!before.active || before.sessionId == null) return before
        val undoKind = when (before.lastKind) {
            KIND_STEP -> KIND_UNDO_STEP
            KIND_STAIR -> KIND_UNDO_STAIR
            else -> return before
        }
        val steps = (before.steps - 1).coerceAtLeast(0)
        val stairs = (before.stairs - if (undoKind == KIND_UNDO_STAIR) 1 else 0).coerceAtLeast(0)
        append(before.sessionId, event(nowMs, undoKind, steps, stairs, noopSteps, null))
        prefs.edit().putInt("steps", steps).putInt("stairs", stairs).remove("lastKind")
            .applyNoop(noopSteps).apply()
        return snapshot()
    }

    @Synchronized
    fun stop(noopSteps: Int?, nowMs: Long = System.currentTimeMillis()): Snapshot {
        val before = snapshot()
        if (!before.active || before.sessionId == null) return before
        append(before.sessionId, event(nowMs, "stop", before.steps, before.stairs, noopSteps, null))
        prefs.edit().putBoolean("active", false).putLong("endedAtMs", nowMs)
            .applyNoop(noopSteps).apply()
        return snapshot()
    }

    suspend fun export(repo: WhoopRepository): File = withContext(Dispatchers.IO) {
        val snap = snapshot()
        require(!snap.active) { "Stop the session before exporting" }
        val id = requireNotNull(snap.sessionId) { "No ground-truth session has been recorded" }
        val deviceId = requireNotNull(snap.deviceId) { "The session has no recorded device" }
        val source = eventFile(id)
        require(source.isFile) { "Ground-truth event file is missing" }
        val events = source.useLines { lines -> lines.filter { it.isNotBlank() }.map(::JSONObject).toList() }
        val endMs = snap.endedAtMs.takeIf { it > 0 } ?: System.currentTimeMillis()
        val outDir = File(context.cacheDir, "logs").apply { mkdirs() }
        val zip = File(outDir, "noop-ground-truth-$id.zip")
        ZipOutputStream(zip.outputStream().buffered()).use { out ->
            out.putNextEntry(ZipEntry("meta.json"))
            out.writerEntry(JSONObject().apply {
                put("schema_version", 1)
                put("session_id", id)
                put("device_id", deviceId)
                put("started_at_ms", snap.startedAtMs)
                put("ended_at_ms", endMs)
                put("manual_steps", snap.steps)
                put("manual_stairs", snap.stairs)
                if (snap.noopStepsAtStart != null) put("noop_steps_at_start", snap.noopStepsAtStart)
                if (snap.lastNoopSteps != null) put("noop_steps_at_end", snap.lastNoopSteps)
                put("device_family", "Android")
                put("app_version", BuildConfig.VERSION_NAME)
                put("ground_truth", "JX-05S hardware clicker; ZOOM_OUT=step, ZOOM_IN=stair")
            }.toString(2))

            out.putNextEntry(ZipEntry("events.jsonl"))
            source.inputStream().use { it.copyTo(out) }
            out.closeEntry()

            out.putNextEntry(ZipEntry("events.csv"))
            out.writerEntry(eventsCsv(events))

            out.putNextEntry(ZipEntry("seconds.csv"))
            out.writerEntry(secondsCsv(events, snap.startedAtMs, endMs))

            out.putNextEntry(ZipEntry("raw-sensors.csv"))
            out.bufferedWriterNoClose().use { writer ->
                RawSensorExport.writeCsv(
                    writer, repo, deviceId,
                    snap.startedAtMs / 1_000L - 5L,
                    endMs / 1_000L + 5L,
                )
                writer.flush()
            }
            out.closeEntry()
        }
        prefs.edit().putBoolean("exported", true).apply()
        zip
    }

    fun share(file: File) {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, "NOOP ground-truth session")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }, "Export ground-truth session"))
    }

    private fun eventFile(id: String) = File(directory, "session-$id.jsonl")
    private fun append(id: String, value: JSONObject) = eventFile(id).appendText(value.toString() + "\n")

    private fun event(
        atMs: Long, kind: String, steps: Int, stairs: Int, noopSteps: Int?, key: KeyInfo?,
    ) = JSONObject().apply {
        put("at_ms", atMs); put("kind", kind); put("steps_total", steps); put("stairs_total", stairs)
        if (noopSteps != null) put("noop_steps", noopSteps)
        if (key != null) {
            put("key_code", key.keyCode); put("key_name", key.keyName); put("scan_code", key.scanCode)
            put("device_id", key.deviceId); put("device_name", key.deviceName); put("source", key.source)
        }
    }

    private fun eventsCsv(events: List<JSONObject>): String = buildString {
        append("at_ms,unix_s,kind,steps_total,stairs_total,noop_steps,key_code,key_name,scan_code,device_id,device_name,source\n")
        for (e in events) append(listOf(
            e.optLong("at_ms"), e.optLong("at_ms") / 1_000L, e.optString("kind"),
            e.optInt("steps_total"), e.optInt("stairs_total"), e.opt("noop_steps") ?: "",
            e.opt("key_code") ?: "", e.optString("key_name"), e.opt("scan_code") ?: "",
            e.opt("device_id") ?: "", e.optString("device_name"), e.opt("source") ?: "",
        ).joinToString(",") { csv(it.toString()) }).append('\n')
    }

    private fun secondsCsv(events: List<JSONObject>, startMs: Long, endMs: Long): String = buildString {
        append("unix_s,manual_steps_delta,manual_stairs_delta,manual_steps_total,manual_stairs_total,noop_steps\n")
        val bySecond = events.groupBy { it.optLong("at_ms") / 1_000L }
        var steps = 0; var stairs = 0; var noop: Int? = null
        for (second in startMs / 1_000L..endMs / 1_000L) {
            val rows = bySecond[second].orEmpty()
            val stepDelta = rows.count { it.optString("kind") == KIND_STEP || it.optString("kind") == KIND_STAIR } -
                rows.count { it.optString("kind") == KIND_UNDO_STEP || it.optString("kind") == KIND_UNDO_STAIR }
            val stairDelta = rows.count { it.optString("kind") == KIND_STAIR } - rows.count { it.optString("kind") == KIND_UNDO_STAIR }
            rows.lastOrNull()?.let { last ->
                steps = last.optInt("steps_total", steps); stairs = last.optInt("stairs_total", stairs)
                if (last.has("noop_steps")) noop = last.optInt("noop_steps")
            }
            append("$second,$stepDelta,$stairDelta,$steps,$stairs,${noop ?: ""}\n")
        }
    }

    private fun csv(value: String): String = if (value.any { it == ',' || it == '"' || it == '\n' })
        "\"${value.replace("\"", "\"\"")}\"" else value

    private fun encodeKey(key: KeyInfo) = JSONObject().apply {
        put("keyCode", key.keyCode); put("keyName", key.keyName); put("scanCode", key.scanCode)
        put("deviceId", key.deviceId); put("deviceName", key.deviceName); put("source", key.source)
    }.toString()

    private fun decodeKey(raw: String): KeyInfo = JSONObject(raw).let {
        KeyInfo(it.getInt("keyCode"), it.getString("keyName"), it.getInt("scanCode"),
            it.getInt("deviceId"), it.getString("deviceName"), it.getInt("source"))
    }

    private fun android.content.SharedPreferences.Editor.applyNoop(value: Int?) = apply {
        if (value == null) remove("lastNoopSteps") else putInt("lastNoopSteps", value)
    }

    private fun android.content.SharedPreferences.Editor.applyNoopStart(value: Int?) = apply {
        if (value == null) remove("noopStepsAtStart") else putInt("noopStepsAtStart", value)
    }

    private fun ZipOutputStream.writerEntry(text: String) {
        write(text.toByteArray(Charsets.UTF_8)); closeEntry()
    }

    /** A Writer whose close only flushes, because closing it must not close the surrounding ZIP. */
    private fun ZipOutputStream.bufferedWriterNoClose() = object : java.io.OutputStreamWriter(this, Charsets.UTF_8) {
        override fun close() = flush()
    }.buffered()

    companion object {
        const val KIND_STEP = "step"
        const val KIND_STAIR = "stair"
        private const val KIND_KEY = "key"
        private const val KIND_UNDO_STEP = "undo_step"
        private const val KIND_UNDO_STAIR = "undo_stair"
        private const val PREFS = "noop_ground_truth_collector"

        fun from(context: Context) = GroundTruthCollector(context.applicationContext)
    }
}
