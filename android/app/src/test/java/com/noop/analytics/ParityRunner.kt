package com.noop.analytics

import com.noop.data.RrInterval
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume
import org.junit.Test
import java.io.File

class ParityRunner {
    @Test
    fun runParityCases() {
        val inputPath = System.getenv("PARITY_INPUT")
        val outputPath = System.getenv("PARITY_OUTPUT")
        if (inputPath == null && outputPath == null) {
            require(System.getenv("PARITY_NEGATIVE_SIDE") == null) {
                "PARITY_NEGATIVE_SIDE is set but PARITY_INPUT/PARITY_OUTPUT are not"
            }
            Assume.assumeTrue("Parity files are provided only for explicit parity runs", false)
            return
        }
        requireNotNull(inputPath) { "PARITY_INPUT is required" }
        requireNotNull(outputPath) { "PARITY_OUTPUT is required" }
        val negativeSide = System.getenv("PARITY_NEGATIVE_SIDE")
        require(negativeSide == null || negativeSide == "swift" || negativeSide == "kotlin") {
            "PARITY_NEGATIVE_SIDE must be swift, kotlin, or unset"
        }

        // Strict decode: the JVM's default replacing decoder would smuggle U+FFFD past the
        // JSON layer where the Swift runner rejects the same bytes outright.
        val inputText = Charsets.UTF_8.newDecoder()
            .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
            .decode(java.nio.ByteBuffer.wrap(File(inputPath).readBytes()))
            .toString()
        require(inputText.endsWith('\n')) { "PARITY_INPUT must end with a newline" }
        val lines = inputText.dropLast(1).split('\n')
        require(lines.isNotEmpty() && lines.none { it.isEmpty() }) {
            "PARITY_INPUT must contain non-blank JSON Lines"
        }
        val records = lines.map(::JSONObject)
        val ids = records.map { it.getString("id") }
        require(ids.toSet().size == ids.size) { "PARITY_INPUT contains duplicate case IDs" }

        val output = buildString {
            for (record in records.sortedBy { it.getString("id") }) {
                append(canonicalJson(evaluate(record, negativeSide)))
                append('\n')
            }
        }
        val outputFile = File(outputPath)
        outputFile.parentFile?.mkdirs()
        outputFile.writeText(output, Charsets.UTF_8)
    }

    private fun evaluate(record: JSONObject, negativeSide: String?): Map<String, Any?> {
        val caseId = record.getString("id")
        val function = record.getString("function")
        val comparison = record.getString("comparison")
        val args = record.getJSONObject("args")
        val effective = record.getJSONObject("effectiveArgs")
        val result = sortedMapOf<String, Any?>(
            "comparison" to comparison,
            "function" to function,
            "id" to caseId,
            "nonce" to record.getString("nonce"),
        )
        when (function) {
            "rmssdRaw" -> {
                require(comparison == "epsilon") { "invalid rmssdRaw case $caseId" }
                result["value"] = finiteOrNull(HrvAnalyzer.rmssdRaw(doubleList(args, "nn")), function, caseId)
            }
            "sdnnRaw" -> {
                require(comparison == "epsilon") { "invalid sdnnRaw case $caseId" }
                result["value"] = finiteOrNull(HrvAnalyzer.sdnnRaw(doubleList(args, "nn")), function, caseId)
            }
            "rangeFilter" -> {
                require(comparison == "exact") { "invalid rangeFilter case $caseId" }
                result["valueBits"] = exactBits(HrvAnalyzer.rangeFilter(doubleList(args, "values")))
            }
            "rejectEctopic" -> {
                require(comparison == "exact") { "invalid rejectEctopic case $caseId" }
                result["valueBits"] = exactBits(HrvAnalyzer.rejectEctopic(doubleList(args, "values")))
            }
            "cleanRR" -> {
                require(comparison == "exact") { "invalid cleanRR case $caseId" }
                result["valueBits"] = exactBits(HrvAnalyzer.cleanRR(doubleList(args, "values")))
            }
            "cleanRRGapAware" -> {
                require(comparison == "exact") { "invalid cleanRRGapAware case $caseId" }
                val clean = HrvAnalyzer.cleanRRGapAware(doubleList(args, "values"))
                result["valueBits"] = sortedMapOf(
                    "contiguous" to clean.contiguous,
                    "nn" to exactBits(clean.nn),
                )
            }
            "rmssdGapAware" -> {
                require(comparison == "epsilon") { "invalid rmssdGapAware case $caseId" }
                val nn = doubleList(args, "nn")
                val contiguous = booleanList(args, "contiguous")
                require(nn.size == contiguous.size) { "invalid rmssdGapAware case $caseId" }
                result["value"] = finiteOrNull(
                    HrvAnalyzer.rmssdGapAware(nn, contiguous), function, caseId,
                )
            }
            "pnn50GapAware" -> {
                require(comparison == "epsilon") { "invalid pnn50GapAware case $caseId" }
                val nn = doubleList(args, "nn")
                val contiguous = booleanList(args, "contiguous")
                require(nn.size == contiguous.size) { "invalid pnn50GapAware case $caseId" }
                result["value"] = finiteOrNull(
                    HrvAnalyzer.pnn50GapAware(nn, contiguous), function, caseId,
                )
            }
            "analyze/3" -> {
                require(comparison == "epsilon") { "invalid analyze/3 case $caseId" }
                val rrJson = args.getJSONArray("rr")
                val rr = (0 until rrJson.length()).map { index ->
                    val item = rrJson.getJSONObject(index)
                    RrInterval(
                        deviceId = "parity",
                        ts = item.getLong("ts"),
                        rrMs = item.getInt("rrMs"),
                    )
                }
                val value = if (!args.has("windowStart") && !args.has("windowEnd")) {
                    HrvAnalyzer.analyze(rr)
                } else {
                    HrvAnalyzer.analyze(
                        rr,
                        args.optLong("windowStart").takeIf { args.has("windowStart") },
                        args.optLong("windowEnd").takeIf { args.has("windowEnd") },
                    )
                }
                result["value"] = sortedMapOf(
                    "meanNN" to finiteOrNull(value.meanNN, function, caseId),
                    "nClean" to value.nClean,
                    "nInput" to value.nInput,
                    "pnn50" to finiteOrNull(value.pnn50, function, caseId),
                    "rmssd" to finiteOrNull(value.rmssd, function, caseId),
                    "sdnn" to finiteOrNull(value.sdnn, function, caseId),
                )
            }
            "HRVAnalyzer.analyze/2=HrvAnalyzer.analyzeRaw/2" -> {
                require(comparison == "epsilon") { "invalid raw analyze case $caseId" }
                val rawRR = doubleList(args, "rawRR")
                val value = if (args.has("maxRejectedFraction")) {
                    HrvAnalyzer.analyzeRaw(rawRR, args.getDouble("maxRejectedFraction"))
                } else {
                    HrvAnalyzer.analyzeRaw(rawRR)
                }
                result["value"] = sortedMapOf(
                    "meanNN" to finiteOrNull(value.meanNN, function, caseId),
                    "nClean" to value.nClean,
                    "nInput" to value.nInput,
                    "pnn50" to finiteOrNull(value.pnn50, function, caseId),
                    "rmssd" to finiteOrNull(value.rmssd, function, caseId),
                    "sdnn" to finiteOrNull(value.sdnn, function, caseId),
                )
            }
            "HRVAnalyzer.median/1=HrvAnalyzer.median/1" -> {
                require(comparison == "exact") { "invalid HRV median case $caseId" }
                result["valueBits"] = java.lang.Long.toHexString(
                    HrvAnalyzer.median(doubleList(args, "values")).toRawBits()
                ).padStart(16, '0')
            }
            "beatSpreadIsTrustworthy" -> {
                require(comparison == "exact") { "invalid beatSpreadIsTrustworthy case $caseId" }
                val raw = args.getString("verdict")
                val verdict = HrvAnalyzer.RrCoverageVerdict.entries.singleOrNull { it.raw == raw }
                requireNotNull(verdict) { "invalid beatSpreadIsTrustworthy case $caseId" }
                result["valueBits"] = HrvAnalyzer.beatSpreadIsTrustworthy(verdict)
            }
            "beatAccurateFraction" -> {
                require(comparison == "epsilon") { "invalid beatAccurateFraction case $caseId" }
                result["value"] = finite(
                    HrvAnalyzer.beatAccurateFraction(longList(args, "tsSec"), doubleList(args, "rrMs")),
                    function,
                    caseId,
                )
            }
            "beatValuesAreTrustworthy" -> {
                require(comparison == "exact") { "invalid beatValuesAreTrustworthy case $caseId" }
                result["valueBits"] = HrvAnalyzer.beatValuesAreTrustworthy(args.getDouble("fraction"))
            }
            "classifyCoverage" -> {
                require(comparison == "exact") { "invalid classifyCoverage case $caseId" }
                val verdict = HrvAnalyzer.classifyCoverage(
                    args.getDouble("coverage"), args.getDouble("collapsed"),
                )
                result["valueBits"] = sortedMapOf("text" to verdict.raw)
            }
            "rrCoverage" -> {
                require(comparison == "epsilon") { "invalid rrCoverage case $caseId" }
                result["value"] = finite(
                    HrvAnalyzer.rrCoverage(longList(args, "tsSec"), doubleList(args, "rrMs")),
                    function,
                    caseId,
                )
            }
            "duplicateBeatCount" -> {
                require(comparison == "exact") { "invalid duplicateBeatCount case $caseId" }
                result["valueBits"] = HrvAnalyzer.duplicateBeatCount(
                    longList(args, "tsSec"), doubleList(args, "rrMs"),
                )
            }
            "collapseOverCount" -> {
                require(comparison == "exact") { "invalid collapseOverCount case $caseId" }
                val ts = longList(args, "tsSec")
                val rr = doubleList(args, "rrMs")
                val collapsed = if (!args.has("rrTolMs") && !args.has("windowSec")) {
                    HrvAnalyzer.collapseOverCount(ts, rr)
                } else {
                    HrvAnalyzer.collapseOverCount(
                        ts,
                        rr,
                        effective.getDouble("rrTolMs"),
                        effective.getLong("windowSec"),
                    )
                }
                result["valueBits"] = sortedMapOf(
                    "rrMs" to exactBits(collapsed.second),
                    "tsSec" to collapsed.first,
                )
            }
            "collapsedCoverage" -> {
                require(comparison == "epsilon") { "invalid collapsedCoverage case $caseId" }
                val ts = longList(args, "tsSec")
                val rr = doubleList(args, "rrMs")
                val value = if (args.has("rrTolMs")) {
                    HrvAnalyzer.collapsedCoverage(ts, rr, effective.getDouble("rrTolMs"))
                } else {
                    HrvAnalyzer.collapsedCoverage(ts, rr)
                }
                result["value"] = finite(value, function, caseId)
            }
            "densestSecondWindowSample" -> {
                require(comparison == "exact") { "invalid densestSecondWindowSample case $caseId" }
                val ts = longList(args, "tsSec")
                val rr = doubleList(args, "rrMs")
                val src = nullableIntList(args, "srcCodes")
                val value = if (!args.has("halfWindowSec") && !args.has("maxRowsPerSecond")) {
                    HrvAnalyzer.densestSecondWindowSample(ts, rr, src)
                } else {
                    HrvAnalyzer.densestSecondWindowSample(
                        ts,
                        rr,
                        src,
                        effective.getInt("halfWindowSec"),
                        effective.getInt("maxRowsPerSecond"),
                    )
                }
                result["valueBits"] = sortedMapOf("text" to value)
            }
            "rollingRmssd" -> {
                require(comparison == "epsilon") { "invalid rollingRmssd case $caseId" }
                val rrJson = args.getJSONArray("rr")
                val rr = (0 until rrJson.length()).map { index ->
                    val item = rrJson.getJSONObject(index)
                    RrInterval(
                        deviceId = "parity",
                        ts = item.getLong("ts"),
                        rrMs = item.getInt("rrMs"),
                    )
                }
                val stepSec = effective.getInt("stepSec")
                val minBeats = effective.getInt("minBeatsPerWindow")
                val windowSec = effective.getInt("windowSec")
                val points = if (!args.has("windowSec") && !args.has("stepSec") &&
                    !args.has("minBeatsPerWindow")
                ) {
                    // Bare case: every optional argument omitted, so the language's own default
                    // expressions execute and the cross-comparison itself checks default parity.
                    HrvAnalyzer.rollingRmssd(rr = rr)
                } else if (args.has("windowSec")) {
                    HrvAnalyzer.rollingRmssd(
                        rr = rr,
                        windowSec = windowSec,
                        stepSec = stepSec,
                        minBeatsPerWindow = minBeats,
                    )
                } else {
                    require(windowSec == HrvAnalyzer.DEFAULT_ROLLING_WINDOW_SEC) {
                        "generated default tuple disagrees with Kotlin for $caseId"
                    }
                    HrvAnalyzer.rollingRmssd(
                        rr = rr,
                        stepSec = stepSec,
                        minBeatsPerWindow = minBeats,
                    )
                }
                result["value"] = points.map { (ts, rmssd) ->
                    require(rmssd.isFinite()) { "rollingRmssd returned a non-finite value for $caseId" }
                    sortedMapOf<String, Any?>("rmssd" to rmssd, "ts" to ts.toString())
                }
            }
            "trimpToStrain" -> {
                require(comparison == "exact") { "invalid trimpToStrain case $caseId" }
                val trimp = args.getDouble("trimp")
                val value = if (args.has("denominator")) {
                    StrainScorer.trimpToStrain(trimp, args.getDouble("denominator"))
                } else {
                    require(effective.getDouble("denominator") == StrainScorer.strainDenominator) {
                        "generated default tuple disagrees with Kotlin for $caseId"
                    }
                    StrainScorer.trimpToStrain(trimp)
                }
                val emitted = if (negativeSide == "kotlin" && caseId == "trimp_negative_probe") {
                    result["negativeSide"] = "kotlin"
                    value + 1e-6
                } else {
                    value
                }
                require(emitted.isFinite()) { "trimpToStrain returned a non-finite value for $caseId" }
                result["valueBits"] = java.lang.Long.toHexString(emitted.toRawBits()).padStart(16, '0')
            }
            else -> error("unsupported parity function $function")
        }
        return result
    }

    private fun doubleList(objectValue: JSONObject, key: String): List<Double> {
        val array = objectValue.getJSONArray(key)
        return (0 until array.length()).map { index -> array.getDouble(index) }
    }

    private fun longList(objectValue: JSONObject, key: String): List<Long> {
        val array = objectValue.getJSONArray(key)
        return (0 until array.length()).map { index -> array.getLong(index) }
    }

    private fun booleanList(objectValue: JSONObject, key: String): List<Boolean> {
        val array = objectValue.getJSONArray(key)
        return (0 until array.length()).map { index -> array.getBoolean(index) }
    }

    private fun nullableIntList(objectValue: JSONObject, key: String): List<Int?> {
        val array = objectValue.getJSONArray(key)
        return (0 until array.length()).map { index ->
            if (array.isNull(index)) null else array.getInt(index)
        }
    }

    private fun exactBits(values: List<Double>): List<String> =
        values.map { java.lang.Long.toHexString(it.toRawBits()).padStart(16, '0') }

    private fun finite(value: Double, function: String, caseId: String): Double {
        require(value.isFinite()) { "$function returned a non-finite value for $caseId" }
        return value
    }

    private fun finiteOrNull(value: Double?, function: String, caseId: String): Double? =
        value?.let { finite(it, function, caseId) }

    private fun canonicalJson(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is String -> JSONObject.quote(value)
        is Boolean -> value.toString()
        is Number -> {
            val finite = when (value) {
                is Double -> value.isFinite()
                is Float -> value.isFinite()
                else -> true
            }
            require(finite) { "JSON numbers must be finite" }
            JSONObject.numberToString(value)
        }
        is Map<*, *> -> value.entries
            .sortedBy { it.key as String }
            .joinToString(prefix = "{", postfix = "}", separator = ",") { (key, item) ->
                JSONObject.quote(key as String) + ":" + canonicalJson(item)
            }
        is List<*> -> value.joinToString(prefix = "[", postfix = "]", separator = ",") {
            canonicalJson(it)
        }
        is JSONObject -> value.keys().asSequence().toList().sorted()
            .joinToString(prefix = "{", postfix = "}", separator = ",") { key ->
                JSONObject.quote(key) + ":" + canonicalJson(value.get(key))
            }
        is JSONArray -> (0 until value.length())
            .joinToString(prefix = "[", postfix = "]", separator = ",") { index ->
                canonicalJson(value.get(index))
            }
        else -> error("unsupported JSON value ${value::class.java.name}")
    }
}
