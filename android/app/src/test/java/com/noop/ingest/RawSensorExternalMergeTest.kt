package com.noop.ingest

import java.io.StringReader
import java.io.StringWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RawSensorExternalMergeTest {
    @Test fun kWayMergeKeepsGlobalTimestampOrderWithOneHeadPerStream() {
        val out = StringWriter()
        var maxHeads = 0
        RawSensorExport.mergeSortedCsvStreams(
            inputs = listOf(
                StringReader(RawSensorExport.encodeStagedLine(1, "1,a,hr") + "\n" + RawSensorExport.encodeStagedLine(4, "4,d,hr") + "\n"),
                StringReader(RawSensorExport.encodeStagedLine(2, "2,b,steps") + "\n" + RawSensorExport.encodeStagedLine(3, "3,c,steps") + "\n"),
                StringReader(RawSensorExport.encodeStagedLine(2, "2,b2,gravity") + "\n"),
            ),
            out = out,
            onBufferedHeads = { maxHeads = maxOf(maxHeads, it) },
        )
        assertEquals(
            listOf("1,a,hr", "2,b,steps", "2,b2,gravity", "3,c,steps", "4,d,hr"),
            out.toString().lineSequence().filter { it.isNotEmpty() }.toList(),
        )
        assertTrue(maxHeads <= 3)
    }

    @Test fun stagedEncodingPreservesQuotedMultilineCsvFields() {
        val csv = "7,t,event,,,,\"line one\nline two\""
        val out = StringWriter()
        RawSensorExport.mergeSortedCsvStreams(
            listOf(StringReader(RawSensorExport.encodeStagedLine(7, csv) + "\n")), out,
        )
        assertEquals(csv + "\n", out.toString())
    }

    @Test fun sessionWindowCapsForgottenSessionsAndNamesThatFact() {
        assertEquals(RawSensorExport.SessionWindow(100, false), RawSensorExport.sessionWindow(100, 200))
        assertEquals(
            RawSensorExport.SessionWindow(100_000 - 86_400, true),
            RawSensorExport.sessionWindow(1, 100_000),
        )
    }
}
