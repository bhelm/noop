package com.noop.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StepsAverageCardTest {
    @Test fun averageIsOffButOfferedByYourCardsEditor() {
        val initial = DashboardCardPrefs.decodeEnabled("")
        assertFalse(DashboardCard.STEPS_AVERAGE_30 in initial)
        assertTrue(DashboardCard.STEPS_AVERAGE_30 in DashboardCard.hiddenOptions(initial))
        assertTrue(DashboardCard.STEPS_AVERAGE_30 in DashboardCard.hiddenOptions(listOf(DashboardCard.STEPS)))
    }

    @Test fun explicitSelectionSurvivesSavingAndIsNotOfferedTwice() {
        val selected = listOf(DashboardCard.STEPS, DashboardCard.STEPS_AVERAGE_30)
        val restored = DashboardCardPrefs.decodeEnabled(DashboardCardPrefs.encode(selected))
        assertEquals(selected, restored)
        assertFalse(DashboardCard.STEPS_AVERAGE_30 in DashboardCard.hiddenOptions(restored))
        assertNull(KeyMetric.fromRaw("stepsAverage30"))
    }

    @Test fun keyMetricEditorOffersOptionalMetricsOnOpenAndReset() {
        // The original editor used defaultOrder as the available set, losing every default-off item.
        assertTrue(KeyMetric.SKIN_TEMP in KeyMetric.hiddenOptions(KeyMetricPrefs.decodeEnabled("")))
        assertTrue(KeyMetric.SKIN_TEMP in KeyMetric.hiddenOptions(KeyMetric.defaultOrder))
        assertFalse(KeyMetric.SKIN_TEMP in KeyMetric.hiddenOptions(listOf(KeyMetric.SKIN_TEMP)))
    }
}
