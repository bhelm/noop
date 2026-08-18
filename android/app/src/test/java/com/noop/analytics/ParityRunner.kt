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
