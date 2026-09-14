package com.noop.ui

import com.noop.ble.FirmwareImageParser
import com.noop.ble.FirmwareUpdateStage
import java.io.ByteArrayInputStream
import java.io.InputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class FirmwareFlashUiPolicyTest {
    @Test
    fun versionComparisonCoversUpgradeEqualAndDowngrade() {
        assertEquals(
            FirmwareVersionRelation.UPGRADE,
            compareFirmwareVersions("50.9.1.2", "50.10.1.2"),
        )
        assertEquals(
            FirmwareVersionRelation.SAME,
            compareFirmwareVersions("50.10.1.2", "50.10.1.2"),
        )
        assertEquals(
            FirmwareVersionRelation.DOWNGRADE,
            compareFirmwareVersions("50.10.1.2", "50.9.9.9"),
        )
    }

    @Test
    fun versionComparisonIsNumericAndRespondsToChangedLiveVersion() {
        val target = "50.10.1.2"
        assertEquals(FirmwareVersionRelation.UPGRADE, compareFirmwareVersions("50.9.99.99", target))
        assertEquals(FirmwareVersionRelation.SAME, compareFirmwareVersions("50.10.1.2", target))
        assertEquals(FirmwareVersionRelation.DOWNGRADE, compareFirmwareVersions("50.11.0.0", target))
    }

    @Test
    fun unknownMalformedAndOutOfRangeVersionsRemainIncomparable() {
        val target = "50.10.1.2"
        listOf(null, "", "50.10.1", "50.10.x.2", "50.-1.1.2", "4294967296.1.2.3").forEach { current ->
            assertEquals(
                "current=$current",
                FirmwareVersionRelation.INCOMPARABLE,
                compareFirmwareVersions(current, target),
            )
        }
        assertEquals(
            FirmwareVersionRelation.INCOMPARABLE,
            compareFirmwareVersions("50.10.1.2", "50.10.1.2.0"),
        )
    }

    @Test
    fun fileReplacementAndClearAreLockedAcrossEveryActiveDeviceStage() {
        val activeStages = listOf(
            FirmwareUpdateStage.PREPARING,
            FirmwareUpdateStage.WRITING,
            FirmwareUpdateStage.REMOTE_VALIDATING,
            FirmwareUpdateStage.READY_TO_ACTIVATE,
            FirmwareUpdateStage.ACTIVATION_REQUESTED,
            FirmwareUpdateStage.RECONNECTING,
            FirmwareUpdateStage.PAUSED,
        )

        activeStages.forEach { stage ->
            assertFalse("choose during $stage", FirmwareFlashUiPolicy.canChooseFile(stage, uiBusy = false))
            assertFalse("clear during $stage", FirmwareFlashUiPolicy.canClear(stage, uiBusy = false))
        }
    }

    @Test
    fun settledAndTerminalStagesUnlockSafeLocalActions() {
        listOf(
            FirmwareUpdateStage.IMAGE_READY,
            FirmwareUpdateStage.FAILED,
            FirmwareUpdateStage.CANCELLED,
            FirmwareUpdateStage.DEVICE_RECONNECTED,
        ).forEach { stage ->
            assertTrue("choose after $stage", FirmwareFlashUiPolicy.canChooseFile(stage, uiBusy = false))
            assertTrue("clear after $stage", FirmwareFlashUiPolicy.canClear(stage, uiBusy = false))
        }
        assertTrue(FirmwareFlashUiPolicy.canChooseFile(FirmwareUpdateStage.EMPTY, uiBusy = false))
        assertFalse(FirmwareFlashUiPolicy.canClear(FirmwareUpdateStage.EMPTY, uiBusy = false))
        assertFalse(FirmwareFlashUiPolicy.canChooseFile(FirmwareUpdateStage.EMPTY, uiBusy = true))
        assertFalse(FirmwareFlashUiPolicy.canClear(FirmwareUpdateStage.IMAGE_READY, uiBusy = true))
    }

    @Test
    fun progressIsShownOnlyThroughRemoteValidationAndTheActivationGate() {
        val progressStages = setOf(
            FirmwareUpdateStage.PREPARING,
            FirmwareUpdateStage.WRITING,
            FirmwareUpdateStage.REMOTE_VALIDATING,
            FirmwareUpdateStage.READY_TO_ACTIVATE,
            FirmwareUpdateStage.PAUSED,
        )

        FirmwareUpdateStage.entries.forEach { stage ->
            assertEquals(stage in progressStages, FirmwareFlashUiPolicy.showsProgress(stage))
        }
    }

    @Test
    fun readerRejectsEmptyAndDeclaredOversizeBeforeReading() {
        assertThrows(EmptyFirmwareImageException::class.java) {
            readFirmwareBytes(ByteArrayInputStream(byteArrayOf()))
        }

        val neverRead = object : InputStream() {
            override fun read(): Int = error("must not read an oversized document")
        }
        assertThrows(FirmwareImageTooLargeException::class.java) {
            readFirmwareBytes(
                neverRead,
                declaredSize = FirmwareImageParser.MAX_IMAGE_BYTES.toLong() + 1L,
            )
        }
    }

    @Test
    fun readerBoundsStreamsWhoseProviderDoesNotDeclareSize() {
        assertThrows(FirmwareImageTooLargeException::class.java) {
            readFirmwareBytes(ByteArrayInputStream(byteArrayOf(1, 2, 3, 4, 5)), maxBytes = 4)
        }
    }

    @Test
    fun readerReturnsExactBytesAndByteLabelsStayDeterministic() {
        val expected = byteArrayOf(0, 1, 2, 3, 0x7f)
        assertTrue(expected.contentEquals(readFirmwareBytes(ByteArrayInputStream(expected))))
        assertEquals("5 B", formatFirmwareBytes(5))
        assertEquals("1.5 KiB", formatFirmwareBytes(1536))
        assertEquals("2.00 MiB", formatFirmwareBytes(2L * 1024L * 1024L))
    }
}
