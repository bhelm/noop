package com.noop.analytics

import com.noop.data.RrInterval
import com.noop.data.HrSample
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
        val dispatchFunction = if (function == "trimpToStrain") {
            "StrainScorer.trimpToStrain/2"
        } else {
            function
        }
        when (dispatchFunction) {
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
            "RecoveryScorer.parasympatheticSaturation/2" -> {
                require(comparison == "epsilon") { "invalid parasympatheticSaturation case $caseId" }
                val value = RecoveryScorer.parasympatheticSaturation(
                    args.getDouble("hrvZ"), nullableDouble(args, "rhrZ"),
                )
                val encoded = sortedMapOf<String, Any?>(
                    "active" to value.active,
                    "dampFraction" to finite(value.dampFraction, function, caseId),
                    "easedHrvZ" to finite(value.easedHrvZ, function, caseId),
                )
                if (args.optBoolean("characterizeRecoveryConstants", false)) {
                    encoded["constants"] = recoveryConstants()
                }
                result["value"] = encoded
            }
            "RecoveryScorer.restingHR/3" -> {
                require(comparison == "exact") { "invalid restingHR case $caseId" }
                result["valueBits"] = RecoveryScorer.restingHR(
                    hrSamples(args), args.getLong("start"), args.getLong("end"),
                )
            }
            "HeartRateRecovery.calculate/4" -> {
                require(comparison == "exact") { "invalid HeartRateRecovery.calculate/4 case $caseId" }
                var value = HeartRateRecovery.calculate(
                    hrSamples(args, "samples"), args.getLong("workoutStart"),
                    args.getLong("workoutEnd"), args.getDouble("maxHR"),
                )
                if (negativeSide == "kotlin" && caseId == "heart_rate_recovery_negative_probe") {
                    val current = requireNotNull(value)
                    value = current.copy(after1Minute = current.after1Minute?.plus(1))
                    result["negativeSide"] = "kotlin"
                }
                result["valueBits"] = value?.let {
                    sortedMapOf(
                        "endHR" to it.endHr,
                        "after1Minute" to it.after1Minute,
                        "after2Minutes" to it.after2Minutes,
                        "after5Minutes" to it.after5Minutes,
                    )
                }
            }
            "RecoveryScorer.recoveryIndexSlope/3" -> {
                require(comparison == "epsilon") { "invalid recoveryIndexSlope case $caseId" }
                result["value"] = finiteOrNull(
                    RecoveryScorer.recoveryIndexSlope(
                        hrSamples(args), args.getLong("start"), args.getLong("end"),
                    ), function, caseId,
                )
            }
            "RecoveryScorer.band/1" -> {
                require(comparison == "exact") { "invalid recovery band case $caseId" }
                var value = RecoveryScorer.band(args.getDouble("score"))
                if (negativeSide == "kotlin" && caseId == "recovery_negative_band_probe") {
                    value += "-mutant"
                    result["negativeSide"] = "kotlin"
                }
                result["valueBits"] = sortedMapOf("text" to value)
            }
            "RecoveryScorer.zScore/3" -> {
                require(comparison == "epsilon") { "invalid recovery zScore case $caseId" }
                result["value"] = finite(
                    RecoveryScorer.zScore(
                        args.getDouble("value"), args.getDouble("mean"), args.getDouble("spread"),
                    ), function, caseId,
                )
            }
            "RecoveryScorer.recovery/12" -> {
                require(comparison == "exact") { "invalid driver recovery case $caseId" }
                val value = if (args.getBoolean("useDefaults")) {
                    RecoveryScorer.recovery(
                        hrv = args.getDouble("hrv"), rhr = args.getDouble("rhr"),
                        resp = nullableDouble(args, "resp"),
                        hrvBaseline = driverBaseline(args, "hrvBaseline"),
                        rhrBaseline = driverBaseline(args, "rhrBaseline"),
                        respBaseline = driverBaseline(args, "respBaseline"),
                        sleepPerf = nullableDouble(args, "sleepPerf"),
                    )
                } else {
                    RecoveryScorer.recovery(
                        hrv = effective.getDouble("hrv"), rhr = effective.getDouble("rhr"),
                        resp = nullableDouble(effective, "resp"),
                        hrvBaseline = driverBaseline(effective, "hrvBaseline"),
                        rhrBaseline = driverBaseline(effective, "rhrBaseline"),
                        respBaseline = driverBaseline(effective, "respBaseline"),
                        sleepPerf = nullableDouble(effective, "sleepPerf"),
                        skinTempDev = nullableDouble(effective, "skinTempDev"),
                        hrvBaselineUsable = effective.getBoolean("hrvBaselineUsable"),
                        recoveryIndexSlope = nullableDouble(effective, "recoveryIndexSlope"),
                        effortBaseline = driverBaseline(effective, "effortBaseline"),
                        priorDayEffort = nullableDouble(effective, "priorDayEffort"),
                    )
                }
                result["valueBits"] = value?.let(::exactBit)
            }
            "RecoveryScorer.logisticScore/1" -> {
                require(comparison == "epsilon") { "invalid logisticScore case $caseId" }
                var value = RecoveryScorer.logisticScore(args.getDouble("compositeZ"))
                if (negativeSide == "kotlin" && caseId == "recovery_negative_logistic_probe") {
                    value += 1e-6
                    result["negativeSide"] = "kotlin"
                }
                result["value"] = finite(value, function, caseId)
            }
            "RecoveryScorer.recovery/11" -> {
                require(comparison == "exact") { "invalid baseline-state recovery case $caseId" }
                val value = if (args.getBoolean("useDefaults")) {
                    RecoveryScorer.recovery(
                        hrv = args.getDouble("hrv"), rhr = args.getDouble("rhr"),
                        resp = nullableDouble(args, "resp"),
                        hrvBaseline = baselineState(args.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(args, "rhrBaseline"),
                        respBaseline = baselineStateOptional(args, "respBaseline"),
                        sleepPerf = nullableDouble(args, "sleepPerf"),
                    )
                } else {
                    RecoveryScorer.recovery(
                        hrv = effective.getDouble("hrv"), rhr = effective.getDouble("rhr"),
                        resp = nullableDouble(effective, "resp"),
                        hrvBaseline = baselineState(effective.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(effective, "rhrBaseline"),
                        respBaseline = baselineStateOptional(effective, "respBaseline"),
                        sleepPerf = nullableDouble(effective, "sleepPerf"),
                        skinTempDev = nullableDouble(effective, "skinTempDev"),
                        recoveryIndexSlope = nullableDouble(effective, "recoveryIndexSlope"),
                        effortBaseline = baselineStateOptional(effective, "effortBaseline"),
                        priorDayEffort = nullableDouble(effective, "priorDayEffort"),
                    )
                }
                result["valueBits"] = value?.let(::exactBit)
            }
            "RecoveryScorer.chargeDrivers/8=RecoveryDrivers.chargeDrivers/8" -> {
                require(comparison == "exact") { "invalid chargeDrivers case $caseId" }
                val value = if (args.getBoolean("useDefaults")) {
                    RecoveryDrivers.chargeDrivers(
                        hrv = args.getDouble("hrv"),
                        rhr = args.getDouble("rhr"),
                        resp = nullableDouble(args, "resp"),
                        hrvBaseline = baselineState(args.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(args, "rhrBaseline"),
                        respBaseline = baselineStateOptional(args, "respBaseline"),
                        sleepPerf = nullableDouble(args, "sleepPerf"),
                    )
                } else {
                    RecoveryDrivers.chargeDrivers(
                        hrv = args.getDouble("hrv"),
                        rhr = args.getDouble("rhr"),
                        resp = nullableDouble(effective, "resp"),
                        hrvBaseline = baselineState(args.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(effective, "rhrBaseline"),
                        respBaseline = baselineStateOptional(effective, "respBaseline"),
                        sleepPerf = nullableDouble(effective, "sleepPerf"),
                        skinTempDev = nullableDouble(effective, "skinTempDev"),
                    )
                }
                val encoded = value.map { driver ->
                    sortedMapOf<String, Any>(
                        "baselineText" to sortedMapOf("text" to driver.baselineText),
                        "deltaPoints" to driver.deltaPoints,
                        "label" to sortedMapOf("text" to driver.label),
                        "valueText" to sortedMapOf("text" to driver.valueText),
                        "verdict" to sortedMapOf("text" to driver.verdict),
                    )
                }.toMutableList()
                if (negativeSide == "kotlin" && caseId == "recovery_drivers_negative_delta_probe" &&
                    encoded.isNotEmpty()
                ) {
                    encoded[0]["deltaPoints"] = (encoded[0]["deltaPoints"] as Int) + 1
                    result["negativeSide"] = "kotlin"
                }
                if (negativeSide == "kotlin" && caseId == "recovery_drivers_negative_order_probe" &&
                    encoded.size >= 2
                ) {
                    java.util.Collections.swap(encoded, 0, 1)
                    result["negativeSide"] = "kotlin"
                }
                result["valueBits"] = encoded
            }
            "RecoveryScorer.recoveryTrace/8=RecoveryScorerTrace.recoveryTrace/8" -> {
                require(comparison == "exact") { "invalid recoveryTrace case $caseId" }
                val value = if (args.getBoolean("useDefaults")) {
                    RecoveryScorerTrace.recoveryTrace(
                        hrv = args.getDouble("hrv"), rhr = args.getDouble("rhr"),
                        resp = nullableDouble(args, "resp"),
                        hrvBaseline = baselineState(args.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(args, "rhrBaseline"),
                        respBaseline = baselineStateOptional(args, "respBaseline"),
                        sleepPerf = nullableDouble(args, "sleepPerf"),
                    )
                } else {
                    RecoveryScorerTrace.recoveryTrace(
                        hrv = effective.getDouble("hrv"), rhr = effective.getDouble("rhr"),
                        resp = nullableDouble(effective, "resp"),
                        hrvBaseline = baselineState(effective.getJSONObject("hrvBaseline")),
                        rhrBaseline = baselineStateOptional(effective, "rhrBaseline"),
                        respBaseline = baselineStateOptional(effective, "respBaseline"),
                        sleepPerf = nullableDouble(effective, "sleepPerf"),
                        skinTempDev = nullableDouble(effective, "skinTempDev"),
                    )
                }
                var emittedScore = value.first
                val emittedTrace = value.second.toMutableList()
                if (negativeSide == "kotlin" && caseId == "recovery_trace_negative_score_probe") {
                    emittedScore = requireNotNull(emittedScore) + 1e-6
                    result["negativeSide"] = "kotlin"
                }
                if (negativeSide == "kotlin" && caseId == "recovery_trace_negative_line_probe") {
                    emittedTrace[0] += " [mutant]"
                    result["negativeSide"] = "kotlin"
                }
                result["valueBits"] = sortedMapOf(
                    "score" to emittedScore?.let(::exactBit),
                    "trace" to emittedTrace.map { sortedMapOf("text" to it) },
                )
            }
            "RecoveryForecaster.forecast/6" -> {
                require(comparison == "exact") { "invalid RecoveryForecast case $caseId" }
                val value = if (args.getBoolean("useDefaults")) {
                    RecoveryForecaster.forecast(
                        recentCharge = doubleList(args, "recentCharge"),
                        todayEffort = nullableDouble(args, "todayEffort"),
                        plannedSleepHours = args.getDouble("plannedSleepHours"),
                    )
                } else {
                    RecoveryForecaster.forecast(
                        recentCharge = doubleList(args, "recentCharge"),
                        recentEffort = doubleList(effective, "recentEffort"),
                        todayEffort = nullableDouble(args, "todayEffort"),
                        plannedSleepHours = args.getDouble("plannedSleepHours"),
                        needHours = nullableDouble(effective, "needHours"),
                        needNights = effective.getInt("needNights"),
                    )
                }
                var encoded: Map<String, Any>? = value?.let(::recoveryForecastBits)
                if (negativeSide == "kotlin" && caseId == "recovery_forecast_negative_output_probe") {
                    encoded = requireNotNull(encoded).toMutableMap().also { it["low"] = exactBit(1.0) }
                    result["negativeSide"] = "kotlin"
                }
                if (encoded != null && args.optBoolean("characterizeForecastConstants", false)) {
                    encoded = encoded.toMutableMap().also { it["constants"] = recoveryForecastConstants() }
                }
                result["valueBits"] = encoded
            }
            "RecoveryForecaster.mean/1" -> {
                require(comparison == "epsilon") { "invalid RecoveryForecast mean case $caseId" }
                val values = doubleList(args, "values").toMutableList()
                if (negativeSide == "kotlin" && caseId == "recovery_forecast_negative_source_probe") {
                    values += 100.0
                    result["negativeSide"] = "kotlin"
                }
                result["value"] = finite(RecoveryForecaster.mean(values), function, caseId)
            }
            "RecoveryForecaster.sampleSD/1" -> {
                require(comparison == "epsilon") { "invalid RecoveryForecast sampleSD case $caseId" }
                result["value"] = finite(
                    RecoveryForecaster.sampleSD(doubleList(args, "values")), function, caseId,
                )
            }
            "RecoveryForecaster.leastSquaresSlope/1" -> {
                require(comparison == "epsilon") { "invalid RecoveryForecast slope case $caseId" }
                result["value"] = finite(
                    RecoveryForecaster.leastSquaresSlope(doubleList(args, "values")), function, caseId,
                )
            }
            "RecoveryForecaster.clamp/3" -> {
                require(comparison == "exact") { "invalid RecoveryForecast clamp case $caseId" }
                result["valueBits"] = exactBit(
                    RecoveryForecaster.clamp(
                        args.getDouble("x"), args.getDouble("lo"), args.getDouble("hi"),
                    )
                )
            }
            "WatchRecovery.compute/4" -> {
                require(comparison == "exact") { "invalid WatchRecovery.compute/4 case $caseId" }
                val value = WatchRecovery.compute(
                    todayHrv = nullableDouble(args, "todayHrv"),
                    todayRhr = if (args.isNull("todayRhr")) null else args.getInt("todayRhr"),
                    hrvHistory = doubleList(args, "hrvHistory"),
                    rhrHistory = doubleList(args, "rhrHistory"),
                )
                val encoded = sortedMapOf<String, Any?>(
                    "recovery" to value.recovery?.let(::exactBit),
                    "confidence" to sortedMapOf("text" to value.confidence.raw),
                    "minBaselineNights" to WatchRecovery.minBaselineNights,
                )
                if (negativeSide == "kotlin" && caseId == "watch_recovery_negative_score_probe") {
                    encoded["recovery"] = exactBit((value.recovery ?: 0.0) + 1.0)
                    result["negativeSide"] = "kotlin"
                }
                if (negativeSide == "kotlin" && caseId == "watch_recovery_negative_confidence_probe") {
                    encoded["confidence"] = sortedMapOf("text" to "calibrating")
                    result["negativeSide"] = "kotlin"
                }
                result["valueBits"] = encoded
            }
            "StrainScorer.trimpToStrain/2" -> {
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
            "StrainScorer.tanakaHRmax/1" -> {
                require(comparison == "epsilon") { "invalid tanakaHRmax case $caseId" }
                result["value"] = finite(StrainScorer.tanakaHRmax(args.getDouble("age")), function, caseId)
            }
            "StrainScorer.defaultMaxHR/1" -> {
                require(comparison == "exact") { "invalid defaultMaxHR case $caseId" }
                val effectiveAge = effective.getInt("ageInt")
                val value = if (args.has("ageInt")) {
                    StrainScorer.defaultMaxHR(args.getInt("ageInt"))
                } else {
                    StrainScorer.defaultMaxHR()
                }
                require(value == 220 - effectiveAge) { "defaultMaxHR effective args disagree for $caseId" }
                result["valueBits"] = value
            }
            "StrainScorer.percentile/2" -> {
                require(comparison == "epsilon") { "invalid percentile case $caseId" }
                result["value"] = finite(
                    StrainScorer.percentile(doubleList(args, "values"), args.getDouble("pct")),
                    function,
                    caseId,
                )
            }
            "StrainScorer.estimateHRmax/2" -> {
                require(comparison == "epsilon") { "invalid estimateHRmax case $caseId" }
                val history = expandedHistory(args.getJSONObject("history"))
                val value = StrainScorer.estimateHRmax(
                    history,
                    args.optDouble("age").takeIf { args.has("age") },
                )
                result["value"] = sortedMapOf(
                    "hrmax" to finite(value.first, function, caseId),
                    "source" to value.second,
                )
            }
            "StrainScorer.pctHRR/3" -> {
                require(comparison == "epsilon") { "invalid pctHRR case $caseId" }
                result["value"] = finite(
                    StrainScorer.pctHRR(
                        args.getDouble("bpm"), args.getDouble("restingHR"), args.getDouble("hrReserve"),
                    ),
                    function,
                    caseId,
                )
            }
            "StrainScorer.zoneWeight/3" -> {
                require(comparison == "exact") { "invalid zoneWeight case $caseId" }
                val weight = StrainScorer.zoneWeight(
                    args.getDouble("bpm"), args.getDouble("restingHR"), args.getDouble("hrReserve"),
                )
                result["valueBits"] = if (args.optBoolean("characterizeZones", false)) {
                    sortedMapOf<String, Any?>(
                        "weight" to weight,
                        "zones" to StrainScorer.edwardsZones.map { (threshold, zoneWeight) ->
                            sortedMapOf<String, Any?>(
                                "threshold" to java.lang.Long.toHexString(threshold.toRawBits()).padStart(16, '0'),
                                "weight" to zoneWeight,
                            )
                        },
                    )
                } else {
                    weight
                }
            }
            "StrainScorer.effectiveEffort/2" -> {
                require(comparison == "exact") { "invalid effectiveEffort case $caseId" }
                val value = StrainScorer.effectiveEffort(
                    args.optDouble("live").takeIf { args.has("live") },
                    args.optDouble("stored").takeIf { args.has("stored") },
                )
                result["valueBits"] = value?.let(::exactBit)
            }
            "StrainScorer.sampleDurationMinutes/1" -> {
                require(comparison == "epsilon") { "invalid sampleDurationMinutes case $caseId" }
                result["value"] = finite(
                    StrainScorer.sampleDurationMinutes(hrSamples(args)), function, caseId,
                )
            }
            "StrainScorer.sampleDurationsMinutes/1" -> {
                require(comparison == "epsilon") { "invalid sampleDurationsMinutes case $caseId" }
                result["value"] = StrainScorer.sampleDurationsMinutes(hrSamples(args)).map {
                    finite(it, function, caseId)
                }
            }
            "StrainScorer.edwardsTRIMP/4" -> {
                require(comparison == "epsilon") { "invalid edwardsTRIMP case $caseId" }
                val hr = hrSamples(args)
                val durations = doubleList(args, "durations")
                require(hr.size == durations.size) { "invalid edwardsTRIMP case $caseId" }
                result["value"] = finite(
                    StrainScorer.edwardsTRIMP(
                        hr, args.getDouble("restingHR"), args.getDouble("hrReserve"), durations,
                    ),
                    function,
                    caseId,
                )
            }
            "StrainScorer.banisterTRIMP/5" -> {
                require(comparison == "epsilon") { "invalid banisterTRIMP case $caseId" }
                val hr = hrSamples(args)
                val durations = doubleList(args, "durations")
                require(hr.size == durations.size) { "invalid banisterTRIMP case $caseId" }
                result["value"] = finite(
                    StrainScorer.banisterTRIMP(
                        hr, args.getDouble("restingHR"), args.getDouble("hrReserve"),
                        durations, args.getDouble("b"),
                    ),
                    function,
                    caseId,
                )
            }
            "StrainScorer.fitStrainDenominator/1" -> {
                require(comparison == "epsilon") { "invalid fitStrainDenominator case $caseId" }
                val rawPairs = args.getJSONArray("pairs")
                val pairs = (0 until rawPairs.length()).map { index ->
                    val pair = rawPairs.getJSONArray(index)
                    require(pair.length() == 2) { "invalid fitStrainDenominator case $caseId" }
                    pair.getDouble(0) to pair.getDouble(1)
                }
                try {
                    result["value"] = finite(
                        StrainScorer.fitStrainDenominator(pairs), function, caseId,
                    )
                } catch (error: StrainScorer.StrainException) {
                    result["error"] = when (error.error) {
                        StrainScorer.StrainError.TOO_FEW_PAIRS -> "tooFewPairs"
                        StrainScorer.StrainError.DEGENERATE -> "degenerate"
                    }
                }
            }
            "StrainScorer.strain/6" -> {
                require(comparison == "exact") { "invalid strain case $caseId" }
                val calls = args.getJSONArray("strainCalls")
                val effectiveCalls = effective.getJSONArray("strainCalls")
                require(calls.length() == effectiveCalls.length()) { "invalid strain case $caseId" }
                val encoded = (0 until calls.length()).map { index ->
                    val call = calls.getJSONObject(index)
                    val effectiveCall = effectiveCalls.getJSONObject(index)
                    val hr = expandedHRSeries(call.getJSONObject("series"))
                    val value = if (call.getBoolean("useDefaults")) {
                        StrainScorer.strain(hr)
                    } else {
                        val methodRaw = effectiveCall.getString("method")
                        val method = if (methodRaw == "edwards") {
                            StrainScorer.Method.EDWARDS
                        } else if (methodRaw == "banister") {
                            StrainScorer.Method.BANISTER
                        } else {
                            error("invalid strain method $caseId")
                        }
                        StrainScorer.strain(
                            hr = hr,
                            maxHR = effectiveCall.getDouble("maxHR"),
                            restingHR = effectiveCall.getDouble("restingHR"),
                            method = method,
                            sex = effectiveCall.getString("sex"),
                            denominator = effectiveCall.getDouble("denominator"),
                        )
                    }
                    value?.also {
                        require(it.isFinite()) { "strain returned non-finite for $caseId" }
                    }?.let(::exactBit)
                }
                if (effective.getBoolean("replayFirstAtEnd")) {
                    require(encoded.first() == encoded.last()) {
                        "strain A→B→A replay changed result for $caseId"
                    }
                }
                result["valueBits"] = encoded
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

    private fun exactBit(value: Double): String =
        java.lang.Long.toHexString(value.toRawBits()).padStart(16, '0')

    private fun nullableDouble(value: JSONObject, key: String): Double? =
        if (!value.has(key) || value.isNull(key)) null else value.getDouble(key)

    private fun driverBaseline(value: JSONObject, key: String): RecoveryScorer.DriverBaseline? {
        if (!value.has(key) || value.isNull(key)) return null
        val baseline = value.getJSONObject(key)
        return RecoveryScorer.DriverBaseline(
            mean = baseline.getDouble("mean"), spread = baseline.getDouble("spread"),
        )
    }

    private fun baselineStateOptional(value: JSONObject, key: String): BaselineState? =
        if (!value.has(key) || value.isNull(key)) null else baselineState(value.getJSONObject(key))

    private fun baselineState(value: JSONObject): BaselineState {
        val status = BaselineStatus.entries.single { it.raw == value.getString("status") }
        return BaselineState(
            baseline = value.getDouble("baseline"), spread = value.getDouble("spread"),
            nValid = value.getInt("nValid"), nightsSinceUpdate = value.getInt("nightsSinceUpdate"),
            status = status,
        )
    }

    private fun recoveryConstants(): Map<String, Any> = sortedMapOf(
        "bandRedMax" to exactBit(RecoveryScorer.bandRedMax),
        "bandYellowMax" to exactBit(RecoveryScorer.bandYellowMax),
        "logisticK" to exactBit(RecoveryScorer.logisticK),
        "logisticZ0" to exactBit(RecoveryScorer.logisticZ0),
        "populationMean" to exactBit(RecoveryScorer.populationMean),
        "recoveryIndexMinBins" to RecoveryScorer.recoveryIndexMinBins,
        "recoveryIndexScaleBpmPerHr" to exactBit(RecoveryScorer.recoveryIndexScaleBpmPerHr),
        "restingHRMinBinSamples" to RecoveryScorer.restingHRMinBinSamples,
        "restingHRMinPlausibleBpm" to exactBit(RecoveryScorer.restingHRMinPlausibleBpm),
        "restingHRWindowS" to RecoveryScorer.restingHRWindowS,
        "satEnterZ" to exactBit(RecoveryScorer.satEnterZ),
        "satFullZ" to exactBit(RecoveryScorer.satFullZ),
        "satMaxDampFraction" to exactBit(RecoveryScorer.satMaxDampFraction),
        "skinTempScale" to exactBit(RecoveryScorer.skinTempDevScale),
        "sleepPerfCenter" to exactBit(RecoveryScorer.sleepPerfCenter),
        "sleepPerfScale" to exactBit(RecoveryScorer.sleepPerfScale),
        "wActivityBalance" to exactBit(RecoveryScorer.wActivityBalance),
        "wHRV" to exactBit(RecoveryScorer.wHRV),
        "wRHR" to exactBit(RecoveryScorer.wRHR),
        "wRecoveryIndex" to exactBit(RecoveryScorer.wRecoveryIndex),
        "wResp" to exactBit(RecoveryScorer.wResp),
        "wSkinTemp" to exactBit(RecoveryScorer.wSkinTemp),
        "wSleep" to exactBit(RecoveryScorer.wSleep),
    )

    private fun recoveryForecastBits(value: RecoveryForecast): Map<String, Any> = sortedMapOf(
        "band" to exactBit(value.band),
        "baseline" to exactBit(value.baseline),
        "confidence" to sortedMapOf("text" to value.confidence.raw),
        "high" to exactBit(value.high),
        "low" to exactBit(value.low),
        "need" to exactBit(value.needHours),
        "nights" to value.nights,
        "planned" to exactBit(value.plannedSleepHours),
        "score" to exactBit(value.charge),
    )

    private fun recoveryForecastConstants(): Map<String, Any> = sortedMapOf(
        "baselineWindow" to RecoveryForecaster.baselineWindow,
        "defaultNeedHours" to exactBit(RecoveryForecaster.defaultNeedHours),
        "effortSpread" to exactBit(RecoveryForecaster.effortSpread),
        "effortWindow" to RecoveryForecaster.effortWindow,
        "minBandPoints" to exactBit(RecoveryForecaster.minBandPoints),
        "minBaselineNights" to RecoveryForecaster.minBaselineNights,
        "reversionAdjCap" to exactBit(RecoveryForecaster.reversionAdjCap),
        "reversionWeight" to exactBit(RecoveryForecaster.reversionWeight),
        "sleepOverCap" to exactBit(RecoveryForecaster.sleepOverCap),
        "sleepWeight" to exactBit(RecoveryForecaster.sleepWeight),
        "solidNeedNights" to RecoveryForecaster.solidNeedNights,
        "strainAdjCap" to exactBit(RecoveryForecaster.strainAdjCap),
        "strainWeight" to exactBit(RecoveryForecaster.strainWeight),
        "thinBandPoints" to exactBit(RecoveryForecaster.thinBandPoints),
        "trustedNights" to RecoveryForecaster.trustedNights,
    )

    private fun hrSamples(args: JSONObject, key: String = "hr"): List<HrSample> {
        val rows = args.getJSONArray(key)
        return (0 until rows.length()).map { index ->
            val row = rows.getJSONObject(index)
            HrSample(deviceId = "parity", ts = row.getLong("ts"), bpm = row.getInt("bpm"))
        }
    }

    private fun expandedHistory(input: JSONObject): List<Double> {
        val count = input.getInt("count")
        require(count >= 0) { "history.count must be non-negative" }
        if (count == 0) return emptyList()
        val low = input.getDouble("low")
        val high = input.getDouble("high")
        if (count == 1) return listOf(high)
        return (0 until count).map { index ->
            low + index.toDouble() * (high - low) / (count - 1).toDouble()
        }
    }

    private fun expandedHRSeries(input: JSONObject): List<HrSample> {
        val count = input.getInt("count")
        if (count <= 0) return emptyList()
        val start = input.getLong("startTs")
        val step = input.getLong("stepSec")
        val bpm = input.getInt("bpm")
        val alternate = input.optInt("alternateBpm").takeIf { input.has("alternateBpm") } ?: bpm
        return (0 until count).map { index ->
            HrSample(
                deviceId = "parity",
                ts = if (index == count - 1 && input.has("finalTs")) {
                    input.getLong("finalTs")
                } else {
                    start + index.toLong() * step
                },
                bpm = if (index % 2 == 0) bpm else alternate,
            )
        }
    }

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
