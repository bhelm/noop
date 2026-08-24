package com.noop.testcentre

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GroundTruthCollectorDeltaTest {
    @Test fun sameCycleComparesSessionIncreaseWithManualCount() {
        assertEquals(23, GroundTruthCollector.sessionDelta(4_620, "sleep:day:100", 5_034, "sleep:day:100", 391))
    }

    @Test fun cycleChangeNeverProducesHugeNegativeDelta() {
        assertNull(GroundTruthCollector.sessionDelta(4_620, "sleep:day:100", 0, "sleep:next:200", 391))
    }

    @Test fun legacySessionWithoutCycleIdentityIsUnavailable() {
        assertNull(GroundTruthCollector.sessionDelta(4_620, null, 5_034, "sleep:day:100", 391))
    }
}
