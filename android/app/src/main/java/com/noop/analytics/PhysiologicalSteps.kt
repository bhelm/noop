package com.noop.analytics

import com.noop.data.StepSample

/**
 * WHOOP-style step-day boundaries. A physiological step day starts at the onset of the main sleep
 * and ends at the next main-sleep onset; the delta is still attributed to the later counter sample.
 * We deliberately do not mask the sleep span: a real walk during a long night-time wake remains a
 * walk, while the shared activity-class and tick-rate gates continue to reject non-locomotion.
 */
object PhysiologicalSteps {

    data class SleepBlock(val onset: Long, val end: Long)

    /** Use the exact same main-night/group selector as the Sleep screen and daily sleep aggregate. */
    fun mainSleepOnset(
        blocks: List<SleepBlock>,
        tzOffsetSeconds: Long,
        habitualMidsleepSec: Long?,
    ): Long? {
        val indices = SleepStageTotals.mainNightGroupIndices(
            blocks.map { SleepStageTotals.NightBlock(it.onset, it.end) },
            tzOffsetSeconds,
            habitualMidsleepSec,
        ) ?: return null
        return indices.minOfOrNull { blocks[it].onset }
    }

    /** Resolve the next observed onset, or [now] for the currently open physiological cycle. */
    fun cycleEnd(day: String, onsetByDay: Map<String, Long>, now: Long): Long {
        val onset = onsetByDay[day] ?: return now
        return onsetByDay.values.asSequence()
            .filter { it > onset && it <= now }
            .minOrNull()
            ?: now
    }

    /**
     * Sum locomotion deltas whose *later* sample lies in [onsetInclusive, endExclusive). Samples may
     * include one predecessor before onset so a counter increment crossing the boundary is attributed
     * correctly. Returns null only when there is not enough counter data to evaluate the cycle; an
     * observed, flat cycle is a real zero.
     */
    fun stepsInCycle(
        samples: List<StepSample>,
        onsetInclusive: Long,
        endExclusive: Long,
    ): Int? {
        if (endExclusive <= onsetInclusive) return 0
        val sorted = samples.sortedBy { it.ts }
        if (sorted.size < 2) return null
        val hasActivityClasses = StepsCounter.hasActivityClasses(sorted)
        var total = 0
        var evaluatedDelta = false
        for (i in 1 until sorted.size) {
            val current = sorted[i]
            if (current.ts < onsetInclusive || current.ts >= endExclusive) continue
            evaluatedDelta = true
            val previous = sorted[i - 1]
            val delta = (current.counter - previous.counter) and 0xFFFF
            if (
                StepsCounter.shouldCountDelta(current.activityClass, hasActivityClasses) &&
                StepsCounter.isPlausibleDelta(previous.ts, current.ts, delta)
            ) {
                total += delta
            }
        }
        return if (evaluatedDelta) total else null
    }
}
