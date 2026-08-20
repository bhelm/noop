package com.noop.ble

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SuccessfulOffloadHookTest {
    @Test fun onlyTrueHistoryCompleteNotifiesDownstream() {
        assertTrue(WhoopBleClient.shouldNotifySuccessfulOffload("HISTORY_COMPLETE"))
        assertFalse(WhoopBleClient.shouldNotifySuccessfulOffload("timeout"))
        assertFalse(WhoopBleClient.shouldNotifySuccessfulOffload("aborted by user"))
        assertFalse(WhoopBleClient.shouldNotifySuccessfulOffload("disconnect"))
    }
}
