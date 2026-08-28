package com.noop

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CrashCaptureTest {
    @Test
    fun unchangedCrashIsShownUntilAcknowledged() {
        val fingerprint = CrashCapture.fingerprint("stack trace")
        assertTrue(CrashCapture.isPending(fingerprint, null))
        assertTrue(CrashCapture.isPending(fingerprint, "older crash"))
        assertFalse(CrashCapture.isPending(fingerprint, fingerprint))
    }

    @Test
    fun fingerprintIsStableAndDistinguishesNewCrashes() {
        assertEquals(CrashCapture.fingerprint("same"), CrashCapture.fingerprint("same"))
        assertNotEquals(CrashCapture.fingerprint("first"), CrashCapture.fingerprint("second"))
    }
}
