package com.noop.push

import android.content.Context
import android.content.SharedPreferences
import com.noop.data.SecurePrefs
import java.security.MessageDigest
import java.util.UUID

/** Stores configuration without ever placing the bearer token in ordinary preferences. */
class SelfHostedPushSettings private constructor(
    private val prefs: SharedPreferences,
    private val secrets: Lazy<SharedPreferences>,
) {
    data class Snapshot(
        val enabled: Boolean,
        val endpoint: PushEndpointPolicy.ValidEndpoint?,
        val hasToken: Boolean,
        val lastSuccessAt: Long?,
        val lastError: String?,
    ) {
        val ready: Boolean get() = enabled && endpoint != null && hasToken
    }

    fun snapshot(): Snapshot {
        val endpoint = (PushEndpointPolicy.validate(prefs.getString(KEY_ENDPOINT, "").orEmpty()) as? PushEndpointPolicy.Result.Valid)?.endpoint
        return Snapshot(
            enabled = prefs.getBoolean(KEY_ENABLED, false),
            endpoint = endpoint,
            hasToken = !secrets.value.getString(KEY_TOKEN, null).isNullOrBlank(),
            lastSuccessAt = prefs.getLong(KEY_LAST_SUCCESS, 0L).takeIf { it > 0 },
            lastError = prefs.getString(KEY_LAST_ERROR, null),
        )
    }

    fun endpointText(): String = prefs.getString(KEY_ENDPOINT, "").orEmpty()
    /** Plain-pref gate used by stale workers before opening Room or Android Keystore. */
    fun enabledEndpoint(): PushEndpointPolicy.ValidEndpoint? {
        if (!prefs.getBoolean(KEY_ENABLED, false)) return null
        return (PushEndpointPolicy.validate(endpointText()) as? PushEndpointPolicy.Result.Valid)?.endpoint
    }

    fun token(): String? = secrets.value.getString(KEY_TOKEN, null)?.takeIf { it.isNotBlank() }

    /** Stable, non-secret receiver namespace. Generated only after the worker's stale-work gates pass. */
    @Synchronized
    fun sourceId(): String {
        prefs.getString(KEY_SOURCE_ID, null)?.let { existing ->
            runCatching { UUID.fromString(existing) }.getOrNull()?.let { return it.toString() }
        }
        val generated = UUID.randomUUID().toString()
        check(prefs.edit().putString(KEY_SOURCE_ID, generated).commit()) { "Could not persist push source id" }
        return generated
    }

    /** Saving a different normalized URL changes the progress namespace; token rotation does not. */
    fun saveEndpoint(raw: String): PushEndpointPolicy.Result {
        val validation = PushEndpointPolicy.validate(raw)
        val normalized = (validation as? PushEndpointPolicy.Result.Valid)?.endpoint?.url
            ?: return validation
        prefs.edit().putString(KEY_ENDPOINT, normalized).apply()
        return validation
    }

    fun saveToken(token: String) {
        val trimmed = token.trim()
        secrets.value.edit().let { edit ->
            if (trimmed.isEmpty()) edit.remove(KEY_TOKEN) else edit.putString(KEY_TOKEN, trimmed)
        }.apply()
    }

    fun setEnabled(enabled: Boolean): Boolean {
        if (enabled && !snapshot().copy(enabled = true).ready) return false
        prefs.edit().putBoolean(KEY_ENABLED, enabled).apply()
        return true
    }

    fun progressNamespace(sourceId: String, endpoint: PushEndpointPolicy.ValidEndpoint): String =
        MessageDigest.getInstance("SHA-256").digest("$sourceId\u0000${endpoint.url}".toByteArray())
            .take(12).joinToString("") { "%02x".format(it) }

    fun recordSuccess(atMillis: Long = System.currentTimeMillis()) {
        prefs.edit().putLong(KEY_LAST_SUCCESS, atMillis).remove(KEY_LAST_ERROR).apply()
    }

    fun recordError(message: String) {
        prefs.edit().putString(KEY_LAST_ERROR, message.take(MAX_STATUS_CHARS)).apply()
    }

    fun nextDeviceIndex(namespace: String): Int = prefs.getInt("$KEY_NEXT_DEVICE.$namespace", 0).coerceAtLeast(0)

    fun saveNextDeviceIndex(namespace: String, index: Int) {
        require(index >= 0)
        prefs.edit().putInt("$KEY_NEXT_DEVICE.$namespace", index).apply()
    }

    fun cycleNeedsAnotherPass(namespace: String): Boolean =
        prefs.getBoolean("$KEY_CYCLE_MORE.$namespace", false)

    fun saveCycleNeedsAnotherPass(namespace: String, needed: Boolean) {
        prefs.edit().putBoolean("$KEY_CYCLE_MORE.$namespace", needed).apply()
    }

    fun cycleHadRejection(namespace: String): Boolean = prefs.getBoolean("$KEY_CYCLE_REJECTED.$namespace", false)

    fun saveCycleHadRejection(namespace: String, rejected: Boolean) {
        prefs.edit().putBoolean("$KEY_CYCLE_REJECTED.$namespace", rejected).apply()
    }

    companion object {
        private const val PREFS = "self_hosted_push"
        private const val SECRETS = "self_hosted_push_secrets"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_ENDPOINT = "endpoint"
        private const val KEY_TOKEN = "bearer_token"
        private const val KEY_SOURCE_ID = "source_id"
        private const val KEY_LAST_SUCCESS = "last_success_at"
        private const val KEY_LAST_ERROR = "last_error"
        private const val KEY_NEXT_DEVICE = "next_device"
        private const val KEY_CYCLE_MORE = "cycle_more"
        private const val KEY_CYCLE_REJECTED = "cycle_rejected"
        private const val MAX_STATUS_CHARS = 300

        fun from(context: Context) = SelfHostedPushSettings(
            context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE),
            lazy(LazyThreadSafetyMode.SYNCHRONIZED) { SecurePrefs.of(context.applicationContext, SECRETS) },
        )

        internal fun forTest(prefs: SharedPreferences, secrets: SharedPreferences) =
            SelfHostedPushSettings(prefs, lazyOf(secrets))
    }
}
