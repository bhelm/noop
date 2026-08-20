package com.noop.push

import org.junit.Assert.assertTrue
import org.junit.Test

class PushDaoPreflightTest {
    @Test fun sqlEstimateUsesUtf8BlobLengthWorstCaseEscapingAndFixedRowOverhead() {
        val expression = PushSnapshotPreflight.rowEstimateExpression(listOf("notes", "payloadJSON"))

        assertTrue(expression.contains(PushSnapshotPreflight.FIXED_ROW_OVERHEAD_BYTES.toString()))
        assertTrue(expression.contains("length(CAST(notes AS BLOB)) * 6"))
        assertTrue(expression.contains("length(CAST(payloadJSON AS BLOB)) * 6"))
        assertTrue(expression.contains("typeof(notes) = 'blob'"))
    }

    @Test fun preflightQueryBoundsTheExactOrderedLimitedSelection() {
        val sql = PushSnapshotPreflight.query(
            table = "event",
            columns = listOf("ts", "kind", "payloadJSON"),
            predicate = "deviceId = ? AND rowid > ?",
            orderBy = "rowid ASC",
        )

        assertTrue(sql.contains("FROM (SELECT ts, kind, payloadJSON FROM event"))
        assertTrue(sql.contains("WHERE deviceId = ? AND rowid > ? ORDER BY rowid ASC LIMIT ?)"))
        assertTrue(sql.startsWith("SELECT COALESCE(SUM("))
    }
}
