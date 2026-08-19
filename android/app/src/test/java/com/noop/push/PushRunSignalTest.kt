package com.noop.push

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PushRunSignalTest {
    @Test fun ownerCoalescesRunningAndBackoffTriggersAndLateFinishCannotClearSuccessor() {
        val prefs = SelfHostedPushSettingsTest.FakePushPrefs()

        assertTrue(PushRunSignal.reserve(prefs, "a"))
        PushRunSignal.begin(prefs, "a")
        assertFalse(PushRunSignal.reserve(prefs, "b"))
        assertTrue(PushRunSignal.finish(prefs, "a", willRetry = false))

        // Pending forced the same owner to survive into retry/backoff; another trigger still coalesces.
        assertFalse(PushRunSignal.reserve(prefs, "b"))
        PushRunSignal.begin(prefs, "a")
        assertTrue(PushRunSignal.finish(prefs, "a", willRetry = true))
        PushRunSignal.begin(prefs, "a")
        assertFalse(PushRunSignal.finish(prefs, "a", willRetry = false))

        assertTrue(PushRunSignal.reserve(prefs, "b"))
        assertFalse(PushRunSignal.finish(prefs, "a", willRetry = false))
        assertFalse(PushRunSignal.reserve(prefs, "c"))
    }

    @Test fun abandonedPreEnqueueReservationExpiresButBackoffOwnerDoesNot() {
        val prefs = SelfHostedPushSettingsTest.FakePushPrefs()
        assertTrue(PushRunSignal.reserve(prefs, "abandoned", now = 1_000L))
        assertFalse(PushRunSignal.reserve(prefs, "too-soon", now = 120_999L))
        assertTrue(PushRunSignal.reserve(prefs, "replacement", now = 121_000L))

        PushRunSignal.begin(prefs, "replacement")
        PushRunSignal.finish(prefs, "replacement", willRetry = true)
        assertFalse(PushRunSignal.reserve(prefs, "must-coalesce", now = Long.MAX_VALUE))
    }
}
