package com.noop.protocol

import java.io.ByteArrayOutputStream
import java.io.InputStream

enum class FirmwareUpdateStage {
    EMPTY,
    IMAGE_READY,
    PREPARING,
    WRITING,
    REMOTE_VALIDATING,
    READY_TO_ACTIVATE,
    ACTIVATION_REQUESTED,
    RECONNECTING,
    DEVICE_RECONNECTED,
    PAUSED,
    FAILED,
    CANCELLED,
}

data class FirmwareUpdateState(
    val stage: FirmwareUpdateStage = FirmwareUpdateStage.EMPTY,
    val image: FirmwareImageInfo? = null,
    val bytesAcknowledged: Int = 0,
    val totalBytes: Int = image?.byteCount ?: 0,
    val status: String = "Choose an original .zbin update image",
    val error: String? = null,
    val deviceEligible: Boolean = false,
    val lockedDeviceLabel: String? = null,
) {
    val progress: Float
        get() = if (totalBytes <= 0) 0f else (bytesAcknowledged.toFloat() / totalBytes).coerceIn(0f, 1f)
    val canStart: Boolean get() = stage == FirmwareUpdateStage.IMAGE_READY && deviceEligible
    val canActivate: Boolean get() = stage == FirmwareUpdateStage.READY_TO_ACTIVATE
    val canResume: Boolean get() = stage == FirmwareUpdateStage.PAUSED && deviceEligible
    val canCancel: Boolean get() = stage in setOf(
        FirmwareUpdateStage.PREPARING,
        FirmwareUpdateStage.WRITING,
        FirmwareUpdateStage.REMOTE_VALIDATING,
        FirmwareUpdateStage.READY_TO_ACTIVATE,
        FirmwareUpdateStage.PAUSED,
    )
}

/** Pure state transitions shared by the BLE integration and unit tests. */
internal object FirmwareUpdateTransitions {
    fun selected(image: FirmwareImageInfo, eligible: Boolean): FirmwareUpdateState = FirmwareUpdateState(
        stage = FirmwareUpdateStage.IMAGE_READY,
        image = image,
        totalBytes = image.byteCount,
        status = if (eligible) "Image validated locally. Ready to transfer." else
            "Image validated. Connect and bond a WHOOP 5/MG strap to continue.",
        deviceEligible = eligible,
    )

    fun begin(state: FirmwareUpdateState, deviceLabel: String): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.PREPARING,
        bytesAcknowledged = 0,
        status = "Preparing the strap's update slot",
        error = null,
        lockedDeviceLabel = deviceLabel,
        deviceEligible = true,
    )

    fun writing(state: FirmwareUpdateState, acknowledged: Int): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.WRITING,
        bytesAcknowledged = acknowledged.coerceIn(0, state.totalBytes),
        status = "Writing firmware: ${acknowledged.coerceIn(0, state.totalBytes)} / ${state.totalBytes} bytes acknowledged",
    )

    fun retrying(
        state: FirmwareUpdateState,
        acknowledged: Int,
        attempt: Int,
        maximum: Int,
    ): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.WRITING,
        bytesAcknowledged = acknowledged.coerceIn(0, state.totalBytes),
        status = "Retrying firmware chunk at offset $acknowledged ($attempt / $maximum)",
    )

    fun paused(state: FirmwareUpdateState, acknowledged: Int, reason: String): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.PAUSED,
        bytesAcknowledged = acknowledged.coerceIn(0, state.totalBytes),
        status = "Transfer paused at the last acknowledged offset. Resume is available only on this connection.",
        error = reason,
        deviceEligible = true,
    )

    fun resuming(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.PREPARING,
        status = "Preparing the same strap connection to resume at offset ${state.bytesAcknowledged}",
        error = null,
    )

    fun remoteValidating(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.REMOTE_VALIDATING,
        bytesAcknowledged = state.totalBytes,
        status = "Transfer complete. Waiting for the strap's asynchronous integrity result.",
    )

    fun ready(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.READY_TO_ACTIVATE,
        status = "The strap verified the image. Activation needs your confirmation.",
    )

    fun activationRequested(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.ACTIVATION_REQUESTED,
        status = "Activation accepted. The strap is restarting.",
    )

    fun reconnecting(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.RECONNECTING,
        status = "The strap is restarting. Waiting for it to reconnect.",
    )

    fun reconnected(state: FirmwareUpdateState, reportedVersion: String?): FirmwareUpdateState {
        val suffix = reportedVersion?.let { " and reports firmware $it" } ?: ""
        return state.copy(
            stage = FirmwareUpdateStage.DEVICE_RECONNECTED,
            status = "The strap reconnected$suffix.",
        )
    }

    fun failed(state: FirmwareUpdateState, reason: String): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.FAILED,
        status = "Firmware update stopped",
        error = reason,
        deviceEligible = false,
    )

    fun cancelled(state: FirmwareUpdateState): FirmwareUpdateState = state.copy(
        stage = FirmwareUpdateStage.CANCELLED,
        status = "Update session cancelled. The app will not resume or activate it automatically.",
        error = null,
        deviceEligible = false,
    )

    fun eligibility(state: FirmwareUpdateState, eligible: Boolean): FirmwareUpdateState {
        if (state.stage != FirmwareUpdateStage.IMAGE_READY) return state
        return state.copy(
            deviceEligible = eligible,
            status = if (eligible) "Image validated locally. Ready to transfer." else
                "Image validated. Connect and bond a WHOOP 5/MG strap to continue.",
        )
    }
}

private const val FIRMWARE_READ_BUFFER_BYTES = 64 * 1024
private const val FIRMWARE_MAX_VERSION_COMPONENT = 4_294_967_295L // UInt.MAX_VALUE

internal enum class FirmwareVersionRelation {
    DOWNGRADE,
    SAME,
    UPGRADE,
    INCOMPARABLE,
}

/** Compares canonical four-component firmware versions numerically, never lexicographically. */
internal fun compareFirmwareVersions(currentVersion: String?, targetVersion: String): FirmwareVersionRelation {
    val current = currentVersion?.let(::parseFirmwareVersion)
        ?: return FirmwareVersionRelation.INCOMPARABLE
    val target = parseFirmwareVersion(targetVersion)
        ?: return FirmwareVersionRelation.INCOMPARABLE
    current.indices.forEach { index ->
        if (target[index] < current[index]) return FirmwareVersionRelation.DOWNGRADE
        if (target[index] > current[index]) return FirmwareVersionRelation.UPGRADE
    }
    return FirmwareVersionRelation.SAME
}

private fun parseFirmwareVersion(version: String): List<Long>? {
    val components = version.trim().split('.')
    if (components.size != 4) return null
    return components.map { component ->
        if (component.isEmpty() || component.any { it !in '0'..'9' }) return null
        component.toLongOrNull()?.takeIf { it in 0..FIRMWARE_MAX_VERSION_COMPONENT } ?: return null
    }
}

internal object FirmwareFlashUiPolicy {
    private val settledStages = setOf(
        FirmwareUpdateStage.EMPTY,
        FirmwareUpdateStage.IMAGE_READY,
        FirmwareUpdateStage.FAILED,
        FirmwareUpdateStage.CANCELLED,
        FirmwareUpdateStage.DEVICE_RECONNECTED,
    )

    fun canChooseFile(stage: FirmwareUpdateStage, uiBusy: Boolean): Boolean = !uiBusy && stage in settledStages

    fun canClear(stage: FirmwareUpdateStage, uiBusy: Boolean): Boolean =
        !uiBusy && stage in settledStages && stage != FirmwareUpdateStage.EMPTY

    fun showsProgress(stage: FirmwareUpdateStage): Boolean = stage in setOf(
        FirmwareUpdateStage.PREPARING,
        FirmwareUpdateStage.WRITING,
        FirmwareUpdateStage.REMOTE_VALIDATING,
        FirmwareUpdateStage.READY_TO_ACTIVATE,
        FirmwareUpdateStage.PAUSED,
    )

}

internal fun readFirmwareBytes(
    input: InputStream,
    declaredSize: Long? = null,
    maxBytes: Int = FirmwareImageParser.MAX_IMAGE_BYTES,
): ByteArray {
    require(maxBytes > 0)
    if (declaredSize != null && declaredSize > maxBytes) {
        throw FirmwareImageTooLargeException()
    }
    val initialCapacity = declaredSize
        ?.coerceAtLeast(0L)
        ?.coerceAtMost(maxBytes.toLong())
        ?.toInt()
        ?: FIRMWARE_READ_BUFFER_BYTES
    val output = ByteArrayOutputStream(initialCapacity)
    val buffer = ByteArray(FIRMWARE_READ_BUFFER_BYTES)
    var total = 0
    while (true) {
        val count = input.read(buffer)
        if (count < 0) break
        if (count == 0) continue
        total += count
        if (total > maxBytes) {
            throw FirmwareImageTooLargeException()
        }
        output.write(buffer, 0, count)
    }
    if (total == 0) throw EmptyFirmwareImageException()
    return output.toByteArray()
}

internal class FirmwareImageTooLargeException : IllegalArgumentException()
internal class EmptyFirmwareImageException : IllegalArgumentException()

internal fun formatFirmwareBytes(bytes: Long): String = when {
    bytes >= 1024L * 1024L -> "%.2f MiB".format(java.util.Locale.ROOT, bytes / (1024.0 * 1024.0))
    bytes >= 1024L -> "%.1f KiB".format(java.util.Locale.ROOT, bytes / 1024.0)
    else -> "$bytes B"
}
