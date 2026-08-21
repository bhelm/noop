package com.noop.ingest

import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.Toast
import androidx.core.content.FileProvider
import com.noop.BuildConfig
import com.noop.data.WhoopRepository
import java.io.File
import java.io.Reader
import java.io.Writer
import java.util.PriorityQueue
import java.util.Base64
import java.util.Locale

/**
 * EXPERIMENTAL diagnostic: dump the decoded per-sample sensor streams NOOP already stores to ONE
 * combined long-format CSV (last 24 h) and share it. Lets power users / external devs prototype
 * sleep / activity / VBT algorithms on real data without a BLE stream (#308/#276/#322).
 *
 * Long format = one row per sample, with a `stream` discriminator and ONLY that stream's columns
 * filled (the rest blank). Streams: hr / rr / gravity / steps / ppghr / spo2 / skintemp / resp /
 * event. All rows are merged then sorted by ts ascending. Plain text only — never any BLE hex.
 *
 * The `hr` stream reads the RAW `hrSample` table (NOT WhoopDao.hrSamples, which COALESCE-unions in
 * the v26 PPG-derived HR); PPG HR is its own `ppghr` stream so a measured sensor HR is never
 * confused with a derived estimate. Columns and semantics MATCH the Swift exporter byte-for-byte:
 *   unix_s,iso_utc,stream,hr_bpm,rr_ms,grav_x,grav_y,grav_z,step_counter,ppg_bpm,ppg_conf,
 *   spo2_red,spo2_ir,skintemp_raw,resp_raw,event_kind,event_payload
 *
 * On-device only — the file is written to cache/logs (the existing FileProvider path) and shared via
 * the same ACTION_SEND mechanism as the strap-log export; nothing leaves the phone unless shared.
 */
object RawSensorExport {

    data class SessionFile(
        val file: File,
        val counts: Map<String, Int>,
        val possiblyTruncated: Set<String>,
        val windowCapped: Boolean,
    )

    data class SessionWindow(val effectiveFrom: Long, val capped: Boolean)

    internal fun sessionWindow(from: Long, to: Long): SessionWindow {
        require(from <= to) { "session start must not be after its end" }
        val effective = maxOf(from, to - 86_400L)
        return SessionWindow(effective, effective != from)
    }

    /** 18 columns, in the contract order shared with the Swift exporter (band_sleep_state added, #175). */
    private const val HEADER =
        "unix_s,iso_utc,stream,hr_bpm,rr_ms,grav_x,grav_y,grav_z,step_counter,ppg_bpm,ppg_conf," +
            "spo2_red,spo2_ir,skintemp_raw,resp_raw,band_sleep_state,event_kind,event_payload"
    private const val STEPS_SESSION_HEADER =
        "$HEADER,step_activity_class,grav_dyn_accel,step_cadence"

    private val UTC_FMT: java.time.format.DateTimeFormatter =
        java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
            .withZone(java.time.ZoneOffset.UTC)

    private fun iso(epochSeconds: Long): String =
        UTC_FMT.format(java.time.Instant.ofEpochSecond(epochSeconds))

    // Locale-proof Double (always '.'); reuse the exporter's csvField for the one free-text column.
    private fun n(v: Double): String = WhoopCsvExporter.num(v)
    private fun n(v: Int): String = v.toString()
    private fun n(v: Long): String = v.toString()

    /**
     * Read each stream for [deviceId] over [from, to] (inclusive, unix seconds), merge by ts ascending,
     * and STREAM the combined long-format CSV body straight through [out] (CSV header row first). A high
     * per-stream [limit] caps a runaway 24 h window without truncating a normal day. Returns a per-stream
     * count map.
     *
     * Memory: each already-sorted stream is queried and staged to a temporary file separately. A k-way
     * merge then holds one line per stream, so peak heap is one Room result list plus a handful of heads,
     * never every stream's formatted rows at once.
     */
    internal suspend fun writeCsv(
        out: Writer,
        repo: WhoopRepository,
        deviceId: String,
        from: Long,
        to: Long,
        limit: Int = 200_000,
        includeStepDiagnostics: Boolean = false,
        tempDir: File? = null,
    ): Map<String, Int> {
        // index: 0 hr_bpm,1 rr_ms,2 grav_x,3 grav_y,4 grav_z,5 step_counter,6 ppg_bpm,7 ppg_conf,
        //        8 spo2_red,9 spo2_ir,10 skintemp_raw,11 resp_raw,12 band_sleep_state,13 event_kind,14 event_payload
        val counts = LinkedHashMap<String, Int>()
        val cellCount = if (includeStepDiagnostics) 18 else 15
        var streamIndex = 0
        val files = ArrayList<File>()
        suspend fun <T> stage(name: String, load: suspend () -> List<T>, format: (T) -> LineRow) {
            val (file, count) = stageStream(tempDir, streamIndex++, load, format)
            counts[name] = count
            files += file
        }

        try {
            stage("hr", { repo.rawHrSamples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("hr", it.ts, cellCount, 0 to n(it.bpm)))
            }
            stage("rr", { repo.rrIntervals(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("rr", it.ts, cellCount, 1 to n(it.rrMs)))
            }
            stage("gravity", { repo.gravitySamples(deviceId, from, to, limit) }) { s ->
                val csv = if (includeStepDiagnostics && s.dynAccel != null) {
                    line("gravity", s.ts, cellCount, 2 to n(s.x), 3 to n(s.y), 4 to n(s.z), 16 to n(s.dynAccel))
                } else line("gravity", s.ts, cellCount, 2 to n(s.x), 3 to n(s.y), 4 to n(s.z))
                LineRow(s.ts, csv)
            }
            stage("steps", { repo.stepSamples(deviceId, from, to, limit) }) { s ->
                val csv = if (includeStepDiagnostics && s.activityClass != null) {
                    line("steps", s.ts, cellCount, 5 to n(s.counter), 15 to n(s.activityClass))
                } else line("steps", s.ts, cellCount, 5 to n(s.counter))
                LineRow(s.ts, csv)
            }
            if (includeStepDiagnostics) {
                stage("v18aux", { repo.v18AuxSamples(deviceId, from, to, limit) }) { s ->
                    LineRow(
                        s.ts,
                        if (s.stepCadence != null) line("v18aux", s.ts, cellCount, 17 to n(s.stepCadence))
                        else line("v18aux", s.ts, cellCount),
                    )
                }
            }
            stage("ppghr", { repo.ppgHrSamples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("ppghr", it.ts, cellCount, 6 to n(it.bpm), 7 to n(it.conf)))
            }
            stage("spo2", { repo.spo2Samples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("spo2", it.ts, cellCount, 8 to n(it.red), 9 to n(it.ir)))
            }
            stage("skintemp", { repo.skinTempSamples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("skintemp", it.ts, cellCount, 10 to n(it.raw)))
            }
            stage("resp", { repo.respSamples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("resp", it.ts, cellCount, 11 to n(it.raw)))
            }
            stage("band_sleep_state", { repo.sleepStateSamples(deviceId, from, to, limit) }) {
                LineRow(it.ts, line("band_sleep_state", it.ts, cellCount, 12 to n(it.state)))
            }
            stage("event", { repo.events(deviceId, from, to, limit) }) {
                LineRow(
                    it.ts,
                    line(
                        "event", it.ts, cellCount,
                        13 to WhoopCsvExporter.csvField(it.kind),
                        14 to WhoopCsvExporter.csvField(it.payloadJSON),
                    ),
                )
            }

            out.write(if (includeStepDiagnostics) STEPS_SESSION_HEADER else HEADER); out.write("\n")
            val readers = files.map { it.bufferedReader() }
            try {
                mergeSortedCsvStreams(readers, out)
            } finally {
                readers.forEach { runCatching { it.close() } }
            }
            return counts
        } finally {
            files.forEach { runCatching { it.delete() } }
        }
    }

    private suspend fun <T> stageStream(
        tempDir: File?,
        streamIndex: Int,
        load: suspend () -> List<T>,
        format: (T) -> LineRow,
    ): Pair<File, Int> {
        val file = File.createTempFile("noop-raw-$streamIndex-", ".csv", tempDir)
        try {
            val rows = load()
            file.bufferedWriter().use { writer ->
                for (row in rows) {
                    val formatted = format(row)
                    writer.appendLine(encodeStagedLine(formatted.ts, formatted.line))
                }
            }
            return file to rows.size
        } catch (t: Throwable) {
            runCatching { file.delete() }
            throw t
        }
    }

    /** One physical temp-file line even when an RFC-4180 field contains CR/LF. */
    internal fun encodeStagedLine(ts: Long, csvLine: String): String =
        "$ts\t${Base64.getEncoder().encodeToString(csvLine.toByteArray(Charsets.UTF_8))}"

    /** External stable k-way merge: at most one encoded record/head per sorted stream is resident. */
    internal fun mergeSortedCsvStreams(
        inputs: List<Reader>,
        out: Writer,
        onBufferedHeads: (Int) -> Unit = {},
    ) {
        data class Head(val ts: Long, val stream: Int, val ordinal: Long, val line: String)
        val readers = inputs.map { if (it is java.io.BufferedReader) it else it.buffered() }
        val ordinals = LongArray(readers.size)
        val queue = PriorityQueue<Head>(compareBy<Head> { it.ts }.thenBy { it.stream }.thenBy { it.ordinal })
        fun offer(stream: Int) {
            val line = readers[stream].readLine() ?: return
            val ts = line.substringBefore('\t').toLong()
            queue += Head(ts, stream, ordinals[stream]++, line)
            onBufferedHeads(queue.size)
        }
        readers.indices.forEach(::offer)
        while (queue.isNotEmpty()) {
            val head = queue.remove()
            val encoded = head.line.substringAfter('\t')
            out.write(String(Base64.getDecoder().decode(encoded), Charsets.UTF_8)); out.write("\n")
            offer(head.stream)
        }
    }

    /**
     * Stage the DB-backed sensor rows for one control-test session. [from] is the persisted activation
     * instant and [to] is the export tap; forgotten sessions longer than 24 h are capped and explicitly
     * identified by [SessionFile.windowCapped]. A stream that reaches [limit] is likewise named in
     * [SessionFile.possiblyTruncated] instead of silently claiming complete coverage.
     */
    suspend fun writeSessionFile(
        context: Context,
        repo: WhoopRepository,
        deviceId: String,
        from: Long,
        to: Long,
        sessionId: String,
        limit: Int = 200_000,
    ): SessionFile {
        val window = sessionWindow(from, to)
        val effectiveFrom = window.effectiveFrom
        val windowCapped = window.capped
        val dir = File(context.cacheDir, "logs").apply { mkdirs() }
        val file = File(dir, "noop-steps-$sessionId-raw-sensors.csv")
        val counts = file.bufferedWriter().use { w ->
            w.appendLine("# NOOP Steps control-test raw sensor export")
            w.appendLine("# session_id=$sessionId activation_unix_s=$from export_from_unix_s=$effectiveFrom end_unix_s=$to device_id=$deviceId")
            w.appendLine("# Active strap only; maximum window=24h; per-stream limit=$limit; window_capped=$windowCapped.")
            writeCsv(w, repo, deviceId, effectiveFrom, to, limit, includeStepDiagnostics = true, tempDir = dir)
        }
        return SessionFile(file, counts, counts.filterValues { it >= limit }.keys, windowCapped)
    }

    /** ts + the fully-formatted CSV line for one sample (kept small so a whole day fits in memory). */
    private class LineRow(val ts: Long, val line: String)

    /** Build one CSV line: its three identity cells plus [cellCount] stream-specific value cells. */
    private fun line(stream: String, ts: Long, cellCount: Int, vararg set: Pair<Int, String>): String {
        val sb = StringBuilder(96)
        sb.append(ts).append(',').append(iso(ts)).append(',').append(stream)
        for (i in 0 until cellCount) {
            sb.append(',')
            for ((idx, v) in set) if (idx == i) { sb.append(v); break }
        }
        return sb.toString()
    }

    /**
     * Build the last-24 h CSV for the strap source and fire a share sheet (text/csv). Runs the DB read
     * off the main thread; toasts a per-stream summary so the user sees what was captured (and that the
     * deeper 5/MG streams are empty until they've been unlocked). On-device only.
     *
     * [deviceId] is REQUIRED, deliberately. It used to default to the canonical "my-whoop", and the one
     * caller took the default — so after a strap re-add this exported the legacy id's streams rather than
     * the strap the user is actually wearing, silently, in the CSV people attach to bug reports. That is
     * the #172/#175 defect exactly: a defaulted strap id that no caller overrides. Removing the default
     * makes the compiler ask, which is stronger than remembering. (Ported from tanarchytan/noop e4f508f.)
     */
    suspend fun export(context: Context, repo: WhoopRepository, deviceId: String) {
        runCatching {
            val now = System.currentTimeMillis() / 1000
            val dir = File(context.cacheDir, "logs").apply { mkdirs() }
            val file = File(dir, "noop-raw-sensors.csv")
            // Stream straight to disk through an 8 KB buffer — never hold the whole CSV as a String (#406).
            val counts = file.bufferedWriter().use { w ->
                w.append("# NOOP raw sensor export · last 24h · long-format CSV\n")
                w.append("# App: ${BuildConfig.VERSION_NAME} (${BuildConfig.TIER}) · Android ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT}) · ${Build.MANUFACTURER} ${Build.MODEL}\n")
                w.append("# One row per decoded sample; only the row's `stream` columns are filled. Times are UTC.\n")
                writeCsv(w, repo, deviceId, now - 86_400, now)
            }

            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val send = Intent(Intent.ACTION_SEND).apply {
                type = "text/csv"
                putExtra(Intent.EXTRA_STREAM, uri)
                putExtra(Intent.EXTRA_SUBJECT, "NOOP raw sensor export")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            context.startActivity(Intent.createChooser(send, "Export raw sensor data"))

            val total = counts.values.sum()
            val summary = if (total == 0) {
                "No samples in the last 24h - wear the strap and let it sync, then export again."
            } else {
                // Compact "hr 3204 · rr 812 · …" line, only non-empty streams.
                counts.filterValues { it > 0 }.entries.joinToString(" · ") { "${it.key} ${it.value}" }
            }
            Toast.makeText(context, summary, Toast.LENGTH_LONG).show()
        }.onFailure {
            Toast.makeText(context, "Couldn't export sensor data: ${it.message}", Toast.LENGTH_LONG).show()
        }
    }
}
