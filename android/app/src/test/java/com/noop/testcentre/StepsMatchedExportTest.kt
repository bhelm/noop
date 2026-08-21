package com.noop.testcentre

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StepsMatchedExportTest {
    @Test fun actionIsOnlyOfferedForTheStepsCapture() {
        assertTrue(StepsMatchedExport.visibleFor(TestDomain.STEPS))
        assertFalse(StepsMatchedExport.visibleFor(TestDomain.SLEEP))
        assertFalse(StepsMatchedExport.visibleFor(TestDomain.MASTER))
    }

    @Test fun copyPromisesOneSessionBoundBundleInsteadOfUnrelatedRotatedFiles() {
        assertTrue(StepsMatchedExport.BUTTON_LABEL.contains("session", ignoreCase = true))
        assertTrue(StepsMatchedExport.BUTTON_LABEL.contains("report", ignoreCase = true))
        assertTrue(StepsMatchedExport.EXPLANATION.contains("activation", ignoreCase = true))
        assertTrue(StepsMatchedExport.EXPLANATION.contains("incomplete", ignoreCase = true))
        assertTrue(StepsMatchedExport.EXPLANATION.contains("ZIP", ignoreCase = true))
        assertFalse(StepsMatchedExport.EXPLANATION.contains("same capture window", ignoreCase = true))
    }

    @Test fun activationMarkerCarriesTheBoundDevice() {
        val marker = StepsMatchedExport.activationMarker(
            TestCentre.CaptureSession("steps-123", 123, "strap-a"),
        )
        assertTrue(marker.contains("session=steps-123"))
        assertTrue(marker.contains("startTs=123"))
        assertTrue(marker.contains("device=strap-a"))
    }
}
