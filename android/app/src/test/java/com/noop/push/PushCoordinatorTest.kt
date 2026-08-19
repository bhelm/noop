package com.noop.push

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class PushCoordinatorTest {
    @Test
    fun exactAckIsRequiredBeforeCursorAdvances() = runBlocking {
        val row = hr(1, 100)
        val source = FakePushSource(append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(row)))
        val progress = MemoryProgress()
        val partial = AckingTransport { batch -> PushAck.fromBatch(batch).copy(acceptedRows = 0) }

        val result = PushCoordinator(source, partial, progress, SOURCE_A).pushAppend(PushAppendTable.HR_SAMPLE, "a")

        assertTrue(result is PushResult.Rejected)
        assertTrue(progress.cursors.isEmpty())
    }

    @Test
    fun acceptedStatusAndFullCursorFingerprintMustMatch() = runBlocking {
        val row = hr(1, 100)
        val source = FakePushSource(append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(row)))
        val progress = MemoryProgress()
        val wrongStatus = AckingTransport { batch -> PushAck.fromBatch(batch).copy(status = "partial") }

        val result = PushCoordinator(source, wrongStatus, progress, SOURCE_A)
            .pushAppend(PushAppendTable.HR_SAMPLE, "a")

        assertTrue(result is PushResult.Rejected)
        assertTrue(progress.cursors.isEmpty())
    }

    @Test
    fun malformedAckLeavesProgressAndRetryBytesUnchanged() = runBlocking {
        val source = FakePushSource(
            append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
        )
        val progress = MemoryProgress()
        val malformed = object : PushTransport {
            val bodies = mutableListOf<ByteArray>()
            override suspend fun post(batch: PushBatch): PushTransportResponse {
                bodies += batch.body.copyOf()
                return PushTransportResponse(200, "{not-json".toByteArray())
            }
        }
        val coordinator = PushCoordinator(source, malformed, progress, SOURCE_A)

        coordinator.pushAppend(PushAppendTable.HR_SAMPLE, "a")
        coordinator.pushAppend(PushAppendTable.HR_SAMPLE, "a")

        assertTrue(progress.cursors.isEmpty())
        assertArrayEquals(malformed.bodies[0], malformed.bodies[1])
    }

    @Test
    fun oversizedAckIsRejectedBeforeParsing() = runBlocking {
        val source = FakePushSource(
            append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
        )
        val progress = MemoryProgress()
        val transport = object : PushTransport {
            override suspend fun post(batch: PushBatch) = PushTransportResponse(
                200,
                ByteArray(PushProtocol.MAX_ACK_BYTES + 1) { 'x'.code.toByte() },
            )
        }

        val result = PushCoordinator(source, transport, progress, SOURCE_A).pushAppend(PushAppendTable.HR_SAMPLE, "a")

        assertTrue(result is PushResult.Rejected)
        assertTrue(progress.cursors.isEmpty())
    }

    @Test
    fun ackWithUndocumentedCommandIsRejectedWithoutAdvancing() = runBlocking {
        val source = FakePushSource(
            append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
        )
        val progress = MemoryProgress()
        val transport = object : PushTransport {
            override suspend fun post(batch: PushBatch): PushTransportResponse {
                val accepted = PushAck.fromBatch(batch).encode().toString(Charsets.UTF_8)
                val withCommand = accepted.dropLast(1) + ",\"command\":\"delete-local-data\"}"
                return PushTransportResponse(200, withCommand.toByteArray())
            }
        }

        val result = PushCoordinator(source, transport, progress, SOURCE_A)
            .pushAppend(PushAppendTable.HR_SAMPLE, "a")

        assertTrue(result is PushResult.Rejected)
        assertTrue(progress.cursors.isEmpty())
    }

    @Test
    fun httpAndTransportFailuresNeverAdvanceAndRetryOnlyTransientClasses() = runBlocking {
        val expected = listOf(401 to false, 408 to true, 429 to true, 500 to true)
        for ((status, retryable) in expected) {
            val source = FakePushSource(
                append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
            )
            val progress = MemoryProgress()
            val transport = object : PushTransport {
                override suspend fun post(batch: PushBatch) = PushTransportResponse(status, ByteArray(0))
            }

            val result = PushCoordinator(source, transport, progress, SOURCE_A)
                .pushAppend(PushAppendTable.HR_SAMPLE, "a")

            assertTrue("HTTP $status", result is PushResult.Rejected && result.retryable == retryable)
            assertTrue("HTTP $status moved cursor", progress.cursors.isEmpty())
        }

        val progress = MemoryProgress()
        val throwing = object : PushTransport {
            override suspend fun post(batch: PushBatch): PushTransportResponse = throw java.io.IOException("timeout")
        }
        val result = PushCoordinator(
            FakePushSource(
                append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
            ),
            throwing,
            progress,
            SOURCE_A,
        ).pushAppend(PushAppendTable.HR_SAMPLE, "a")
        assertTrue(result is PushResult.Rejected && result.retryable)
        assertTrue(progress.cursors.isEmpty())
    }

    @Test
    fun emptyMutableWindowPropagatesDeletionAndAdvancesOnlyOnExactAck() = runBlocking {
        val source = FakePushSource()
        val progress = MemoryProgress()
        val transport = AckingTransport()
        val coordinator = PushCoordinator(
            source,
            transport,
            progress,
            SOURCE_A,
            today = { LocalDate.of(2026, 8, 18) },
            zoneId = ZoneId.of("Europe/Berlin"),
        )

        val result = coordinator.pushMutable(PushMutableTable.JOURNAL, "noop-journal")

        assertTrue(result is PushResult.Accepted)
        assertEquals(0, transport.batches.single().recordCount)
        assertEquals("replace_window", transport.batches.single().mode)
        assertEquals("2026-08-05", transport.batches.single().window?.fromDay)
        assertEquals(
            transport.batches.single().replacementId,
            progress.windows[key(PushMutableTable.JOURNAL, "noop-journal")]?.batchId,
        )
    }

    @Test
    fun databaseSnapshotEndsBeforeTransportStarts() = runBlocking {
        val source = FakePushSource(
            append = mutableMapOf(key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100))),
        )
        val transport = object : PushTransport {
            override suspend fun post(batch: PushBatch): PushTransportResponse {
                assertFalse("database read transaction must be closed before HTTP", source.reading)
                return PushTransportResponse(200, PushAck.fromBatch(batch).encode())
            }
        }

        PushCoordinator(source, transport, MemoryProgress(), SOURCE_A)
            .pushAppend(PushAppendTable.HR_SAMPLE, "a")
        Unit
    }

    @Test
    fun oversizedMutableWindowFailsBeforeHttpWithoutUnboundedAccumulation() = runBlocking {
        val huge = PushMutableRecord(
            linkedMapOf("day" to "2026-08-18", "question" to "large"),
            linkedMapOf(
                "answeredYes" to true,
                "notes" to "x".repeat(PushProtocol.MAX_MUTABLE_SNAPSHOT_ENCODED_BYTES + 1),
                "numericValue" to null,
            ),
        )
        val source = FakePushSource(
            mutable = mutableMapOf(key(PushMutableTable.JOURNAL, "a") to mutableListOf(huge)),
        )
        val transport = AckingTransport()

        val result = PushCoordinator(source, transport, MemoryProgress(), SOURCE_A)
            .pushMutable(PushMutableTable.JOURNAL, "a")

        assertTrue(result is PushResult.Rejected && !result.retryable)
        assertTrue(transport.batches.isEmpty())
    }

    @Test
    fun boundedDeviceRotationGuaranteesLaterDeviceProgress() = runBlocking {
        val source = FakePushSource(
            append = mutableMapOf(
                key(PushAppendTable.HR_SAMPLE, "a") to mutableListOf(hr(1, 100)),
                key(PushAppendTable.HR_SAMPLE, "b") to mutableListOf(hr(2, 200)),
            ),
        )
        val progress = MemoryProgress()
        val firstTransport = AckingTransport()
        val first = PushCoordinator(source, firstTransport, progress, SOURCE_A)
            .pushKnownDevices(startDeviceIndex = 0, maxDevices = 1)

        assertTrue(first.hasMoreDevices)
        assertEquals(1, first.nextDeviceIndex)
        assertTrue(firstTransport.batches.isNotEmpty())
        assertTrue(firstTransport.batches.all { it.deviceId == "a" })

        val secondTransport = AckingTransport()
        val second = PushCoordinator(source, secondTransport, progress, SOURCE_A)
            .pushKnownDevices(startDeviceIndex = first.nextDeviceIndex, maxDevices = 1)

        assertEquals(0, second.nextDeviceIndex)
        assertTrue(secondTransport.batches.isNotEmpty())
        assertTrue(secondTransport.batches.all { it.deviceId == "b" })
    }

    @Test
    fun rememberedDeviceSendsEmptyReplacementAfterItsLastMutableRowIsDeleted() = runBlocking {
        val source = FakePushSource(
            mutable = mutableMapOf(
                key(PushMutableTable.JOURNAL, "noop-journal") to mutableListOf(
                    PushMutableRecord(
                        linkedMapOf("day" to "2026-08-18", "question" to "caffeine"),
                        linkedMapOf("answeredYes" to true, "notes" to null, "numericValue" to null),
                    ),
                ),
            ),
        )
        val progress = MemoryProgress()
        PushCoordinator(source, AckingTransport(), progress, SOURCE_A)
            .pushKnownDevices()
        source.mutable.remove(key(PushMutableTable.JOURNAL, "noop-journal"))

        val afterDelete = AckingTransport()
        PushCoordinator(source, afterDelete, progress, SOURCE_A)
            .pushKnownDevices()

        val journal = afterDelete.batches.single {
            it.deviceId == "noop-journal" && it.table == PushMutableTable.JOURNAL
        }
        assertEquals(0, journal.recordCount)
        assertEquals("replace_window", journal.mode)
    }
}

internal fun key(table: PushTable, deviceId: String) = "${table.wireName}|$deviceId"

internal fun hr(rowId: Long, ts: Long) = PushAppendRecord(
    rowId,
    linkedMapOf("ts" to ts),
    linkedMapOf("bpm" to 60),
)

internal class FakePushSource(
    val append: MutableMap<String, MutableList<PushAppendRecord>> = mutableMapOf(),
    val mutable: MutableMap<String, MutableList<PushMutableRecord>> = mutableMapOf(),
) : PushSnapshotSource {
    val afterCursors = mutableListOf<Long>()
    var reading = false

    override suspend fun knownDeviceIds(): List<String> =
        (append.keys + mutable.keys).map { it.substringAfter('|') }.distinct().sorted()

    override suspend fun appendRecordAt(
        table: PushAppendTable,
        deviceId: String,
        rowId: Long,
    ): PushAppendRecord? = append[key(table, deviceId)]?.firstOrNull { it.rowId == rowId }

    override suspend fun appendRows(
        table: PushAppendTable,
        deviceId: String,
        afterRowId: Long,
        limit: Int,
    ): List<PushAppendRecord> {
        reading = true
        return try {
            afterCursors += afterRowId
            append[key(table, deviceId)].orEmpty().filter { it.rowId > afterRowId }.take(limit)
        } finally {
            reading = false
        }
    }

    override suspend fun mutableRows(
        table: PushMutableTable,
        deviceId: String,
        window: PushWindow,
        limit: Int,
    ): List<PushMutableRecord> = mutable[key(table, deviceId)].orEmpty().take(limit)
}

internal class MemoryProgress : PushProgressStore {
    val devices = mutableSetOf<String>()
    val cursors = mutableMapOf<String, PushCursor>()
    val windows = mutableMapOf<String, PushWindowProgress>()

    override suspend fun knownDeviceIds(): Set<String> = devices.toSet()
    override suspend fun rememberDeviceId(deviceId: String) { devices += deviceId }

    override suspend fun cursor(table: PushAppendTable, deviceId: String): PushCursor? = cursors[key(table, deviceId)]
    override suspend fun saveCursor(table: PushAppendTable, deviceId: String, cursor: PushCursor) {
        cursors[key(table, deviceId)] = cursor
    }

    override suspend fun window(table: PushMutableTable, deviceId: String): PushWindowProgress? =
        windows[key(table, deviceId)]

    override suspend fun saveWindow(table: PushMutableTable, deviceId: String, progress: PushWindowProgress) {
        windows[key(table, deviceId)] = progress
    }
}

internal class AckingTransport(
    private val ack: (PushBatch) -> PushAck = { PushAck.fromBatch(it) },
) : PushTransport {
    val batches = mutableListOf<PushBatch>()
    val bodies = mutableListOf<ByteArray>()

    override suspend fun post(batch: PushBatch): PushTransportResponse {
        batches += batch
        bodies += batch.body.copyOf()
        return PushTransportResponse(200, ack(batch).encode())
    }
}
