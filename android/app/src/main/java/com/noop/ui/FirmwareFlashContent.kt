package com.noop.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.noop.R
import com.noop.ble.WhoopBleClient
import com.noop.protocol.EmptyFirmwareImageException
import com.noop.protocol.FirmwareFlashUiPolicy
import com.noop.protocol.FirmwareImageTooLargeException
import com.noop.protocol.FirmwareUpdateStage
import com.noop.protocol.FirmwareUpdateState
import com.noop.protocol.FirmwareVersionRelation
import com.noop.protocol.compareFirmwareVersions
import com.noop.protocol.formatFirmwareBytes
import com.noop.protocol.readFirmwareBytes
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The Test Centre firmware-update body. File selection and local validation are deliberately separate
 * from transfer, and transfer is deliberately separate from activation. A local CRC match proves that
 * the selected bytes are internally intact; it does not authenticate the publisher or prove that the
 * firmware is compatible with the connected strap.
 */
@Composable
internal fun FirmwareFlashContent(
    ble: WhoopBleClient,
    hasActiveDevice: Boolean,
    hasWhoop5MgEvidence: Boolean,
    connected: Boolean,
    encryptedBond: Boolean,
    reportedFirmware: String?,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val state by ble.firmwareUpdateState.collectAsStateWithLifecycle()
    var pickerOpen by remember { mutableStateOf(false) }
    var fileReadBusy by remember { mutableStateOf(false) }
    var fileReadError by remember { mutableStateOf<String?>(null) }
    var showActivationConfirmation by remember { mutableStateOf(false) }

    val uiBusy = pickerOpen || fileReadBusy
    val canChooseFile = FirmwareFlashUiPolicy.canChooseFile(state.stage, uiBusy)
    val canClear = FirmwareFlashUiPolicy.canClear(state.stage, uiBusy)

    LaunchedEffect(state.stage) {
        if (state.stage != FirmwareUpdateStage.READY_TO_ACTIVATE) showActivationConfirmation = false
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        pickerOpen = false
        if (uri == null) return@rememberLauncherForActivityResult

        // Fail closed before reading the replacement. A read failure or invalid replacement may never
        // leave a previously validated image armed behind a newly displayed file name.
        ble.clearFirmwareImage()
        fileReadError = null
        fileReadBusy = true
        scope.launch {
            try {
                val selection = withContext(Dispatchers.IO) { readFirmwareDocument(context, uri) }
                withContext(Dispatchers.Default) {
                    ble.selectFirmwareImage(selection.fileName, selection.bytes)
                }
            } catch (t: Throwable) {
                fileReadError = when (t) {
                    is FirmwareImageTooLargeException -> context.getString(R.string.firmware_flash_too_large)
                    is EmptyFirmwareImageException -> context.getString(R.string.firmware_flash_empty)
                    else -> t.message ?: context.getString(R.string.firmware_flash_read_failed)
                }
            } finally {
                fileReadBusy = false
            }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            text = stringResourceCompat(R.string.firmware_flash_integrity_note),
            style = NoopType.footnote,
            color = Palette.statusWarning,
        )
        Text(
            text = stringResourceCompat(R.string.firmware_flash_file_note),
            style = NoopType.footnote,
            color = Palette.textTertiary,
        )

        FirmwareDeviceReadiness(
            hasActiveDevice = hasActiveDevice,
            hasWhoop5MgEvidence = hasWhoop5MgEvidence,
            connected = connected,
            encryptedBond = encryptedBond,
            reportedFirmware = reportedFirmware,
        )

        NoopButton(
            text = if (state.image == null) {
                stringResourceCompat(R.string.firmware_flash_choose_image)
            } else {
                stringResourceCompat(R.string.firmware_flash_choose_replacement)
            },
            leadingIcon = Icons.Filled.FolderOpen,
            kind = NoopButtonKind.Secondary,
            fullWidth = true,
            enabled = canChooseFile,
            onClick = {
                pickerOpen = true
                fileReadError = null
                // Firmware downloads often arrive with application/octet-stream or no useful MIME type.
                // The parser remains the authority for extension, length, type and both CRC checks.
                picker.launch(arrayOf("*/*"))
            },
        )

        if (uiBusy) {
            Text(
                text = if (fileReadBusy) {
                    stringResourceCompat(R.string.firmware_flash_reading)
                } else {
                    stringResourceCompat(R.string.firmware_flash_picker_open)
                },
                style = NoopType.footnote,
                color = Palette.textSecondary,
            )
        }

        fileReadError?.let { error ->
            Text(error, style = NoopType.footnote, color = Palette.statusCritical)
        }

        state.image?.let { image ->
            HorizontalDivider(color = Palette.hairline)
            Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(image.fileName, style = NoopType.subhead, color = Palette.textPrimary)
                Text(
                    stringResourceCompat(R.string.firmware_flash_local_valid),
                    style = NoopType.footnote,
                    color = Palette.statusPositive,
                )
                FirmwareFact(
                    stringResourceCompat(R.string.firmware_flash_format),
                    when (image.format.name) {
                        "ZBIN_COMPRESSED" -> stringResourceCompat(R.string.firmware_flash_format_zbin)
                        "BIN_RAW" -> stringResourceCompat(R.string.firmware_flash_format_bin)
                        else -> image.format.name
                    },
                )
                FirmwareFact(
                    stringResourceCompat(R.string.firmware_flash_size),
                    formatFirmwareBytes(image.byteCount.toLong()),
                )
                FirmwareVersionPair(
                    currentVersion = reportedFirmware,
                    targetVersion = image.version,
                )
                FirmwareFact(
                    stringResourceCompat(R.string.firmware_flash_payload),
                    formatFirmwareBytes(image.payloadLength.toLong()),
                )
                FirmwareFact(stringResourceCompat(R.string.firmware_flash_payload_crc), image.payloadCrc32, mono = true)
                FirmwareFact(stringResourceCompat(R.string.firmware_flash_header_crc), image.headerCrc32, mono = true)
                FirmwareFact(stringResourceCompat(R.string.firmware_flash_sha256), image.sha256, mono = true)
                Text(
                    when (image.format.name) {
                        "ZBIN_COMPRESSED" -> stringResourceCompat(R.string.firmware_flash_compat_zbin)
                        "BIN_RAW" -> stringResourceCompat(R.string.firmware_flash_compat_bin)
                        else -> image.compatibilityNote
                    },
                    style = NoopType.footnote,
                    color = Palette.textTertiary,
                )
            }
        }

        if (state.stage != FirmwareUpdateStage.EMPTY || fileReadBusy) {
            HorizontalDivider(color = Palette.hairline)
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    stringResourceCompat(R.string.firmware_flash_status),
                    style = NoopType.subhead,
                    color = Palette.textPrimary,
                )
                Text(state.status, style = NoopType.footnote, color = Palette.textSecondary)
                state.error?.let { error ->
                    Text(error, style = NoopType.footnote, color = Palette.statusCritical)
                }
                state.lockedDeviceLabel?.let { label ->
                    FirmwareFact(stringResourceCompat(R.string.firmware_flash_session_device), label)
                }
                if (FirmwareFlashUiPolicy.showsProgress(state.stage)) {
                    LinearProgressIndicator(
                        progress = { state.progress.coerceIn(0f, 1f) },
                        modifier = Modifier.fillMaxWidth(),
                        color = Palette.accent,
                        trackColor = Palette.surfaceInset,
                    )
                    Text(
                        stringResourceCompat(
                            R.string.firmware_flash_progress,
                            formatFirmwareBytes(state.bytesAcknowledged.toLong()),
                            formatFirmwareBytes(state.totalBytes.toLong()),
                            (state.progress.coerceIn(0f, 1f) * 100f).toInt(),
                        ),
                        style = NoopType.footnote,
                        color = Palette.textTertiary,
                    )
                    if (state.stage == FirmwareUpdateStage.REMOTE_VALIDATING) {
                        Text(
                            stringResourceCompat(R.string.firmware_flash_remote_check_in_progress),
                            style = NoopType.footnote,
                            color = Palette.textSecondary,
                        )
                    }
                }
            }
        }

        NoopButton(
            text = stringResourceCompat(R.string.firmware_flash_transfer),
            leadingIcon = Icons.Filled.PlayArrow,
            kind = NoopButtonKind.Primary,
            fullWidth = true,
            enabled = state.canStart && !uiBusy,
            onClick = { ble.startFirmwareTransfer() },
        )

        if (state.stage == FirmwareUpdateStage.PAUSED) {
            Text(
                stringResourceCompat(R.string.firmware_flash_resume_note),
                style = NoopType.footnote,
                color = Palette.statusWarning,
            )
            NoopButton(
                text = stringResourceCompat(R.string.firmware_flash_resume),
                leadingIcon = Icons.Filled.PlayArrow,
                kind = NoopButtonKind.Primary,
                fullWidth = true,
                enabled = state.canResume && !uiBusy,
                onClick = { ble.resumeFirmwareTransfer() },
            )
        }

        if (state.stage == FirmwareUpdateStage.READY_TO_ACTIVATE) {
            Text(
                stringResourceCompat(R.string.firmware_flash_remote_valid),
                style = NoopType.footnote,
                color = Palette.statusPositive,
            )
            NoopButton(
                text = stringResourceCompat(R.string.firmware_flash_activate),
                kind = NoopButtonKind.Destructive,
                fullWidth = true,
                enabled = state.canActivate && !uiBusy,
                onClick = { showActivationConfirmation = true },
            )
        }

        if (state.canCancel) {
            NoopButton(
                text = stringResourceCompat(R.string.firmware_flash_cancel),
                leadingIcon = Icons.Filled.Stop,
                kind = NoopButtonKind.Secondary,
                fullWidth = true,
                enabled = !uiBusy,
                onClick = { ble.cancelFirmwareUpdate() },
            )
            Text(
                stringResourceCompat(R.string.firmware_flash_cancel_note),
                style = NoopType.footnote,
                color = Palette.textTertiary,
            )
        }

        if (canClear) {
            NoopButton(
                text = stringResourceCompat(R.string.firmware_flash_clear),
                kind = NoopButtonKind.Tertiary,
                fullWidth = true,
                onClick = {
                    fileReadError = null
                    ble.clearFirmwareImage()
                },
            )
        }

    }

    if (showActivationConfirmation) {
        FirmwareActivationDialog(
            state = state,
            currentVersion = reportedFirmware,
            onDismiss = { showActivationConfirmation = false },
            onActivate = {
                showActivationConfirmation = false
                ble.activateVerifiedFirmware()
            },
        )
    }
}

@Composable
private fun FirmwareDeviceReadiness(
    hasActiveDevice: Boolean,
    hasWhoop5MgEvidence: Boolean,
    connected: Boolean,
    encryptedBond: Boolean,
    reportedFirmware: String?,
) {
    val (text, color) = when {
        !hasActiveDevice -> stringResourceCompat(R.string.firmware_flash_no_active_device) to Palette.statusWarning
        !hasWhoop5MgEvidence -> stringResourceCompat(R.string.firmware_flash_whoop5_only) to Palette.statusWarning
        !connected -> stringResourceCompat(R.string.firmware_flash_device_disconnected) to Palette.statusWarning
        !encryptedBond -> stringResourceCompat(R.string.firmware_flash_device_not_paired) to Palette.statusWarning
        else -> stringResourceCompat(
            R.string.firmware_flash_device_ready,
            reportedFirmware ?: stringResourceCompat(R.string.firmware_flash_unknown_version),
        ) to Palette.statusPositive
    }
    Text(text, style = NoopType.footnote, color = color)
}

@Composable
private fun FirmwareActivationDialog(
    state: FirmwareUpdateState,
    currentVersion: String?,
    onDismiss: () -> Unit,
    onActivate: () -> Unit,
) {
    val image = state.image
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Palette.surfaceOverlay,
        title = {
            Text(
                stringResourceCompat(R.string.firmware_flash_activate_title),
                style = NoopType.title2,
                color = Palette.textPrimary,
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    stringResourceCompat(
                        R.string.firmware_flash_activate_identity,
                        image?.fileName ?: "?",
                        currentVersion ?: stringResourceCompat(R.string.firmware_flash_unknown_version),
                        image?.version ?: "?",
                        state.lockedDeviceLabel ?: stringResourceCompat(R.string.firmware_flash_unknown_device),
                    ),
                    style = NoopType.subhead,
                    color = Palette.textPrimary,
                )
                FirmwareVersionRelationNote(
                    compareFirmwareVersions(currentVersion, image?.version ?: ""),
                )
                Text(
                    stringResourceCompat(R.string.firmware_flash_activate_warning),
                    style = NoopType.footnote,
                    color = Palette.statusWarning,
                )
            }
        },
        confirmButton = {
            TextButton(enabled = state.canActivate, onClick = onActivate) {
                Text(
                    stringResourceCompat(R.string.firmware_flash_activate_confirm),
                    style = NoopType.body,
                    color = Palette.statusCritical,
                )
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(
                    stringResourceCompat(R.string.firmware_flash_keep_staged),
                    style = NoopType.body,
                    color = Palette.textSecondary,
                )
            }
        },
    )
}

@Composable
private fun FirmwareVersionPair(currentVersion: String?, targetVersion: String) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                stringResourceCompat(R.string.firmware_flash_current_version),
                style = NoopType.footnote,
                color = Palette.textTertiary,
            )
            Text(
                currentVersion ?: stringResourceCompat(R.string.firmware_flash_unknown_version),
                style = NoopType.subhead.copy(fontFamily = FontFamily.Monospace),
                color = Palette.textSecondary,
            )
        }
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                stringResourceCompat(R.string.firmware_flash_target_version),
                style = NoopType.footnote,
                color = Palette.textTertiary,
            )
            Text(
                targetVersion,
                style = NoopType.subhead.copy(fontFamily = FontFamily.Monospace),
                color = Palette.textSecondary,
            )
        }
    }
    FirmwareVersionRelationNote(compareFirmwareVersions(currentVersion, targetVersion))
}

@Composable
private fun FirmwareVersionRelationNote(relation: FirmwareVersionRelation) {
    val (message, color) = when (relation) {
        FirmwareVersionRelation.DOWNGRADE ->
            stringResourceCompat(R.string.firmware_flash_version_downgrade) to Palette.statusWarning
        FirmwareVersionRelation.SAME ->
            stringResourceCompat(R.string.firmware_flash_version_same) to Palette.textSecondary
        FirmwareVersionRelation.UPGRADE ->
            stringResourceCompat(R.string.firmware_flash_version_upgrade) to Palette.statusPositive
        FirmwareVersionRelation.INCOMPARABLE ->
            stringResourceCompat(R.string.firmware_flash_version_incomparable) to Palette.statusWarning
    }
    Text(message, style = NoopType.footnote, color = color)
}

@Composable
private fun FirmwareFact(label: String, value: String, mono: Boolean = false) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(label, style = NoopType.footnote, color = Palette.textTertiary, modifier = Modifier.weight(0.38f))
        Text(
            value,
            style = if (mono) NoopType.footnote.copy(fontFamily = FontFamily.Monospace) else NoopType.footnote,
            color = Palette.textSecondary,
            modifier = Modifier.weight(0.62f),
        )
    }
}

internal data class FirmwareDocument(val fileName: String, val bytes: ByteArray)

private fun readFirmwareDocument(context: Context, uri: Uri): FirmwareDocument {
    var displayName: String? = null
    var declaredSize: Long? = null
    context.contentResolver.query(
        uri,
        arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
        null,
        null,
        null,
    )?.use { cursor ->
        if (cursor.moveToFirst()) {
            val nameColumn = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            val sizeColumn = cursor.getColumnIndex(OpenableColumns.SIZE)
            if (nameColumn >= 0 && !cursor.isNull(nameColumn)) displayName = cursor.getString(nameColumn)
            if (sizeColumn >= 0 && !cursor.isNull(sizeColumn)) declaredSize = cursor.getLong(sizeColumn)
        }
    }
    val name = displayName?.takeIf { it.isNotBlank() }
        ?: uri.lastPathSegment?.substringAfterLast('/')?.takeIf { it.isNotBlank() }
        ?: throw IllegalArgumentException(context.getString(R.string.firmware_flash_name_missing))
    val stream = context.contentResolver.openInputStream(uri)
        ?: throw IllegalArgumentException(context.getString(R.string.firmware_flash_open_failed))
    val bytes = stream.use { readFirmwareBytes(it, declaredSize) }
    return FirmwareDocument(name, bytes)
}

@Composable
private fun stringResourceCompat(id: Int, vararg formatArgs: Any): String =
    androidx.compose.ui.res.stringResource(id, *formatArgs)
