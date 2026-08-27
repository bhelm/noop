package com.noop.testcentre

import com.noop.data.ImuChunkEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlinx.coroutines.runBlocking

/** Small source-seam guards for the raw-capture ownership and bounded-export contract. */
class RawCaptureExportContractTest {
    private fun chunk(id: String, from: Long, to: Long) = ImuChunkEntity(
        id, "strap", from, to, ((to - from) + 1).toInt() * 100, 100, 1, "zip-deflate",
        "imu-chunks/$id.imuc", 1, "sha", 1, Long.MAX_VALUE,
    )

    private fun source(name: String): String {
        var root = File(System.getProperty("user.dir") ?: ".").canonicalFile
        repeat(4) {
            val file = File(root, "android/app/src/main/java/com/noop/testcentre/$name")
            if (file.isFile) return file.readText()
            root = root.parentFile ?: root
        }
        error("repository root not found")
    }

    @Test fun editedWindowOwnsPublicEventsAndImuBounds() {
        val collector = source("GroundTruthCollector.kt")
        assertTrue(collector.contains("publicEvents(events, summary.startedAtMs, endMs, deviceId)"))
        assertTrue(collector.contains("summary.markers.filter { it.atMs in summary.startedAtMs..endMs }"))
        assertTrue(collector.contains("val sensorFrom = ceilSecond(summary.startedAtMs)"))
        assertTrue(collector.contains("val sensorTo = endMs / 1_000L - 1L"))
        assertTrue(collector.contains("val coverageFrom = sensorFrom"))
        assertFalse(collector.contains("maxOf(fullFrom, pinnedChunks.minOfOrNull"))
        assertTrue(collector.contains("val sensorAvailable = pinnedChunks.isNotEmpty() ||"))
        assertFalse(collector.contains("put(\"sensor_export_available\", deviceId != null)"))
    }

    @Test fun sessionPayloadDeletionKeepsDiscoveryFileUntilLast() {
        val collector = source("GroundTruthCollector.kt")
        assertTrue(collector.contains("val payloadFiles = listOf("))
        assertTrue(collector.contains("if (!payloadFiles.all"))
        assertTrue(collector.contains("if (eventFile(sessionId).exists() && !eventFile(sessionId).delete()) return false"))
    }

    @Test fun rawCollectorHasNoStepAlgorithmExport() {
        val collector = source("GroundTruthCollector.kt")
        assertFalse(collector.contains("algorithm-signals.csv"))
        assertFalse(collector.contains("writeAlgorithmSignalsCsv"))
        assertFalse(collector.contains("stepSamples("))
    }

    @Test fun chunksAreSessionOwnedAndLiveSourceSurvivesExport() {
        val chunks = source("ImuChunkStore.kt")
        val collector = source("GroundTruthCollector.kt")
        assertTrue(chunks.contains("chunkPrefix(sessionId)"))
        assertTrue(chunks.contains("put(\"axes\", JSONArray("))
        assertTrue(chunks.contains("suspend fun deleteOwned("))
        assertTrue(collector.contains(".deleteOwned("))
        assertFalse(collector.contains("if (imuComplete) {\n            ImuSessionFileStore(context).remove(id)"))
    }

    @Test fun emptySelectedSourceNeverReusesAnOverlappingBroadArchive() {
        val broad = chunk("first--broad", 100, 199)
        assertEquals(emptyList<ImuChunkEntity>(), ImuChunkStore.boundedExisting(listOf(broad), 120, 140))
    }

    @Test fun ownershipPrefixDoesNotClaimAnOverlappingSession() {
        assertTrue(ImuChunkStore.isOwnedChunk("first", "first--archive"))
        assertFalse(ImuChunkStore.isOwnedChunk("first", "first-extra--archive"))
    }

    @Test fun unrelatedArchivesAreNotClaimedAsSessionOwned() {
        assertFalse(ImuChunkStore.isOwnedChunk("first", "550e8400-e29b-41d4-a716-446655440000"))
    }

    @Test fun reuseIsStrictlyContainedBySelectedRange() {
        val inside = chunk("session--inside", 120, 140)
        val broad = chunk("session--broad", 100, 199)
        assertEquals(listOf(inside), ImuChunkStore.boundedExisting(listOf(inside, broad), 120, 140))
    }

    @Test fun archiveDeletionFailureLeavesCatalogUntouchedForRetry() = runBlocking {
        val operations = mutableListOf<String>()
        val deleted = ImuChunkStore.deleteRetrySafe(
            fileExists = true,
            deleteFile = { operations += "file"; false },
            deleteCatalog = { operations += "catalog" },
        )
        assertFalse(deleted)
        assertEquals(listOf("file"), operations)
    }

    @Test fun catalogDeletionRunsOnlyAfterArchiveDeletion() = runBlocking {
        val operations = mutableListOf<String>()
        val deleted = ImuChunkStore.deleteRetrySafe(
            fileExists = true,
            deleteFile = { operations += "file"; true },
            deleteCatalog = { operations += "catalog"; error("database unavailable") },
        )
        assertFalse(deleted)
        assertEquals(listOf("file", "catalog"), operations)
    }

    @Test fun deviceDeletionOwnsExternalFilesBeforeDatabaseWipe() {
        val chunks = source("ImuChunkStore.kt")
        assertTrue(chunks.contains("suspend fun deleteDevice(deviceId: String)"))
        assertTrue(chunks.indexOf("if (file.exists() && !file.delete()) return false") <
            chunks.indexOf("repository.deleteImuChunksFor(deviceId)"))
    }
}
