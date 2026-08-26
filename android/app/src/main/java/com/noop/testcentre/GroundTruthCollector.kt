package com.noop.testcentre

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.content.FileProvider
import com.noop.BuildConfig
import com.noop.data.WhoopRepository
import com.noop.ingest.RawSensorExport
import com.noop.protocol.Whoop5RawImu
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** App-private, explicitly user-started bounded 5/MG raw-data capture with optional manual labels. */
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

    data class SessionSummary(
        val id: String,
        val deviceId: String?,
        val startedAtMs: Long,
        val endedAtMs: Long?,
        val steps: Int,
        val stairs: Int,
        val excludedWindows: Int,
        val comment: String,
        val active: Boolean,
        val exported: Boolean,
    )

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private val directory = File(context.filesDir, "ground-truth").apply { mkdirs() }

    fun realtimeImuBytes(sessionId: String): Long =
        File(directory, "realtime-imu-$sessionId.bin").takeIf(File::isFile)?.length() ?: 0L

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
    fun start(noopSteps: Int?, deviceId: String, nowMs: Long = System.currentTimeMillis()): Snapshot {
        val id = nowMs.toString()
        prefs.edit()
            .putBoolean("active", true)
            .putBoolean("exported", false)
            .putString("sessionId", id)
            .putString("deviceId", deviceId)
            .putLong("startedAtMs", nowMs)
            .putInt("steps", 0)
            .putInt("stairs", 0)
            .applyNoopStart(noopSteps)
            .applyNoop(noopSteps)
            .putString(sessionDeviceKey(id), deviceId)
            .putLong(sessionStartKey(id), nowMs)
            .putBoolean(sessionExportedKey(id), false)
            .apply()
        append(id, event(nowMs, "start", 0, 0, noopSteps, null).put("strap_device_id", deviceId))
        return snapshot()
    }

    @Synchronized
    fun excludeLastMinutes(minutes: Int, nowMs: Long = System.currentTimeMillis()): Snapshot {
        require(minutes in 1..240) { "Minutes must be between 1 and 240" }
        val before = snapshot()
        if (!before.active || before.sessionId == null) return before
        val fromMs = maxOf(before.startedAtMs, nowMs - minutes * 60_000L)
        append(before.sessionId, event(nowMs, KIND_EXCLUDE, before.steps, before.stairs, before.lastNoopSteps, null).apply {
            put("from_ms", fromMs)
            put("to_ms", nowMs)
        })
        return snapshot()
    }

    @Synchronized
    fun setComment(sessionId: String, comment: String) {
        prefs.edit().putString(sessionCommentKey(sessionId), comment.take(4_000)).apply()
    }

    @Synchronized
    fun sessions(): List<SessionSummary> {
        val current = snapshot()
        return directory.listFiles { file -> file.name.startsWith("session-") && file.name.endsWith(".jsonl") }
            .orEmpty()
            .mapNotNull { file ->
                val id = file.name.removePrefix("session-").removeSuffix(".jsonl")
                val rows = runCatching {
                    file.useLines { lines ->
                        lines.filter { it.isNotBlank() }
                            .mapNotNull { line -> runCatching { JSONObject(line) }.getOrNull() }
                            .toList()
                    }
                }.getOrNull() ?: return@mapNotNull null
                val first = rows.firstOrNull() ?: return@mapNotNull null
                val last = rows.last()
                val isCurrent = current.sessionId == id
                SessionSummary(
                    id = id,
                    deviceId = prefs.getString(sessionDeviceKey(id), null)
                        ?: first.optString("strap_device_id").takeIf(String::isNotBlank)
                        ?: current.deviceId.takeIf { isCurrent },
                    startedAtMs = prefs.getLong(sessionStartKey(id), first.optLong("at_ms")),
                    endedAtMs = rows.lastOrNull { it.optString("kind") == "stop" }?.optLong("at_ms"),
                    steps = last.optInt("steps_total"),
                    stairs = last.optInt("stairs_total"),
                    excludedWindows = rows.count { it.optString("kind") == KIND_EXCLUDE },
                    comment = prefs.getString(sessionCommentKey(id), "").orEmpty(),
                    active = isCurrent && current.active,
                    exported = prefs.getBoolean(sessionExportedKey(id), isCurrent && current.exported),
                )
            }
            .sortedByDescending { it.startedAtMs }
    }

    /** Delete one completed capture and all app-private files and metadata belonging to it. */
    @Synchronized
    fun deleteSession(sessionId: String): Boolean {
        val current = snapshot()
        if (current.active && current.sessionId == sessionId) return false

        val files = listOf(
            eventFile(sessionId),
            File(directory, "realtime-imu-$sessionId.bin"),
            File(context.cacheDir, "logs/noop-ground-truth-$sessionId.zip"),
        )
        // Evaluate every deletion even if one fails, so a stale export ZIP never survives merely
        // because an earlier file could not be removed.
        val filesDeleted = files.map { file -> !file.exists() || file.delete() }.all { it }
        if (eventFile(sessionId).exists()) return false

        prefs.edit()
            .remove(sessionDeviceKey(sessionId))
            .remove(sessionStartKey(sessionId))
            .remove(sessionEndKey(sessionId))
            .remove(sessionCommentKey(sessionId))
            .remove(sessionExportedKey(sessionId))
            .apply {
                if (current.sessionId == sessionId) clearCurrentSession()
            }
            .apply()
        return filesDeleted
    }

    /** Delete every completed capture. Active captures are deliberately retained. */
    @Synchronized
    fun deleteAllSessions(): Int {
        val completedIds = sessions().filterNot(SessionSummary::active).map(SessionSummary::id)
        return completedIds.count(::deleteSession)
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
            .putLong(sessionEndKey(before.sessionId), nowMs)
            .applyNoop(noopSteps).apply()
        return snapshot()
    }

    suspend fun export(repo: WhoopRepository, sessionId: String? = null): File = withContext(Dispatchers.IO) {
        val snap = snapshot()
        val id = sessionId ?: requireNotNull(snap.sessionId) { "No ground-truth session has been recorded" }
        val summary = sessions().firstOrNull { it.id == id } ?: error("Ground-truth session is missing")
        require(!summary.active) { "Stop the session before exporting" }
        val deviceId = summary.deviceId
        val source = eventFile(id)
        require(source.isFile) { "Ground-truth event file is missing" }
        val events = source.useLines { lines ->
            lines.filter { it.isNotBlank() }
                .mapNotNull { line -> runCatching { JSONObject(line) }.getOrNull() }
                .toList()
        }
        val endMs = summary.endedAtMs ?: events.lastOrNull()?.optLong("at_ms") ?: summary.startedAtMs
        val exclusions = exclusionWindows(events)
        // A raw-data session is useful even without manual step labels. Export its complete bounded
        // interval; labels and exclusion windows are optional annotations, not a prerequisite for data.
        val sensorFrom = if (deviceId == null) 1L else summary.startedAtMs / 1_000L
        val sensorTo = if (deviceId == null) 0L else endMs / 1_000L
        val outDir = File(context.cacheDir, "logs").apply { mkdirs() }
        val zip = File(outDir, "noop-ground-truth-$id.zip")
        ZipOutputStream(zip.outputStream().buffered()).use { out ->
            out.putNextEntry(ZipEntry("meta.json"))
            out.writerEntry(JSONObject().apply {
                put("schema_version", 3)
                put("capture_kind", "whoop_5mg_raw_data")
                put("session_id", id)
                if (deviceId != null) put("device_id", deviceId)
                put("sensor_export_available", deviceId != null)
                put("started_at_ms", summary.startedAtMs)
                put("ended_at_ms", endMs)
                put("manual_steps", summary.steps)
                put("manual_stairs", summary.stairs)
                put("comment", summary.comment)
                put("excluded_windows", summary.excludedWindows)
                if (id == snap.sessionId && snap.noopStepsAtStart != null) put("noop_steps_at_start", snap.noopStepsAtStart)
                if (id == snap.sessionId && snap.lastNoopSteps != null) put("noop_steps_at_end", snap.lastNoopSteps)
                put("device_family", "Android")
                put("app_version", BuildConfig.VERSION_NAME)
                put("manual_labels", "optional; JX-05S ZOOM_OUT=step, ZOOM_IN=stair")
            }.toString(2))

            out.putNextEntry(ZipEntry("events.jsonl"))
            source.inputStream().use { it.copyTo(out) }
            out.closeEntry()

            out.putNextEntry(ZipEntry("events.csv"))
            out.writerEntry(eventsCsv(events))

            out.putNextEntry(ZipEntry("seconds.csv"))
            out.writerEntry(secondsCsv(events, sensorFrom * 1_000L, sensorTo * 1_000L))

            out.putNextEntry(ZipEntry("exclusions.csv"))
            out.writerEntry(exclusionsCsv(events))

            out.putNextEntry(ZipEntry("algorithm-signals.csv"))
            out.bufferedWriterNoClose().use { writer ->
                writeAlgorithmSignalsCsv(
                    writer,
                    repo,
                    deviceId.orEmpty(),
                    sensorFrom,
                    sensorTo,
                    exclusions,
                )
                writer.flush()
            }
            out.closeEntry()

            out.putNextEntry(ZipEntry("v18-aux.csv"))
            out.bufferedWriterNoClose().use { writer ->
                writeV18AuxCsv(writer, repo, deviceId.orEmpty(), sensorFrom, sensorTo)
                writer.flush()
            }
            out.closeEntry()

            out.putNextEntry(ZipEntry("raw-sensors.csv"))
            out.bufferedWriterNoClose().use { writer ->
                RawSensorExport.writeCsv(
                    writer, repo, deviceId.orEmpty(),
                    sensorFrom,
                    sensorTo,
                )
                writer.flush()
            }
            out.closeEntry()

            out.putNextEntry(ZipEntry("raw-imu.csv"))
            out.bufferedWriterNoClose().use { writer ->
                writeRawImuCsv(writer, repo, deviceId.orEmpty(), sensorFrom, sensorTo)
                writer.flush()
            }
            out.closeEntry()

            val realtimeImu = File(directory, "realtime-imu-$id.bin")
            if (realtimeImu.isFile) {
                out.putNextEntry(ZipEntry("realtime-imu.bin"))
                realtimeImu.inputStream().use { it.copyTo(out) }
                out.closeEntry()
            }
        }
        prefs.edit().putBoolean(sessionExportedKey(id), true).apply()
        if (id == snap.sessionId) prefs.edit().putBoolean("exported", true).apply()
        zip
    }

    fun share(file: File) {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, "NOOP 5/MG raw-data session")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }, "Export raw-data session").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
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
        append("unix_s,manual_steps_delta,manual_stairs_delta,manual_steps_total,manual_stairs_total,noop_steps,excluded\n")
        val bySecond = events.groupBy { it.optLong("at_ms") / 1_000L }
        val exclusions = exclusionWindows(events)
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
            val excluded = exclusions.any { second * 1_000L <= it.second && second * 1_000L + 999L >= it.first }
            append("$second,$stepDelta,$stairDelta,$steps,$stairs,${noop ?: ""},$excluded\n")
        }
    }

    private fun exclusionsCsv(events: List<JSONObject>): String = buildString {
        append("from_ms,to_ms,from_unix_s,to_unix_s\n")
        for ((from, to) in exclusionWindows(events)) append("$from,$to,${from / 1_000L},${to / 1_000L}\n")
    }

    private fun exclusionWindows(events: List<JSONObject>): List<Pair<Long, Long>> = events
        .filter { it.optString("kind") == KIND_EXCLUDE }
        .mapNotNull { row ->
            val from = row.optLong("from_ms", -1L)
            val to = row.optLong("to_ms", -1L)
            if (from >= 0L && to >= from) from to to else null
        }

    private fun csv(value: String): String = if (value.any { it == ',' || it == '"' || it == '\n' })
        "\"${value.replace("\"", "\"\"")}\"" else value

    private suspend fun writeAlgorithmSignalsCsv(
        out: java.io.Writer,
        repo: WhoopRepository,
        deviceId: String,
        from: Long,
        to: Long,
        exclusions: List<Pair<Long, Long>>,
    ) {
        out.write("unix_s,counter,activity_class,gravity_x,gravity_y,gravity_z,dynamic_accel,cadence,heart_rate,band_sleep_state,excluded\n")
        if (from > to || deviceId.isBlank()) return
        val seconds = (to - from + 1L).coerceIn(1L, 86_400L).toInt()
        val steps = repo.stepSamples(deviceId, from, to, seconds).associateBy { it.ts }
        val gravity = repo.gravitySamples(deviceId, from, to, seconds).associateBy { it.ts }
        val cadence = repo.v18AuxSamples(deviceId, from, to, seconds).associate { it.ts to it.stepCadence }
        val heartRate = repo.rawHrSamples(deviceId, from, to, seconds).associate { it.ts to it.bpm }
        val sleepState = repo.sleepStateSamples(deviceId, from, to, seconds).associate { it.ts to it.state }
        val timestamps = (steps.keys + gravity.keys + cadence.keys + heartRate.keys + sleepState.keys).sorted()
        for (ts in timestamps) {
            val step = steps[ts]
            val grav = gravity[ts]
            val excluded = exclusions.any { ts * 1_000L <= it.second && ts * 1_000L + 999L >= it.first }
            out.write(listOf(
                ts,
                step?.counter ?: "",
                step?.activityClass ?: "",
                grav?.x?.let(::decimal) ?: "",
                grav?.y?.let(::decimal) ?: "",
                grav?.z?.let(::decimal) ?: "",
                grav?.dynAccel?.let(::decimal) ?: "",
                cadence[ts] ?: "",
                heartRate[ts] ?: "",
                sleepState[ts] ?: "",
                excluded,
            ).joinToString(","))
            out.write("\n")
        }
    }

    private suspend fun writeV18AuxCsv(
        out: java.io.Writer,
        repo: WhoopRepository,
        deviceId: String,
        from: Long,
        to: Long,
    ) {
        out.write(
            "unix_s,record_index,rr_count,cardiac_flags,hr_quality_flags,heart_rate_alt,rr_packed," +
                "cardiac_status,step_cadence,status_word,status_word_1,status_word_2,aux_byte_82," +
                "optical_baseline_a,optical_baseline_b,optical_amp_a,optical_amp_b,unknown_f32_bits\n",
        )
        if (from > to || deviceId.isBlank()) return
        val seconds = (to - from + 1L).coerceIn(1L, 86_400L).toInt()
        for (row in repo.v18AuxSamples(deviceId, from, to, seconds)) {
            out.write((listOf(row.ts) + row.slotValues.map { it ?: "" }).joinToString(","))
            out.write("\n")
        }
    }

    private fun decimal(value: Double): String = String.format(java.util.Locale.US, "%.9f", value)

    private suspend fun writeRawImuCsv(
        out: java.io.Writer,
        repo: WhoopRepository,
        deviceId: String,
        from: Long,
        to: Long,
    ) {
        out.write("unix_s,sample_index,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps\n")
        if (from > to) return
        val secondsInWindow = (to - from + 1L).coerceIn(1L, 86_400L).toInt()
        for ((ts, columns) in repo.rawImuSamples(deviceId, from, to, limit = secondsInWindow)) {
            if (columns.size < Whoop5RawImu.sampleCount * 6) continue
            for (i in 0 until Whoop5RawImu.sampleCount) {
                val values = listOf(
                    columns[i] * Whoop5RawImu.accelScale,
                    columns[Whoop5RawImu.sampleCount + i] * Whoop5RawImu.accelScale,
                    columns[Whoop5RawImu.sampleCount * 2 + i] * Whoop5RawImu.accelScale,
                    columns[Whoop5RawImu.sampleCount * 3 + i] * Whoop5RawImu.gyroScale,
                    columns[Whoop5RawImu.sampleCount * 4 + i] * Whoop5RawImu.gyroScale,
                    columns[Whoop5RawImu.sampleCount * 5 + i] * Whoop5RawImu.gyroScale,
                ).joinToString(",") { String.format(java.util.Locale.US, "%.8f", it) }
                out.write("$ts,$i,$values\n")
            }
        }
    }

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

    private fun android.content.SharedPreferences.Editor.clearCurrentSession() = apply {
        remove("active")
        remove("exported")
        remove("sessionId")
        remove("deviceId")
        remove("startedAtMs")
        remove("endedAtMs")
        remove("steps")
        remove("stairs")
        remove("lastKind")
        remove("lastKey")
        remove("noopStepsAtStart")
        remove("lastNoopSteps")
    }

    private fun sessionDeviceKey(id: String) = "session.$id.deviceId"
    private fun sessionStartKey(id: String) = "session.$id.startedAtMs"
    private fun sessionEndKey(id: String) = "session.$id.endedAtMs"
    private fun sessionCommentKey(id: String) = "session.$id.comment"
    private fun sessionExportedKey(id: String) = "session.$id.exported"

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
        private const val KIND_EXCLUDE = "exclude_window"
        private const val PREFS = "noop_ground_truth_collector"

        fun from(context: Context) = GroundTruthCollector(context.applicationContext)
    }
}
