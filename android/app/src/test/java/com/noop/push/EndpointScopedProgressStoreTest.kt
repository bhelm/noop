package com.noop.push

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EndpointScopedProgressStoreTest {
    @Test fun endpointChangeIsolatesProgressWhileSameEndpointCanRotateToken() = runBlocking {
        val underlying = MemoryProgress()
        val first = EndpointScopedProgressStore(underlying, "endpoint-a")
        val sameUrlNewToken = EndpointScopedProgressStore(underlying, "endpoint-a")
        val changedUrl = EndpointScopedProgressStore(underlying, "endpoint-b")
        val cursor = PushCursor(42, "natural-key")

        first.saveCursor(PushAppendTable.HR_SAMPLE, "strap", cursor)

        assertEquals(cursor, sameUrlNewToken.cursor(PushAppendTable.HR_SAMPLE, "strap"))
        assertNull(changedUrl.cursor(PushAppendTable.HR_SAMPLE, "strap"))
    }
}
