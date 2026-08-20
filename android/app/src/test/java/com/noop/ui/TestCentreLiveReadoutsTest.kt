package com.noop.ui

import com.noop.testcentre.TestDomain
import com.noop.testcentre.TestModeRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TestCentreLiveReadoutsTest {

    @Test fun activeRecoveryRowRendersChargeLabelAndParsedValueFromExportLog() {
        val mode = requireNotNull(TestModeRegistry.mode(TestDomain.RECOVERY))
        val rows = TestCentreLiveReadouts.rows(
            mode = mode,
            active = true,
            snapshot = TestCentreLiveSnapshot(
                logLines = listOf("[recovery] charge day=2026-08-19 score=62.5 band=yellow"),
            ),
        )

        assertEquals(
            listOf(LiveReadoutRow("lastChargeBreakdown", "Last Charge breakdown", "score=62.5 band=yellow")),
            rows,
        )
    }

    @Test fun recoveryUsesTheFirstRealDomainTagAndRejectsEmbeddedTagSpoofing() {
        val mode = requireNotNull(TestModeRegistry.mode(TestDomain.RECOVERY))
        val rows = TestCentreLiveReadouts.rows(
            mode = mode,
            active = true,
            snapshot = TestCentreLiveSnapshot(
                logLines = listOf(
                    "2026-08-19 12:00:00 [recovery] charge day=2026-08-19 score=62.5 band=yellow",
                    "[connection] payload=[recovery] charge day=2026-08-20 score=99.0 band=green",
                    "message payload=[recovery] charge day=2026-08-21 score=100.0 band=green",
                ),
            ),
        )

        assertEquals("score=62.5 band=yellow", rows.single().value)
    }

    @Test fun everyRegistryDeclaredIdHasExactlyOnePresentationMapping() {
        val declared = TestModeRegistry.all.flatMap { it.liveReadout }.toSet()
        assertEquals(17, declared.size)
        assertEquals(declared, TestCentreLiveReadouts.mappedIds)

        TestModeRegistry.all.forEach { mode ->
            assertEquals(
                mode.liveReadout,
                TestCentreLiveReadouts.rows(
                    mode = mode,
                    active = true,
                    snapshot = TestCentreLiveSnapshot(),
                ).map { it.id },
            )
        }
    }

    @Test fun stepsShowsProductionAndScaledShadowCandidateSideBySide() {
        val mode = requireNotNull(TestModeRegistry.mode(TestDomain.STEPS))
        val rows = TestCentreLiveReadouts.rows(
            mode = mode,
            active = true,
            snapshot = TestCentreLiveSnapshot(
                logLines = listOf(
                    "[steps] stepsRaw total rawTicks=8400 ticksPerStep=2.0 scaledSteps=4200",
                    "[steps] stepsShadow productionTicks=8400 shadowTicks=7600 " +
                        "productionSteps=4200 shadowSteps=3800 instrumentationOnly=true",
                ),
            ),
        )

        assertEquals("Current algorithm", rows[0].label)
        assertEquals("4200", rows[0].value)
        assertEquals("Shadow candidate", rows[1].label)
        assertEquals("3800", rows[1].value)
    }

    @Test fun inactiveRowIsCompactAndDoesNotResolveEvenAnUnknownReadout() {
        val futureMode = requireNotNull(TestModeRegistry.mode(TestDomain.RECOVERY))
            .copy(liveReadout = listOf("futureReadout"))

        assertTrue(
            TestCentreLiveReadouts.rows(
                mode = futureMode,
                active = false,
                snapshot = TestCentreLiveSnapshot(),
            ).isEmpty(),
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun activeUnknownReadoutFailsVisiblyInsteadOfDisappearing() {
        val futureMode = requireNotNull(TestModeRegistry.mode(TestDomain.RECOVERY))
            .copy(liveReadout = listOf("futureReadout"))

        TestCentreLiveReadouts.rows(
            mode = futureMode,
            active = true,
            snapshot = TestCentreLiveSnapshot(),
        )
    }

    @Test fun refreshPolicyIsActiveOnlyAndObservesOnlyRelevantSourceRevisions() {
        val recovery = requireNotNull(TestModeRegistry.mode(TestDomain.RECOVERY))
        val connection = requireNotNull(TestModeRegistry.mode(TestDomain.CONNECTION))
        val sleep = requireNotNull(TestModeRegistry.mode(TestDomain.SLEEP))
        val battery = requireNotNull(TestModeRegistry.mode(TestDomain.BATTERY))

        assertEquals(LiveReadoutRefreshSources(), TestCentreLiveRefreshPolicy.sources(recovery, active = false))
        assertEquals(
            LiveReadoutRefreshSources(observeLogRevision = true),
            TestCentreLiveRefreshPolicy.sources(recovery, active = true),
        )
        assertEquals(
            LiveReadoutRefreshSources(observeLogRevision = true, connectionClockEveryMs = 1_000),
            TestCentreLiveRefreshPolicy.sources(connection, active = true),
        )
        assertEquals(
            LiveReadoutRefreshSources(observeLogRevision = true, observeSleepSampleRevision = true),
            TestCentreLiveRefreshPolicy.sources(sleep, active = true),
        )
        assertEquals(
            LiveReadoutRefreshSources(observeLogRevision = true, observeBatteryRevision = true),
            TestCentreLiveRefreshPolicy.sources(battery, active = true),
        )
    }
}
