package com.noop.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StepsControlSessionExportTest {
    private val marker = "stepsControl session=steps-123 startTs=123"

    @Test fun reportIsCutAtTheExactActivationMarker() {
        val log = "old unrelated line\n12:00:00  [steps] $marker\n12:01:00  [steps] stepsCycle wakeDay=2026-08-21"
        val cut = LogExport.stepsSessionLog(log, "steps-123")
        assertNotNull(cut)
        cut as String
        assertFalse(cut.contains("old unrelated"))
        assertTrue(cut.startsWith("12:00:00"))
        assertTrue(cut.contains("stepsCycle wakeDay="))
    }

    @Test fun missingMarkerIsExplicitAndNeverPretendsTheWholeLogMatches() {
        assertEquals(null, LogExport.stepsSessionLog("some current log", "steps-123"))
    }

    @Test fun metadataNamesCoverageAndIncompleteReasons() {
        val complete = LogExport.stepsSessionMetadata(
            sessionId = "steps-123", startedAt = 123, endedAt = 456, deviceId = "strap-a",
            counts = mapOf("steps" to 40, "gravity" to 40), possiblyTruncated = emptySet(),
            logMatched = true, stepsCycleTraceComplete = true,
            deviceChanged = false, deviceUnknown = false,
        )
        assertTrue(complete.contains("\"complete\":true"))
        assertTrue(complete.contains("\"session_id\":\"steps-123\""))

        val incomplete = LogExport.stepsSessionMetadata(
            sessionId = "steps-123", startedAt = 123, endedAt = 456, deviceId = "strap-a",
            counts = mapOf("steps" to 0), possiblyTruncated = setOf("rr"), logMatched = false,
            stepsCycleTraceComplete = false, windowCapped = true,
            deviceChanged = true, deviceUnknown = false,
        )
        assertTrue(incomplete.contains("\"complete\":false"))
        assertTrue(incomplete.contains("no_step_rows"))
        assertTrue(incomplete.contains("log_marker_missing"))
        assertTrue(incomplete.contains("possibly_truncated"))
        assertTrue(incomplete.contains("steps_cycle_trace_missing_or_incomplete"))
        assertTrue(incomplete.contains("window_capped_to_24h"))
        assertTrue(incomplete.contains("device_changed_during_session"))

        val unknown = LogExport.stepsSessionMetadata(
            sessionId = "steps-legacy", startedAt = 123, endedAt = 456, deviceId = null,
            counts = emptyMap(), possiblyTruncated = emptySet(), logMatched = true,
            stepsCycleTraceComplete = true, deviceChanged = false, deviceUnknown = true,
        )
        assertTrue(unknown.contains("device_unknown"))
        assertTrue(unknown.contains("\"device_id\":null"))
    }
}
