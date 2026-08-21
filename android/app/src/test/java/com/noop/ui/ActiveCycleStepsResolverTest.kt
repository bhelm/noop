package com.noop.ui

import com.noop.data.DailyMetric
import com.noop.data.MetricSeriesRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActiveCycleStepsResolverTest {
    private fun day(key: String, steps: Int?) =
        DailyMetric(deviceId = "my-whoop-noop", day = key, steps = steps)

    private fun onset(day: String, ts: Long) = MetricSeriesRow(
        deviceId = "my-whoop-noop",
        day = day,
        key = "steps_cycle_onset_ts",
        value = ts.toDouble(),
    )

    @Test fun publishedActiveStrapReplacesConstructionTimeFallback() {
        assertEquals("strap-b", effectiveActiveStrapId("strap-b", "strap-a"))
        assertEquals("strap-a", effectiveActiveStrapId(null, "strap-a"))
    }

    @Test fun activeCycleIgnoresTheFourAmLogicalDayRollover() {
        val days = listOf(day("2026-08-20", 6_000), day("2026-08-21", null))

        assertEquals(
            ActiveCycleSteps("2026-08-20", 1_000L, 6_000),
            resolveActiveCycleSteps(days, days, listOf(onset("2026-08-20", 1_000L)), nowSeconds = 5_000L),
        )
    }

    @Test fun newlyRecognisedMainSleepSwitchesToItsWakeDayCycle() {
        val days = listOf(day("2026-08-20", 6_000), day("2026-08-21", 17))
        val markers = listOf(onset("2026-08-20", 1_000L), onset("2026-08-21", 4_000L))

        assertEquals(
            ActiveCycleSteps("2026-08-21", 4_000L, 17),
            resolveActiveCycleSteps(days, days, markers, nowSeconds = 5_000L),
        )
    }

    @Test fun futureAndMalformedMarkersCannotMoveTheTile() {
        val days = listOf(day("2026-08-20", 6_000), day("2026-08-21", 17))
        val markers = listOf(
            onset("2026-08-20", 1_000L),
            onset("2026-08-21", 9_000L),
            onset("2026-08-22", 0L),
        )

        assertEquals(
            ActiveCycleSteps("2026-08-20", 1_000L, 6_000),
            resolveActiveCycleSteps(days, days, markers, nowSeconds = 5_000L),
        )
    }

    @Test fun noConfirmedCycleHasNoOverrideSoCalendarFallbackCanRemain() {
        val days = listOf(day("2026-08-21", 400))
        assertNull(resolveActiveCycleSteps(days, days, emptyList(), 5_000L))
    }

    @Test fun importedMergedStepsCannotOverrideTheComputedCycleValue() {
        val merged = listOf(day("2026-08-21", 410))
        val computed = listOf(day("2026-08-21", 17))
        assertEquals(
            ActiveCycleSteps("2026-08-21", 1_000, 17),
            resolveActiveCycleSteps(merged, computed, listOf(onset("2026-08-21", 1_000)), 5_000),
        )
    }

    @Test fun staleNewestMarkerFallsBackToPreviousMarkerWithAComputedValue() {
        val days = listOf(day("2026-08-20", 6_000), day("2026-08-21", null))
        val markers = listOf(onset("2026-08-20", 1_000), onset("2026-08-21", 4_000))
        assertEquals(
            ActiveCycleSteps("2026-08-20", 1_000, 6_000),
            resolveActiveCycleSteps(days, days, markers, 5_000),
        )
    }
}
