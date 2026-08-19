package com.noop.push

import android.content.SharedPreferences
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfHostedPushSettingsTest {
    @Test fun defaultsOffAndCannotEnableWithoutEndpointAndToken() {
        val settings = SelfHostedPushSettings.forTest(FakePushPrefs(), FakePushPrefs())
        assertFalse(settings.snapshot().enabled)
        assertFalse(settings.setEnabled(true))
    }

    @Test fun blankTokenRemovesEncryptedCredentialAndTurnsReadinessOff() {
        val plain = FakePushPrefs()
        val encrypted = FakePushPrefs()
        val settings = SelfHostedPushSettings.forTest(plain, encrypted)
        settings.saveEndpoint("https://example.com/push")
        settings.saveToken("secret")
        assertTrue(settings.setEnabled(true))
        assertTrue(settings.snapshot().ready)

        settings.saveToken("   ")

        assertNull(settings.token())
        assertFalse(settings.snapshot().ready)
    }

    @Test fun capturedEndpointKeepsItsOwnProgressNamespaceAcrossConcurrentEdit() {
        val settings = SelfHostedPushSettings.forTest(FakePushPrefs(), FakePushPrefs())
        val first = (PushEndpointPolicy.validate("https://one.example/push") as PushEndpointPolicy.Result.Valid).endpoint
        val second = (PushEndpointPolicy.validate("https://two.example/push") as PushEndpointPolicy.Result.Valid).endpoint

        settings.saveEndpoint(first.url)
        val firstNamespace = settings.progressNamespace(SOURCE_A, first)
        settings.saveEndpoint(second.url)

        assertNotEquals(firstNamespace, settings.progressNamespace(SOURCE_A, second))
        assertTrue(firstNamespace == settings.progressNamespace(SOURCE_A, first))
    }

    internal class FakePushPrefs : SharedPreferences {
        private val map = HashMap<String, Any?>()
        override fun getBoolean(key: String, defValue: Boolean) = map[key] as? Boolean ?: defValue
        override fun getLong(key: String, defValue: Long) = map[key] as? Long ?: defValue
        override fun getString(key: String, defValue: String?) = map[key] as? String ?: defValue
        override fun getInt(key: String, defValue: Int) = map[key] as? Int ?: defValue
        override fun getFloat(key: String, defValue: Float) = map[key] as? Float ?: defValue
        @Suppress("UNCHECKED_CAST")
        override fun getStringSet(key: String, defValues: MutableSet<String>?) =
            map[key] as? MutableSet<String> ?: defValues
        override fun getAll(): MutableMap<String, *> = HashMap(map)
        override fun contains(key: String) = map.containsKey(key)
        override fun registerOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit
        override fun unregisterOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit
        override fun edit(): SharedPreferences.Editor = Editor()

        private inner class Editor : SharedPreferences.Editor {
            private val pending = HashMap<String, Any?>()
            private val removals = HashSet<String>()
            override fun putString(key: String, value: String?) = apply { pending[key] = value }
            override fun putStringSet(key: String, values: MutableSet<String>?) = apply { pending[key] = values }
            override fun putInt(key: String, value: Int) = apply { pending[key] = value }
            override fun putLong(key: String, value: Long) = apply { pending[key] = value }
            override fun putFloat(key: String, value: Float) = apply { pending[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { pending[key] = value }
            override fun remove(key: String) = apply { removals += key }
            override fun clear() = apply { map.clear() }
            override fun commit(): Boolean { flush(); return true }
            override fun apply() = flush()
            private fun flush() {
                removals.forEach(map::remove)
                map.putAll(pending)
            }
        }
    }
}
