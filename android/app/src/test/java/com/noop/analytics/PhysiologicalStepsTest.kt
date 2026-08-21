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

    @Test fun napOnlyNeverOpensACycleEvenWhenItIsLong() {
        fun utc(hour: Int) = LocalDateTime.of(2026, 8, 21, hour, 0).toEpochSecond(ZoneOffset.UTC)

        assertNull(
            PhysiologicalSteps.mainSleepOnset(
                listOf(
                    PhysiologicalSteps.SleepBlock(
                        utc(1),
                        utc(9),
                        kind = PhysiologicalSteps.SleepKind.NAP,
                    ),
                    PhysiologicalSteps.SleepBlock(
                        utc(13),
                        utc(19),
                        kind = PhysiologicalSteps.SleepKind.NAP,
                    ),
                ),
                tzOffsetSeconds = 0,
                habitualMidsleepSec = null,
            ),
        )
    }

    @Test fun productionClassificationRejectsShortOrDaytimeNapShapes() {
        fun utc(hour: Int) = LocalDateTime.of(2026, 8, 21, hour, 0).toEpochSecond(ZoneOffset.UTC)
        val classified = PhysiologicalSteps.classifyForCycle(
            listOf(
                PhysiologicalSteps.SleepBlock(utc(1), utc(8), id = "night"),
                // Keep this clearly separate from the main night. A fragment beginning at 09:00 after
                // a one-hour wake is deliberately treated as a continuation of an interrupted night.
                PhysiologicalSteps.SleepBlock(utc(11), utc(13), id = "short"),
                PhysiologicalSteps.SleepBlock(utc(13), utc(19), id = "daytime"),
            ),
            tzOffsetSeconds = 0,
            habitualMidsleepSec = null,
        ).associate { it.id to it.kind }

        assertEquals(PhysiologicalSteps.SleepKind.MAIN_SLEEP, classified["night"])
        assertEquals(PhysiologicalSteps.SleepKind.NAP, classified["short"])
        assertEquals(PhysiologicalSteps.SleepKind.NAP, classified["daytime"])
    }

    @Test fun longDaytimeNapCannotHideAShorterValidMainNight() {
        fun utc(day: Int, hour: Int, minute: Int = 0) =
            LocalDateTime.of(2026, 8, day, hour, minute).toEpochSecond(ZoneOffset.UTC)
        val classified = PhysiologicalSteps.classifyForCycle(
            listOf(
                PhysiologicalSteps.SleepBlock(utc(21, 1), utc(21, 4, 30), id = "night"),
                PhysiologicalSteps.SleepBlock(utc(21, 13), utc(21, 19), id = "nap"),
            ),
            0,
            null,
        ).associate { it.id to it.kind }

        assertEquals(PhysiologicalSteps.SleepKind.MAIN_SLEEP, classified["night"])
        assertEquals(PhysiologicalSteps.SleepKind.NAP, classified["nap"])
    }

    @Test fun missingSleepKeepsTheLastCycleOpenAndLateSleepAdvancesIt() {
        val first = PhysiologicalSteps.CycleBoundary("night-a", onset = 1_000)

        assertEquals(first, PhysiologicalSteps.advanceBoundary(first, emptyList(), now = 3_000))
        assertEquals(
            PhysiologicalSteps.CycleBoundary("night-b", onset = 2_000),
            PhysiologicalSteps.advanceBoundary(
                first,
                listOf(
                    PhysiologicalSteps.SleepBlock(
                        onset = 2_000,
                        end = 2_800,
                        id = "night-b",
                    ),
                ),
                now = 3_000,
            ),
        )
    }

    @Test fun historicalWindowsHaveOnlySleepBoundariesAndKeepTheLastOneOpen() {
        assertEquals(
            listOf(
                PhysiologicalSteps.CycleWindow("night-a", 1_000, 2_000),
                PhysiologicalSteps.CycleWindow("night-b", 2_000, 9_000),
            ),
            PhysiologicalSteps.cycleWindows(
                listOf(
                    PhysiologicalSteps.CycleBoundary("night-a", 1_000),
                    PhysiologicalSteps.CycleBoundary("night-b", 2_000),
                ),
                now = 9_000,
            ),
        )
    }

    @Test fun napOnlyObservationCannotReplaceTheOpenCycle() {
        val first = PhysiologicalSteps.CycleBoundary("night-a", onset = 1_000)

        assertEquals(
            first,
            PhysiologicalSteps.advanceBoundary(
                first,
                listOf(
                    PhysiologicalSteps.SleepBlock(
                        onset = 2_000,
                        end = 8_000,
                        id = "long-daytime-nap",
                    ),
                ),
                now = 9_000,
                tzOffsetSeconds = 43_200,
            ),
        )
    }

    @Test fun editedOnsetMovesTheSameBoundaryWithoutCreatingAnotherCycle() {
        val previous = PhysiologicalSteps.CycleBoundary("night-a", onset = 1_000)

        assertEquals(
            PhysiologicalSteps.CycleBoundary("night-a", onset = 900),
            PhysiologicalSteps.advanceBoundary(
                previous,
                listOf(
                    PhysiologicalSteps.SleepBlock(
                        onset = 1_000,
                        end = 2_000,
                        id = "night-a",
                        editedOnset = 900,
                    ),
                ),
                now = 3_000,
            ),
        )
    }

    @Test fun ownerSegmentsFollowResolvedCalendarOwnersWithoutCrossDeviceSeams() {
        assertEquals(
            listOf(
                PhysiologicalSteps.OwnerSegment("strap-a", 1_000, 86_400),
                PhysiologicalSteps.OwnerSegment("strap-b", 86_400, 172_800),
                PhysiologicalSteps.OwnerSegment("strap-a", 172_800, 200_000),
            ),
            PhysiologicalSteps.ownerSegments(
                PhysiologicalSteps.CycleWindow("night", 1_000, 200_000),
                ownerByDay = mapOf(
                    "1970-01-01" to "strap-a",
                    "1970-01-02" to "strap-b",
                    "1970-01-03" to "strap-a",
                ),
                tzOffsetSeconds = 0,
                fallbackOwner = "strap-a",
            ),
        )
    }

    @Test fun middayReAddSwitchesOwnersAtRawCoverageBoundaryAndOverlapNeverDoubles() {
        val window = PhysiologicalSteps.CycleWindow("night", 0, 86_400)
        assertEquals(
            listOf(
                PhysiologicalSteps.OwnerSegment("strap-a", 0, 14 * 3_600L),
                PhysiologicalSteps.OwnerSegment("strap-b", 14 * 3_600L, 86_400),
            ),
            PhysiologicalSteps.ownerSegmentsFromCoverage(
                window,
                listOf(
                    PhysiologicalSteps.OwnerCoverage("strap-a", 0, 86_400, priority = 1),
                    PhysiologicalSteps.OwnerCoverage("strap-b", 14 * 3_600L, 86_400, priority = 0),
                ),
                fallbackOwner = "strap-a",
            ),
        )
        assertEquals(
            listOf(PhysiologicalSteps.OwnerSegment("strap-b", 0, 86_400)),
            PhysiologicalSteps.ownerSegmentsFromCoverage(
                window,
                listOf(
                    PhysiologicalSteps.OwnerCoverage("strap-a", 0, 86_400, 1),
                    PhysiologicalSteps.OwnerCoverage("strap-b", 0, 86_400, 0),
                ),
                "strap-a",
            ),
        )
    }

    @Test fun onlyCycleOnsetMayReadAPredecessorNeverANewOwnerSeam() {
        assertEquals(true, PhysiologicalSteps.shouldReadCounterPredecessor(segmentIndex = 0))
        assertEquals(false, PhysiologicalSteps.shouldReadCounterPredecessor(segmentIndex = 1))
        assertEquals(false, PhysiologicalSteps.shouldReadCounterPredecessor(segmentIndex = 2))
    }

    @Test fun internalRadioGapDoesNotSwitchAwayFromTheHigherPriorityOwner() {
        // Coverage is deliberately first..last, not a list of sample runs. A radio/sample hole inside
        // strap-b's observed span therefore creates no seam at which concurrently worn strap-a can win.
        assertEquals(
            listOf(PhysiologicalSteps.OwnerSegment("strap-b", 0, 86_400)),
            PhysiologicalSteps.ownerSegmentsFromCoverage(
                PhysiologicalSteps.CycleWindow("night", 0, 86_400),
                listOf(
                    PhysiologicalSteps.OwnerCoverage("strap-a", 0, 86_400, priority = 1),
                    // Its first and last samples surround an internal 10-minute radio gap.
                    PhysiologicalSteps.OwnerCoverage("strap-b", 0, 86_400, priority = 0),
                ),
                fallbackOwner = "strap-b",
            ),
        )
    }

    @Test fun paginatedCountCarriesItsPredecessorAcrossMoreThanTwoHundredThousandRows() {
        val samples = (0..200_005).map { i -> step(ts = i.toLong(), counter = i and 0xFFFF) }
        var state = PhysiologicalSteps.newCycleCount(hasActivityClasses = true)
        samples.chunked(10_000).forEach { page ->
            state = PhysiologicalSteps.accumulateCyclePage(
                state,
                page,
                onsetInclusive = 1,
                endExclusive = 200_006,
            )
        }

        assertEquals(200_005, PhysiologicalSteps.finishCycleCount(state))
    }
}
