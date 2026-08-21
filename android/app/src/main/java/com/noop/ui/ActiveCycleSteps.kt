package com.noop.ui

import com.noop.data.DailyMetric
import com.noop.data.MetricSeriesRow

/** The step value Today must show independently of the dashboard's fixed 04:00 presentation day. */
internal data class ActiveCycleSteps(
    val wakeDay: String,
    val onsetTs: Long,
    val steps: Int,
)

internal fun effectiveActiveStrapId(published: String?, fallback: String): String =
    published?.takeIf { it.isNotBlank() } ?: fallback

/**
 * Resolve the newest confirmed physiological step-cycle which has actually begun. The marker's day is
 * the wake-day row carrying that cycle's total. Future/invalid markers are ignored so a bad clock cannot
 * blank or advance Today. null deliberately means "no confirmed cycle yet": the caller may retain the
 * existing calendar/logical-day fallback for cold start.
 */
internal fun resolveActiveCycleSteps(
    visibleDays: List<DailyMetric>,
    computedDays: List<DailyMetric>,
    onsetMarkers: List<MetricSeriesRow>,
    nowSeconds: Long,
): ActiveCycleSteps? {
    val visibleDayKeys = visibleDays.mapTo(HashSet()) { it.day }
    val computedByDay = computedDays.associateBy { it.day }
    return onsetMarkers.asSequence()
        .filter { it.value.isFinite() && it.value > 0.0 && it.value <= nowSeconds.toDouble() }
        .filter { it.day in visibleDayKeys }
        .sortedByDescending { it.value }
        .mapNotNull { marker ->
            val steps = computedByDay[marker.day]?.steps ?: return@mapNotNull null
            ActiveCycleSteps(marker.day, marker.value.toLong(), steps)
        }
        .firstOrNull()
}
