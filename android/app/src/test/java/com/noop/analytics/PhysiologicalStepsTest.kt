package com.noop.analytics

import com.noop.data.StepSample
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneOffset

class PhysiologicalStepsTest {

    private fun step(ts: Long, counter: Int, activityClass: Int? = 1) =
        StepSample(deviceId = "my-whoop", ts = ts, counter = counter, activityClass = activityClass)

    @Test fun resetsAtMainSleepOnsetButKeepsRealNightWalking() {
        val samples = listOf(
            step(90, 100),       // predecessor before the new physiological cycle
            step(100, 104),      // delta belongs to the previous cycle
            step(200, 104, 0),   // asleep/still
            step(201, 106, 1),   // gets up and walks
            step(202, 109, 1),
            step(300, 112, 0),   // non-locomotion delta must still be ignored
        )

        assertEquals(5, PhysiologicalSteps.stepsInCycle(samples, onsetInclusive = 101, endExclusive = 400))
    }

    @Test fun nextMainSleepOnsetClosesTheCycle() {
        val samples = listOf(
            step(99, 10),
            step(110, 12),
            step(199, 15),
            step(200, 18), // attributed to the next cycle because its timestamp is the boundary
        )

        assertEquals(5, PhysiologicalSteps.stepsInCycle(samples, onsetInclusive = 100, endExclusive = 200))
    }

    @Test fun emptyCycleIsZeroRatherThanMissingOnceAnOnsetExists() {
        assertEquals(
            0,
            PhysiologicalSteps.stepsInCycle(
                listOf(step(99, 10), step(120, 10)),
                onsetInclusive = 100,
                endExclusive = 200,
            ),
        )
        assertNull(PhysiologicalSteps.stepsInCycle(emptyList(), onsetInclusive = 100, endExclusive = 200))
    }

    @Test fun nextOnsetWinsAndCurrentCycleFallsBackToNow() {
        val starts = mapOf(
            "2026-08-20" to 1_000L,
            "2026-08-21" to 2_000L,
        )

        assertEquals(2_000L, PhysiologicalSteps.cycleEnd("2026-08-20", starts, now = 2_500L))
        assertEquals(2_500L, PhysiologicalSteps.cycleEnd("2026-08-21", starts, now = 2_500L))
    }

    @Test fun mainSleepOnsetWinsOverASeparateDaytimeNap() {
        fun utc(day: Int, hour: Int, minute: Int = 0) =
            LocalDateTime.of(2026, 8, day, hour, minute).toEpochSecond(ZoneOffset.UTC)
        val blocks = listOf(
            PhysiologicalSteps.SleepBlock(utc(21, 14), utc(21, 15)),
            PhysiologicalSteps.SleepBlock(utc(21, 2, 57), utc(21, 10, 15)),
        )

        assertEquals(
            utc(21, 2, 57),
            PhysiologicalSteps.mainSleepOnset(blocks, tzOffsetSeconds = 0, habitualMidsleepSec = null),
        )
    }
}
