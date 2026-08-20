package com.noop.ui

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Test

class LogExportDeepBufferTest {
    @Test fun rotatedCaptureGenerationsAreExportedOldestFirst() {
        val dir = Files.createTempDirectory("noop-deep-export").toFile()
        try {
            val main = File(dir, "whoop5-deepbuffers.jsonl").apply { writeText("new\n") }
            File(dir, "whoop5-deepbuffers.jsonl.1").writeText("old\n")
            val out = File(dir, "combined.jsonl")
            LogExport.combineRotatedJsonl(main, out)
            assertEquals("old\nnew\n", out.readText())
        } finally {
            dir.deleteRecursively()
        }
    }
}
