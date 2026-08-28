package com.noop.data

import java.lang.reflect.Proxy
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

/** DAO-backed contract tests for the public device-aware repository reads. */
class RawTimelineRepositoryDaoTest {
    private fun rr(source: String, ts: Long, ms: Int) =
        RrInterval(deviceId = source, ts = ts, rrMs = ms)

    @Test
    fun rrIntervalsUnionQueriesActiveThenCanonicalAndMergesTheirRows() = runBlocking {
        val queried = mutableListOf<String>()
        val active = listOf(rr("whoop-new", 101, 810), rr("whoop-new", 103, 830))
        val canonical = listOf(rr("my-whoop", 100, 800), rr("my-whoop", 101, 810))
        val repo = WhoopRepository(proxyDao { method, args ->
            when (method) {
                "rrIntervals" -> {
                    val id = args[0] as String
                    queried += id
                    if (id == "whoop-new") active else canonical
                }
                else -> throw UnsupportedOperationException(method)
            }
        })

        val rows = repo.rrIntervalsUnion("whoop-new", 10, 20, limit = 50)

        assertEquals(listOf("whoop-new", "my-whoop"), queried)
        assertEquals(listOf(100L, 101L, 103L), rows.map { it.ts })
        assertEquals("whoop-new", rows.first { it.ts == 101L }.deviceId)
    }

    @Test
    fun rrIntervalsUnionCanonicalActivePerformsOneLegacyReadUnchanged() = runBlocking {
        val queried = mutableListOf<String>()
        val canonical = listOf(rr("my-whoop", 100, 800), rr("my-whoop", 101, 810))
        val repo = WhoopRepository(proxyDao { method, args ->
            when (method) {
                "rrIntervals" -> {
                    queried += args[0] as String
                    canonical
                }
                else -> throw UnsupportedOperationException(method)
            }
        })

        val rows = repo.rrIntervalsUnion("my-whoop", 10, 20, limit = 50)

        assertEquals(listOf("my-whoop"), queried)
        assertSame("single-source legacy result must pass through unchanged", canonical, rows)
    }

    @Test
    fun sessionMotionsPrefersOldSessionOwnerOverSameStartFallbacks() = runBlocking {
        val queried = mutableListOf<Pair<String, Long>>()
        val start = 1_000L
        val values = mapOf(
            "whoop-old-noop" to "[1,2]",
            "whoop-new-noop" to "[8,8]",
            "my-whoop-noop" to "[9,9]",
        )
        val repo = WhoopRepository(proxyDao { method, args ->
            when (method) {
                "sessionMotionJson" -> {
                    val id = args[0] as String
                    queried += id to (args[1] as Long)
                    values[id]
                }
                else -> throw UnsupportedOperationException(method)
            }
        })

        val result = repo.sessionMotions(
            activeStrapId = "whoop-new",
            sessions = listOf(SleepSession(deviceId = "whoop-old", startTs = start, endTs = start + 3600)),
        )

        assertEquals(listOf("whoop-old-noop" to start), queried)
        assertEquals(listOf(1.0, 2.0), result[start])
    }

    private fun proxyDao(call: (String, Array<out Any?>) -> Any?): WhoopDao =
        Proxy.newProxyInstance(
            WhoopDao::class.java.classLoader,
            arrayOf(WhoopDao::class.java),
        ) { _, method, args -> call(method.name, args ?: emptyArray()) } as WhoopDao
}
