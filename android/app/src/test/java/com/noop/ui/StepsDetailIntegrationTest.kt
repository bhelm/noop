package com.noop.ui

import com.noop.analytics.StepsDetailGranularity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StepsDetailIntegrationTest {
    private fun reading(day: String, value: Double) = VitalReading(day, value, "steps-source")

    @Test
    fun `steps force bars even when the preference is line`() {
        assertTrue(vitalChartIsBars("steps_est", TrendChartStyle.LINE))
        assertTrue(vitalChartIsBars("steps_est", TrendChartStyle.BAR))
    }

    @Test
    fun `other vitals retain the chart preference`() {
        assertFalse(vitalChartIsBars("hrv", TrendChartStyle.LINE))
        assertTrue(vitalChartIsBars("hrv", TrendChartStyle.BAR))
    }

    @Test
    fun `one raw step reading remains one daily bar`() {
        val series = projectStepsDetail(
            readings = listOf(reading("2026-09-10", 4_321.0)),
            range = VitalDetailRange.WEEK,
        )

        assertEquals(listOf("2026-09-10" to 4_321.0), series.points)
        assertEquals(1, series.buckets.single().observedDayCount)
        assertEquals(StepsDetailGranularity.DAILY, series.granularity)
    }

    @Test
    fun `several raw readings in one monthly bucket remain one bar`() {
        val series = projectStepsDetail(
            readings = listOf(
                reading("2026-08-02", 1_000.0),
                reading("2026-08-20", 2_000.0),
            ),
            range = VitalDetailRange.SIX_MONTH,
        )

        assertEquals(listOf("2026-08-01" to 1_500.0), series.points)
        assertEquals(2, series.buckets.single().observedDayCount)
        assertEquals(StepsDetailGranularity.MONTHLY, series.granularity)
        assertTrue(series.selectionLabels.single().contains("Aug"))
        assertTrue(series.accessibilitySummary.contains("average steps per observed day"))
    }

    @Test
    fun `every range maps to the shared projector contract`() {
        val expected = listOf(
            StepsDetailGranularity.DAILY,
            StepsDetailGranularity.DAILY,
            StepsDetailGranularity.DAILY,
            StepsDetailGranularity.DAILY,
            StepsDetailGranularity.WEEKLY,
            StepsDetailGranularity.MONTHLY,
            StepsDetailGranularity.MONTHLY,
            StepsDetailGranularity.MONTHLY,
        )
        assertEquals(expected, VitalDetailRange.entries.map { projectStepsDetail(emptyList(), it).granularity })
    }

    @Test
    fun `step projection excludes invalid values and never falls back by position`() {
        val readings = listOf(
            reading("not-a-day", 99_999.0),
            reading("2026-01-01", 1_000.0),
            reading("2026-02-01", Double.NaN),
            reading("2026-02-02", -1.0),
            reading("2026-03-01", 3_000.0),
        )

        val series = projectStepsDetail(readings, VitalDetailRange.WEEK)
        assertEquals(listOf("2026-03-01" to 3_000.0), series.points)
        assertEquals(
            listOf("not-a-day", "2026-03-01"),
            filterStepReadings(readings, VitalDetailRange.WEEK).map { it.day },
        )
    }
}
