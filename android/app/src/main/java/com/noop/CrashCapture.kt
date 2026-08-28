package com.noop

import android.content.Context
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.security.MessageDigest

/**
 * Captures the last uncaught exception to a file so a crash that only reproduces on a user's own
 * device — a deterministic crash on a specific data shape, like the Insights tab (#224/#267) — lands
 * in the shareable strap log instead of being lost to a logcat no one can reach without adb. The
 * handler records the trace, then chains to the previous handler so the process still dies normally
 * (we never swallow the crash). [LogExport] appends [lastCrash] to the strap log header.
 */
object CrashCapture {
    private const val FILE = "last_crash.txt"
    private const val PREFS = "noop_crash_capture"
    private const val ACKNOWLEDGED = "acknowledged_fingerprint"

    fun install(context: Context) {
        val appContext = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            // The handler itself must never throw, or we replace one crash with another.
            runCatching {
                val sw = StringWriter()
                throwable.printStackTrace(PrintWriter(sw))
                val text = buildString {
                    appendLine("when:   ${java.util.Date()}")
                    appendLine("thread: ${thread.name}")
                    appendLine(sw.toString())
                }
                File(appContext.filesDir, FILE).writeText(text)
            }
            previous?.uncaughtException(thread, throwable)
        }
    }

    /** The captured crash text, or null if there hasn't been one. Surfaced by [LogExport]. */
    fun lastCrash(context: Context): String? {
        val f = File(context.applicationContext.filesDir, FILE)
        if (!f.exists()) return null
        return runCatching { f.readText() }.getOrNull()?.ifBlank { null }
    }

    /** A crash not yet dismissed by the user, shown before launch touches the database or BLE stack. */
    fun pendingCrash(context: Context): String? {
        val crash = lastCrash(context) ?: return null
        val acknowledged = context.applicationContext
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(ACKNOWLEDGED, null)
        return crash.takeIf { isPending(fingerprint(it), acknowledged) }
    }

    /** Keep the crash for diagnostics, but allow the next launch attempt to continue. */
    fun acknowledge(context: Context, crash: String) {
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(ACKNOWLEDGED, fingerprint(crash)).apply()
    }

    internal fun isPending(fingerprint: String, acknowledged: String?) = fingerprint != acknowledged

    internal fun fingerprint(text: String): String = MessageDigest.getInstance("SHA-256")
        .digest(text.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
