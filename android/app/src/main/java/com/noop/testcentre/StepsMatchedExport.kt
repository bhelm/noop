package com.noop.testcentre

/** Pure presentation contract for the one-tap control-test export in the Steps row. */
object StepsMatchedExport {
    const val BUTTON_LABEL = "Export this Steps session + report"
    const val EXPLANATION =
        "Creates one ZIP with up to 24 hours of raw sensor rows banked since Steps test activation and " +
            "the matching report for that same strap. The ZIP is marked incomplete if coverage is missing " +
            "or the active strap changed."

    fun activationMarker(session: TestCentre.CaptureSession): String =
        "stepsControl session=${session.id} startTs=${session.startedAt} device=${session.deviceId ?: "UNKNOWN"}"

    fun visibleFor(domain: TestDomain): Boolean = domain == TestDomain.STEPS
}
