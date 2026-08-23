package com.noop.analytics

/**
 * User-selectable ownership rule for additive daily metrics.
 *
 * Sleep and recovery values keep their night/wake-day semantics; this setting is for values accumulated
 * while awake, such as steps, energy and cardiovascular load.
 */
enum class DayCycleMode(val persistedValue: String) {
    SLEEP_ONSET("sleep_onset"),
    MIDNIGHT("midnight");

    companion object {
        fun fromPersisted(value: String?): DayCycleMode = entries.firstOrNull {
            it.persistedValue == value
        } ?: SLEEP_ONSET
    }
}

/** Shared cycle window consumed by every additive daily metric. */
data class DayCycleWindow(
    val id: String,
    val startInclusive: Long,
    val endExclusive: Long,
    val displayDay: String,
    val source: Source,
) {
    enum class Source { DETECTED_SLEEP, EDITED_SLEEP, SYNTHETIC_MIDNIGHT, CALENDAR }
}

object DayCycleResolver {
    const val MIN_SYNTHETIC_MIDNIGHT_AGE_SECONDS = 18 * 3_600L
    const val ABSOLUTE_MAX_OPEN_SECONDS = 40 * 3_600L

    /** Midnight is always available and is also the honest cold-start/failure fallback. */
    fun calendarWindow(now: Long, tzOffsetSeconds: Long): DayCycleWindow {
        val local = now + tzOffsetSeconds
        val dayNumber = Math.floorDiv(local, SleepStageTotals.SECONDS_PER_DAY)
        val start = dayNumber * SleepStageTotals.SECONDS_PER_DAY - tzOffsetSeconds
        val day = AnalyticsEngine.dayString(start, tzOffsetSeconds)
        return DayCycleWindow("calendar:$day", start, now, day, DayCycleWindow.Source.CALENDAR)
    }

    /** First local midnight that does not truncate a freshly-started sleep cycle. */
    fun fallbackMidnightAfter(start: Long, tzOffsetSeconds: Long): Long {
        val minimum = start + MIN_SYNTHETIC_MIDNIGHT_AGE_SECONDS
        val local = minimum + tzOffsetSeconds
        val dayNumber = Math.floorDiv(local, SleepStageTotals.SECONDS_PER_DAY)
        val atMidnight = dayNumber * SleepStageTotals.SECONDS_PER_DAY - tzOffsetSeconds
        return if (atMidnight >= minimum) atMidnight
        else (dayNumber + 1) * SleepStageTotals.SECONDS_PER_DAY - tzOffsetSeconds
    }

    /**
     * Resolve the active window. Reliable, explicitly-awake coverage preserves a genuine all-nighter.
     * Missing/untrustworthy coverage falls back to midnight; even claimed awake coverage is capped at 40 h.
     */
    fun activeWindow(
        mode: DayCycleMode,
        latestSleep: DayCycleWindow?,
        now: Long,
        tzOffsetSeconds: Long,
        reliableAwakeCoverage: Boolean,
    ): DayCycleWindow {
        if (mode == DayCycleMode.MIDNIGHT || latestSleep == null) {
            return calendarWindow(now, tzOffsetSeconds)
        }
        val age = now - latestSleep.startInclusive
        val mustFallback = age >= ABSOLUTE_MAX_OPEN_SECONDS ||
            (!reliableAwakeCoverage && now >= fallbackMidnightAfter(latestSleep.startInclusive, tzOffsetSeconds))
        if (!mustFallback) return latestSleep.copy(endExclusive = now)
        val boundary = fallbackMidnightAfter(latestSleep.startInclusive, tzOffsetSeconds)
        val day = AnalyticsEngine.dayString(boundary, tzOffsetSeconds)
        return DayCycleWindow(
            id = "synthetic:$day",
            startInclusive = boundary,
            endExclusive = now,
            displayDay = day,
            source = DayCycleWindow.Source.SYNTHETIC_MIDNIGHT,
        )
    }
}
