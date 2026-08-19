package com.noop.push

import androidx.work.ExistingWorkPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfHostedPushSchedulerTest {
    @Test fun workIsSerializedAndBackoffMeetsWorkManagerMinimum() {
        assertEquals(ExistingWorkPolicy.REPLACE, SelfHostedPushScheduler.EXISTING_WORK_POLICY)
        assertTrue(SelfHostedPushScheduler.UNIQUE_WORK.isNotBlank())
        assertTrue(SelfHostedPushScheduler.BACKOFF_SECONDS >= 10)
    }
}
