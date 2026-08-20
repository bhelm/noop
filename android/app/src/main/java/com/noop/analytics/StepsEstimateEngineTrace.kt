package com.noop.analytics

import com.noop.data.StepSample
import com.noop.data.GravitySample
import com.noop.data.V18AuxRow
import kotlin.math.max

// StepsEstimateEngineTrace.kt - Kotlin twin of StepsEstimateEngine+Trace.swift. The Steps test-mode traces.
//
// Two pure, side-effect-free twins for the two ways NOOP produces a step number:
//
//  1. calibrationTrace(...) - the WHOOP-4 motion-volume path. Reports each calibration day's motion VOLUME
//     and phone reference count, then the fitted (or manual) calibration state (k / sampleDays / confidence
//     / manual) by reusing StepsEstimateEngine.calibrate VERBATIM, so the trace can never disagree with the
//     coefficient the Settings/Steps screen shows; when withheld it names the status (the "Need N more days"
//     reason), the same status the tile renders.
//
//  2. rawCounterTrace(...) - the WHOOP 5/MG raw path. Reports the cumulative step_motion_counter series and
//     its WRAP-AWARE deltas (cur - prev) and 0xFFFF, absolute-boundary and per-second rate outliers, and
//     the same total AnalyticsEngine.analyzeDay sums, with the SAME plausibility gate and ticks-per-step
//     scaling, so the trace and the daily steps_est can never diverge.
//
// No clock, no IO, no PII (counts and ratios only). The Steps test mode gates each call behind
// TestCentre.active(STEPS) at the call site (IntelligenceEngine); when the mode is off neither is ever
// called, so there is zero cost. Byte-aligned with the Swift line shapes so a shared report reads
// identically on either platform. No em-dashes.

object StepsEstimateEngineTrace {

    private fun r2(x: Double): Double = Math.round(x * 100.0) / 100.0

    /**
     * The WHOOP-4 motion-volume calibration trace. Given the per-day calibration points (each a motion volume
     * + a phone reference step count) and the optional manual override, it logs one `stepsCal point` line per
     * usable day, then the calibration outcome - built by reusing [StepsEstimateEngine.calibrate] VERBATIM
     * (so k / sampleDays / confidence / manual match the stored coefficient), or the [StepsEstimateEngine.status]
     * line naming why the fit was withheld. Mirrors the Swift StepsEstimateEngine.calibrationTrace.
     */
    fun calibrationTrace(
        points: List<StepsEstimateEngine.CalibrationPoint>,
        manualOverride: Double? = null,
    ): List<String> {
        val lines = ArrayList<String>()

        // Per-usable-day points: the SAME filter the fit applies, so the trace shows exactly the days that voted.
        val usable = points.filter(StepsEstimateEngine::isUsableCalibrationPoint)
        for (p in usable) {
            val ratio = if (p.motion > 0) p.steps / p.motion else 0.0
            lines.add(
                "stepsCal point motion=${r2(p.motion)} phoneRef=${p.steps.toInt()} " +
                    "ratio=${r2(ratio)} (steps/motion votes weighted by motion)",
            )
        }

        // The calibration outcome, read from calibrate(...) verbatim so it matches the stored coefficient.
        val cal = StepsEstimateEngine.calibrate(points, manualOverride)
        if (cal != null && (usable.size >= StepsEstimateEngine.MIN_CALIBRATION_DAYS || cal.manual)) {
            val source = if (cal.manual) "user-set k" else "k = motion-weighted median of steps/motion"
            lines.add(
                "stepsCal fit k=${r2(cal.coefficient)} sampleDays=${cal.sampleDays} " +
                    "confidence=${r2(cal.confidence)} manual=${cal.manual} " +
                    "($source)",
            )
        } else {
            // Withheld: name the status the tile shows, via status(...) verbatim (SAME usable-day filter).
            when (val status = StepsEstimateEngine.status(points, manualOverride)) {
                is StepsEstimateEngine.CalibrationStatus.NeedsMoreDays ->
                    lines.add(
                        "stepsCal withheld reason=needsMoreDays have=${status.have} need=${status.need} " +
                            "(no usable auto-fit and no manual k)",
                    )
                is StepsEstimateEngine.CalibrationStatus.Manual ->
                    lines.add(
                        "stepsCal fit k=${r2(status.coefficient)} sampleDays=${status.sampleDays} " +
                            "confidence=1.0 manual=true (user-set k)",
                    )
                is StepsEstimateEngine.CalibrationStatus.Calibrated ->
                    lines.add(
                        "stepsCal fit k=${r2(status.coefficient)} sampleDays=${status.sampleDays} " +
                            "confidence=${r2(status.confidence)} manual=false " +
                            "(k = motion-weighted median of steps/motion)",
                    )
            }
        }
        return lines
    }

    /**
     * The WHOOP 5/MG raw-counter trace for one day. Recomputes the SAME wrap-aware sum [AnalyticsEngine.analyzeDay]
     * runs over the cumulative step_motion_counter series: the time-ordered records filtered to the LOCAL day,
     * each consecutive (cur - prev) and 0xFFFF increment, the dropped deltas (>= maxStepDelta), and the
     * ticksPerStep scaling. Reports the counter series length, kept/dropped delta counts, raw tick total and
     * scaled steps - the SAME value the daily steps_est carries. Mirrors the Swift StepsEstimateEngine.rawCounterTrace.
     */
    fun rawCounterTrace(
        daySteps: List<StepSample>,
        dayKey: String,
        tzOffsetSeconds: Long,
        ticksPerStep: Double,
    ): List<String> {
        // The SAME filter + sort: keep only this LOCAL day's samples, time-ordered.
        val sorted = daySteps
            .filter { AnalyticsEngine.dayString(it.ts, tzOffsetSeconds) == dayKey }
            .sortedBy { it.ts }

        val lines = ArrayList<String>()
        // #810: a WHOOP 4.0 sends NO raw step counter over BLE at all, so `daySteps` is empty for it; its
        // steps are MOTION-ESTIMATED (the calibrationTrace path), not counted. Emitting the bare
        // "counterSamples=0 (need >=2 for a delta)" line made a 4.0 export read as BROKEN. When there is
        // no counter sample at all, say so honestly so the trace reflects the model, not a fault. (A 5/MG
        // with a single counter sample still falls through to the "need >=2" line: it HAS a counter, just
        // one read this window.)
        if (daySteps.isEmpty()) {
            lines.add(
                "stepsRaw day=$dayKey counterSamples=0 noRawCounter " +
                    "(no step counter on this device; steps are motion-estimated, e.g. WHOOP 4.0)",
            )
            return lines
        }
        // A non-empty input proves a counter exists. If its rows all fall outside the requested local day,
        // report only that window mismatch; never infer device capability or motion-estimation semantics.
        if (sorted.isEmpty()) {
            lines.add(
                "stepsRaw day=$dayKey counterSamples=0 inputSamples=${daySteps.size} noRowsForDay " +
                    "(no counter rows matched the requested local day)",
            )
            return lines
        }
        if (sorted.size < 2) {
            lines.add("stepsRaw day=$dayKey counterSamples=${sorted.size} (need >=2 for a delta)")
            return lines
        }

        // Walk the wrap-aware deltas exactly as the production sum does.
        var rawTotal = 0
        var keptDeltas = 0
        var droppedDeltas = 0
        var rateOutliers = 0
        var nonLocomotionDeltas = 0
        var minDelta = Int.MAX_VALUE
        var maxDelta = Int.MIN_VALUE
        val hasActivityClasses = StepsCounter.hasActivityClasses(sorted)
        for (i in 1 until sorted.size) {
            val delta = (sorted[i].counter - sorted[i - 1].counter) and 0xFFFF // wrap-aware u16 increment
            val plausible = StepsCounter.isPlausibleDelta(sorted[i - 1].ts, sorted[i].ts, delta)
            if (plausible &&
                StepsCounter.shouldCountDelta(sorted[i].activityClass, hasActivityClasses)) {
                rawTotal += delta
                keptDeltas += 1
                minDelta = minOf(minDelta, delta)
                maxDelta = maxOf(maxDelta, delta)
            } else if (plausible) {
                nonLocomotionDeltas += 1
            } else if (delta >= StepsCounter.MAX_STEP_DELTA) {
                droppedDeltas += 1
            } else if (delta > 0) {
                rateOutliers += 1
            }
        }

        val firstCounter = sorted.first().counter
        val lastCounter = sorted.last().counter
        lines.add(
            "stepsRaw day=$dayKey counterSamples=${sorted.size} " +
                "firstCounter=$firstCounter lastCounter=$lastCounter (cumulative u16 @57)",
        )
        lines.add(
            "stepsRaw deltas kept=$keptDeltas dropped=$droppedDeltas rateOutliers=$rateOutliers " +
                "(absolute delta>=${StepsCounter.MAX_STEP_DELTA}; rate >${StepsCounter.MAX_TICKS_PER_SECOND} ticks/s)",
        )
        lines.add(
            "stepsRaw activityFilter=${if (hasActivityClasses) "walk-run" else "legacy-unclassed"} " +
                "nonLocomotion=$nonLocomotionDeltas",
        )
        if (keptDeltas > 0) {
            lines.add(
                "stepsRaw keptRange min=$minDelta max=$maxDelta " +
                    "(each = (cur-prev)&0xFFFF, wrap-aware)",
            )
        }

        // The scaled total, the SAME expression analyzeDay produces for steps_est (ticks / ticksPerStep,
        // floored at 0.5 so a bad pref can at most double, never explode, the total).
        val scaled = if (rawTotal > 0) {
            Math.round(rawTotal.toDouble() / max(ticksPerStep, 0.5)).toInt()
        } else {
            0
        }
        // L7: production analyzeDay returns `scaled > 0 ? scaled : null`, so a tiny rawTotal that rounds to 0
        // yields NO steps_est for the day. Render "none" (not 0) so the trace matches the null headline rather
        // than implying a real zero-step measurement.
        val scaledText = if (scaled > 0) scaled.toString() else "none"
        lines.add(
            "stepsRaw total rawTicks=$rawTotal ticksPerStep=${r2(ticksPerStep)} " +
                "scaledSteps=$scaledText (steps_est for the day)",
        )
        return lines
    }

    /**
     * Instrumentation-only candidate derived from the 2026-08-20 controlled walk/household capture.
     * It never feeds [StepsCounter], a score, storage or UI. Missing auxiliary samples fail open and are
     * counted explicitly, so old history cannot silently look better. Run-class rows stay untouched;
     * only walk-class rows are screened by the strap's orientation-independent dynamic-acceleration
     * magnitude and cadence-like byte.
     */
    fun shadowCandidateTrace(
        daySteps: List<StepSample>,
        gravity: List<GravitySample>,
        aux: List<V18AuxRow>,
        dayKey: String,
        tzOffsetSeconds: Long,
    ): List<String> {
        val sorted = daySteps
            .filter { AnalyticsEngine.dayString(it.ts, tzOffsetSeconds) == dayKey }
            .sortedBy { it.ts }
        if (sorted.size < 2) return emptyList()
        val dynByTs = gravity.asSequence().mapNotNull { g -> g.dynAccel?.let { g.ts to it } }.toMap()
        val cadenceByTs = aux.asSequence().mapNotNull { a -> a.stepCadence?.let { a.ts to it.toInt() } }.toMap()
        val hasClasses = StepsCounter.hasActivityClasses(sorted)
        var productionTicks = 0
        var shadowTicks = 0
        var dynRejectedTicks = 0
        var cadenceRejectedTicks = 0
        var missingDyn = 0
        var missingCadence = 0
        for (i in 1 until sorted.size) {
            val cur = sorted[i]
            val delta = (cur.counter - sorted[i - 1].counter) and 0xFFFF
            if (!StepsCounter.isPlausibleDelta(sorted[i - 1].ts, cur.ts, delta)) continue
            if (!StepsCounter.shouldCountDelta(cur.activityClass, hasClasses)) continue
            productionTicks += delta
            if (cur.activityClass == 2) {
                shadowTicks += delta
                continue
            }
            val dyn = dynByTs[cur.ts]
            val cadence = cadenceByTs[cur.ts]
            if (dyn == null) missingDyn++
            if (cadence == null) missingCadence++
            when {
                dyn != null && dyn > SHADOW_MAX_DYN_ACCEL_G -> dynRejectedTicks += delta
                cadence != null && cadence !in SHADOW_CADENCE_RANGE -> cadenceRejectedTicks += delta
                else -> shadowTicks += delta
            }
        }
        return listOf(
            "stepsShadow day=$dayKey productionTicks=$productionTicks shadowTicks=$shadowTicks " +
                "instrumentationOnly=true",
            "stepsShadow thresholds walkDynMax=$SHADOW_MAX_DYN_ACCEL_G " +
                "walkCadence=${SHADOW_CADENCE_RANGE.first}..${SHADOW_CADENCE_RANGE.last} runPassThrough=true",
            "stepsShadow rejected dynRejectedTicks=$dynRejectedTicks cadenceRejectedTicks=$cadenceRejectedTicks " +
                "missingDyn=$missingDyn missingCadence=$missingCadence (missing fails open)",
        )
    }

    private const val SHADOW_MAX_DYN_ACCEL_G = 0.19
    private val SHADOW_CADENCE_RANGE = 110..180
}

/**
 * Pure values for the Steps live-readout panel. Kotlin twin of the Swift StepsReadout. Each parses the
 * STEPS-tagged log tail the Steps test-mode emitters write. No state, no IO, no em-dashes. (Android defers
 * the Compose readout panel for ALL modes, matching the existing split; this twin exists for parity + tests.)
 */
object StepsReadout {

    /** Today's steps for the `stepsToday` id: the most recent scaled-steps figure in the tagged tail (the
     *  5/MG `scaledSteps=` or the WHOOP-4 `stepsEst ... steps=`). null when no step line is present yet. */
    fun stepsToday(taggedTail: List<String>): Int? {
        for (line in taggedTail.asReversed()) {
            val n = intField(line, "scaledSteps=")
            if (n != null) return n
            if (line.contains("stepsEst ")) {
                val e = intField(line, "steps=")
                if (e != null) return e
            }
        }
        return null
    }

    /** Calibration state for the `calibrationState` id: the most recent calibration outcome fragment (the
     *  WHOOP-4 `stepsCal fit ...` or `stepsCal withheld reason=...`). null when no calibration line yet. */
    fun calibrationState(taggedTail: List<String>): String? {
        for (line in taggedTail.asReversed()) {
            val fit = line.indexOf("stepsCal fit ")
            if (fit >= 0) {
                val frag = line.substring(fit + "stepsCal fit ".length).takeWhile { it != '(' }.trim()
                if (frag.isNotEmpty()) return frag
            }
            val withheld = line.indexOf("stepsCal withheld reason=")
            if (withheld >= 0) {
                val frag = line.substring(withheld + "stepsCal withheld reason=".length)
                    .takeWhile { it != '(' }.trim()
                if (frag.isNotEmpty()) return "not calibrated ($frag)"
            }
        }
        return null
    }

    /** Parse a `key=<int>` field out of a line (value runs to the next space). null when absent/non-numeric. */
    internal fun intField(line: String, key: String): Int? {
        val i = line.indexOf(key)
        if (i < 0) return null
        val token = line.substring(i + key.length).takeWhile { it != ' ' }
        return token.toIntOrNull()
    }
}
