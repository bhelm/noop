package com.noop.testcentre

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Mirror of the Swift TestCentreActivationTests: same activation semantics, master-implies-all,
 * universal-rides-any, answers round-trip, idempotent migration that preserves legacy keys.
 *
 * The project ships NO Robolectric (junit + kotlinx-coroutines-test only, see app/build.gradle.kts and
 * DeviceRegistryTest / MoodStoreTest), so rather than stand up a real Context we run the REAL [TestCentre]
 * over an in-memory [FakeSharedPreferences] that reproduces the SharedPreferences read/write contract.
 */
class TestCentreTest {

    /** A minimal in-memory SharedPreferences: enough of the read/write contract for [TestCentre]. The
     *  editor mutates a shadow map and commits it on apply(), exactly as the platform would persist. */
    private class FakeSharedPreferences : SharedPreferences {
        val map = HashMap<String, Any?>()

        override fun getBoolean(key: String, defValue: Boolean): Boolean = map[key] as? Boolean ?: defValue
        override fun getLong(key: String, defValue: Long): Long = map[key] as? Long ?: defValue
        override fun getString(key: String, defValue: String?): String? = map[key] as? String ?: defValue
        override fun getInt(key: String, defValue: Int): Int = map[key] as? Int ?: defValue
        override fun getFloat(key: String, defValue: Float): Float = map[key] as? Float ?: defValue
        @Suppress("UNCHECKED_CAST")
        override fun getStringSet(key: String, defValues: MutableSet<String>?): MutableSet<String>? =
            map[key] as? MutableSet<String> ?: defValues
        override fun getAll(): MutableMap<String, *> = HashMap(map)
        override fun contains(key: String): Boolean = map.containsKey(key)
        override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}

        override fun edit(): SharedPreferences.Editor = FakeEditor(this)

        private class FakeEditor(private val prefs: FakeSharedPreferences) : SharedPreferences.Editor {
            private val pending = HashMap<String, Any?>()
            private val removals = HashSet<String>()
            override fun putString(key: String, value: String?): SharedPreferences.Editor { pending[key] = value; return this }
            override fun putStringSet(key: String, values: MutableSet<String>?): SharedPreferences.Editor { pending[key] = values; return this }
            override fun putInt(key: String, value: Int): SharedPreferences.Editor { pending[key] = value; return this }
            override fun putLong(key: String, value: Long): SharedPreferences.Editor { pending[key] = value; return this }
            override fun putFloat(key: String, value: Float): SharedPreferences.Editor { pending[key] = value; return this }
            override fun putBoolean(key: String, value: Boolean): SharedPreferences.Editor { pending[key] = value; return this }
            override fun remove(key: String): SharedPreferences.Editor { removals.add(key); return this }
            override fun clear(): SharedPreferences.Editor { prefs.map.clear(); return this }
            override fun commit(): Boolean { flush(); return true }
            override fun apply() { flush() }
            private fun flush() {
                for (k in removals) prefs.map.remove(k)
                prefs.map.putAll(pending)
            }
        }
    }

    private fun newCentre() = TestCentre(FakeSharedPreferences())

    @Test fun activateThenActiveThenDeactivate() {
        val tc = newCentre()
        assertFalse(tc.active(TestDomain.SLEEP))
        tc.activate(TestDomain.SLEEP)
        assertTrue(tc.active(TestDomain.SLEEP))
        assertFalse(tc.active(TestDomain.BATTERY))
        tc.deactivate(TestDomain.SLEEP)
        assertFalse(tc.active(TestDomain.SLEEP))
    }

    @Test fun masterImpliesAll() {
        val tc = newCentre()
        tc.activate(TestDomain.MASTER)
        assertTrue(tc.active(TestDomain.SLEEP))
        assertTrue(tc.active(TestDomain.HRV))
    }

    @Test fun universalRidesAnyActive() {
        val tc = newCentre()
        assertFalse(tc.active(TestDomain.UNIVERSAL))
        tc.activate(TestDomain.BATTERY)
        assertTrue(tc.active(TestDomain.UNIVERSAL))
    }

    @Test fun startedAtStampedOnActivate() {
        val tc = newCentre()
        assertNull(tc.startedAt(TestDomain.SLEEP))
        tc.activate(TestDomain.SLEEP)
        assertTrue((tc.startedAt(TestDomain.SLEEP) ?: 0L) > 0L)
    }

    @Test fun stepsActivationCreatesOneStableExportSessionIdentity() {
        val tc = newCentre()
        tc.activate(TestDomain.STEPS, deviceId = "strap-a")
        val first = tc.captureSession(TestDomain.STEPS)
        assertTrue(first != null)
        assertTrue(first!!.id.startsWith("steps-"))
        assertEquals(tc.startedAt(TestDomain.STEPS), first.startedAt)
        assertEquals("strap-a", first.deviceId)
        assertEquals(first, tc.captureSession(TestDomain.STEPS))
    }

    @Test fun legacySessionWithoutDeviceNeverInventsTheCurrentStrap() {
        val tc = newCentre()
        tc.activate(TestDomain.STEPS)
        assertNull(tc.captureSession(TestDomain.STEPS)?.deviceId)
    }

    @Test fun activeLegacyStepsTestGetsSessionAtAppStartNotExportTime() {
        val prefs = FakeSharedPreferences()
        val tc = TestCentre(prefs)
        tc.activate(TestDomain.STEPS)
        prefs.map.remove("testcentre.session.steps")

        val repaired = tc.startMissingCaptureSession(
            TestDomain.STEPS,
            deviceId = "strap-a",
            nowMs = 1_787_330_400_000L,
        )

        assertEquals("steps-1787330400000", repaired?.id)
        assertEquals(1_787_330_400L, repaired?.startedAt)
        assertEquals("strap-a", repaired?.deviceId)
        assertEquals(repaired, tc.captureSession(TestDomain.STEPS))
        assertNull(tc.startMissingCaptureSession(TestDomain.STEPS, "strap-b", nowMs = 1_787_330_500_000L))
        assertEquals("strap-a", tc.captureSession(TestDomain.STEPS)?.deviceId)
    }

    @Test fun answersRoundTrip() {
        val tc = newCentre()
        assertEquals(emptyMap<String, String>(), tc.answers(TestDomain.BATTERY))
        tc.setAnswers(TestDomain.BATTERY, mapOf("whoopAppInstalled" to "yes", "batterySaverApps" to "none"))
        assertEquals(mapOf("whoopAppInstalled" to "yes", "batterySaverApps" to "none"), tc.answers(TestDomain.BATTERY))
    }

    @Test fun migrationIsIdempotentAndPreservesLegacyKeys() {
        // A legacy experiments key set "before" lives in its OWN prefs store (noop_experiments). The Test
        // Centre prefs file is separate; migrate() must never reach into or wipe the legacy store.
        val legacy = FakeSharedPreferences()
        legacy.edit().putBoolean("noopWhoop5DeepData", true).apply()
        val tc = TestCentre(FakeSharedPreferences())
        tc.migrate()
        tc.migrate()
        assertTrue(legacy.getBoolean("noopWhoop5DeepData", false))   // NOT renamed/wiped by migration
    }
}
