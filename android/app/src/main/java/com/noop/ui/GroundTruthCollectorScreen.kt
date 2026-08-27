package com.noop.ui

import android.os.Build
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import android.view.HapticFeedbackConstants
import android.view.InputDevice
import android.view.KeyEvent
import android.widget.Toast
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.noop.R
import com.noop.testcentre.GroundTruthCollector
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Bounded 5/MG raw-data capture. Manual clicker labels are an optional fork extension. */
@Composable
fun GroundTruthCollectorScreen(vm: AppViewModel) {
    val context = LocalContext.current
    val collector = remember { GroundTruthCollector.from(context) }
    val focusRequester = remember { FocusRequester() }
    val view = LocalView.current
    val scope = rememberCoroutineScope()
    val days by vm.recentDays.collectAsStateWithLifecycle()
    val live by vm.live.collectAsStateWithLifecycle()
    val imuStatus by vm.ble.groundTruthImuStatus.collectAsStateWithLifecycle()
    // Optional fork overlay only. The raw collector itself must not depend on the still-separate
    // physiological day-cycle work; use the latest published total when the step counter is present.
    val noopSteps = days.lastOrNull()?.steps
    var state by remember { mutableStateOf(collector.snapshot()) }
    var haptics by remember { mutableStateOf(false) }
    var exportingSessionId by remember { mutableStateOf<String?>(null) }
    var sessions by remember { mutableStateOf(collector.sessions()) }
    var latestSensorTs by remember { mutableStateOf<Long?>(null) }
    var nowMs by remember { mutableStateOf(System.currentTimeMillis()) }
    var captureStats by remember { mutableStateOf<Map<String, GroundTruthCollector.CaptureStats>>(emptyMap()) }
    var markerEditor by remember { mutableStateOf<MarkerEditor?>(null) }
    var sessionPendingDelete by remember { mutableStateOf<GroundTruthCollector.SessionSummary?>(null) }
    var confirmDeleteAll by remember { mutableStateOf(false) }

    LaunchedEffect(vm.activeStrapId) {
        while (true) {
            nowMs = System.currentTimeMillis()
            latestSensorTs = vm.activeStrapId.takeIf(String::isNotBlank)?.let { vm.repo.latestHrSampleTs(it) }
            captureStats = sessions.associate { it.id to collector.captureStats(it, nowMs) }
            delay(1_000)
        }
    }

    DisposableEffect(view) {
        val oldKeepScreenOn = view.keepScreenOn
        view.keepScreenOn = true
        onDispose {
            view.keepScreenOn = oldKeepScreenOn
        }
    }
    LaunchedEffect(state.active, state.steps, state.stairs) { focusRequester.requestFocus() }
    // Re-arm after a BLE reconnect or Android process restart while the manual session is still active.
    LaunchedEffect(state.active, state.sessionId, live.connected) {
        if (state.active && live.connected) state.sessionId?.let(vm.ble::startGroundTruthImuCapture)
    }

    ScreenScaffold(
        title = stringResource(R.string.ground_truth_title),
        subtitle = stringResource(R.string.ground_truth_subtitle),
        modifier = Modifier
            .focusRequester(focusRequester)
            .focusable()
            .onPreviewKeyEvent { composeEvent ->
                val event = composeEvent.nativeKeyEvent
                if (event.action != KeyEvent.ACTION_DOWN || event.repeatCount != 0) return@onPreviewKeyEvent false
                val key = event.toCollectorKey()
                state = when (event.keyCode) {
                    KeyEvent.KEYCODE_ZOOM_OUT -> collector.record(GroundTruthCollector.KIND_STEP, key, noopSteps)
                    KeyEvent.KEYCODE_ZOOM_IN -> collector.record(GroundTruthCollector.KIND_STAIR, key, noopSteps)
                    else -> collector.observeKey(key, noopSteps)
                }
                val mapped = event.keyCode == KeyEvent.KEYCODE_ZOOM_OUT || event.keyCode == KeyEvent.KEYCODE_ZOOM_IN
                if (mapped && haptics) {
                    view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 30) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.KEYBOARD_TAP)
                }
                mapped
            },
    ) {
        NoopCard(tint = Palette.accent) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth()) {
                    CounterColumn(stringResource(R.string.ground_truth_noop_steps), noopSteps?.toString() ?: "-", Modifier.weight(1f))
                    CounterColumn(stringResource(R.string.ground_truth_manual_steps), state.steps.toString(), Modifier.weight(1f))
                    CounterColumn(stringResource(R.string.ground_truth_stairs), state.stairs.toString(), Modifier.weight(1f))
                }
                val noopSessionSteps = state.noopStepsAtStart?.let { start -> noopSteps?.minus(start) }
                val delta = noopSessionSteps?.minus(state.steps)
                Text(
                    text = stringResource(R.string.ground_truth_delta, delta?.toString() ?: "-"),
                    style = NoopType.subhead,
                    color = Palette.textSecondary,
                )
            }
        }

        NoopCard {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val activeSession = sessions.firstOrNull { it.active }
                val activeStats = activeSession?.let { captureStats[it.id] }
                Text(if (activeSession == null) "Capture status" else "Recording · ${formatDuration(nowMs - activeSession.startedAtMs)}",
                    style = NoopType.headline, color = Palette.textPrimary)
                if (activeStats != null) Text(
                    "IMU ${formatBytes(activeStats.bytes)} · ${coverageText(activeStats)}",
                    style = NoopType.body,
                    color = if (activeStats.coveredSeconds > 0) Palette.statusPositive else Palette.textSecondary,
                )
                Text(
                    if (live.connected) "Band: connected${if (live.bonded) " + paired" else "; pairing"}"
                    else "Band: disconnected${if (live.scanning) "; searching" else ""}",
                    style = NoopType.body,
                    color = if (live.connected) Palette.statusPositive else Palette.statusCritical,
                )
                Text(
                    when {
                        live.backfilling -> "History sync: running (${live.syncChunksThisSession} chunks)"
                        live.lastSyncAt != null -> "History sync completed: ${diagnosticTime(live.lastSyncAt!! * 1000)}"
                        else -> "History sync: no completed sync recorded"
                    },
                    style = NoopType.body,
                    color = Palette.textSecondary,
                )
                Text(
                    latestSensorTs?.let { "Decoded 1 Hz sensors: ${diagnosticAge(it * 1000)} behind" }
                        ?: "Decoded 1 Hz sensors: none",
                    style = NoopType.caption,
                    color = Palette.textSecondary,
                )
                Text(
                    "Realtime IMU: ${imuStatus.note}; ${imuStatus.packets} packets / ${imuStatus.bytes} bytes" +
                        (imuStatus.lastPacketAtMs?.let { "; last ${diagnosticAge(it)} ago" } ?: ""),
                    style = NoopType.body,
                    color = if (imuStatus.packets > 0) Palette.statusPositive else Palette.textSecondary,
                )
            }
        }

        NoopCard {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.ground_truth_mapping_title), style = NoopType.headline, color = Palette.textPrimary)
                Text(stringResource(R.string.ground_truth_mapping_body), style = NoopType.body, color = Palette.textSecondary)
                val key = state.lastKey
                Text(
                    text = if (key == null) stringResource(R.string.ground_truth_no_key)
                    else stringResource(
                        R.string.ground_truth_key_detail,
                        key.keyName, key.keyCode, key.scanCode, key.deviceName, key.deviceId, key.source,
                    ),
                    style = NoopType.caption,
                    color = Palette.textSecondary,
                )
            }
        }

        NoopCard {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.ground_truth_haptics), style = NoopType.body, color = Palette.textPrimary)
                    Text(stringResource(R.string.ground_truth_haptics_desc), style = NoopType.caption, color = Palette.textSecondary)
                }
                Switch(checked = haptics, onCheckedChange = { haptics = it })
            }
        }

        if (state.active) {
            NoopButton(
                text = "Add marker",
                kind = NoopButtonKind.Secondary,
                fullWidth = true,
                onClick = { state.sessionId?.let { markerEditor = MarkerEditor(it, null, nowMs, "Moment", "") } },
            )
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                NoopButton(
                    text = stringResource(R.string.ground_truth_undo),
                    kind = NoopButtonKind.Secondary,
                    modifier = Modifier.weight(1f),
                    onClick = { state = collector.undo(noopSteps) },
                )
                NoopButton(
                    text = stringResource(R.string.ground_truth_stop),
                    kind = NoopButtonKind.Destructive,
                    modifier = Modifier.weight(1f),
                    onClick = {
                        vm.ble.stopGroundTruthImuCapture()
                        state = collector.stop(noopSteps)
                        sessions = collector.sessions()
                    },
                )
            }
        } else {
            NoopButton(
                text = stringResource(R.string.ground_truth_start),
                fullWidth = true,
                enabled = vm.activeStrapId.isNotBlank(),
                onClick = {
                    state = collector.start(noopSteps, vm.activeStrapId)
                    sessions = collector.sessions()
                },
            )
        }
        NoopButton(
            text = stringResource(R.string.ground_truth_add_historical),
            kind = NoopButtonKind.Secondary,
            fullWidth = true,
            enabled = !state.active && vm.activeStrapId.isNotBlank(),
            onClick = {
                val end = System.currentTimeMillis()
                collector.createHistoricalSession(vm.activeStrapId, end - 60 * 60 * 1_000L, end)
                sessions = collector.sessions()
            },
        )
        Text(stringResource(R.string.ground_truth_sessions), style = NoopType.title2, color = Palette.textPrimary)
        if (sessions.isEmpty()) {
            Text(stringResource(R.string.ground_truth_no_sessions), style = NoopType.body, color = Palette.textSecondary)
        } else {
            NoopButton(
                text = stringResource(R.string.ground_truth_delete_all),
                kind = NoopButtonKind.Destructive,
                fullWidth = true,
                enabled = sessions.none { it.active },
                onClick = { confirmDeleteAll = true },
            )
            sessions.forEach { session ->
                SessionCard(
                    session = session,
                    latestSensorTs = latestSensorTs,
                    stats = captureStats[session.id],
                    exporting = exportingSessionId == session.id,
                    onComment = { comment ->
                        collector.setComment(session.id, comment)
                        sessions = sessions.map { if (it.id == session.id) it.copy(comment = comment) else it }
                    },
                    onExport = {
                        exportingSessionId = session.id
                        scope.launch {
                            try {
                                collector.share(collector.export(vm.repo, session.id))
                                vm.ble.finishGroundTruthImuCapture(session.id)
                                sessions = collector.sessions()
                            } catch (failure: Throwable) {
                                if (failure is kotlinx.coroutines.CancellationException) throw failure
                                Toast.makeText(context, context.getString(R.string.ground_truth_export_failed,
                                    "${failure.javaClass.simpleName}: ${failure.message ?: "unknown error"}"), Toast.LENGTH_LONG).show()
                            } finally {
                                exportingSessionId = null
                            }
                        }
                    },
                    onDelete = { sessionPendingDelete = session },
                    onAddMarker = { markerEditor = MarkerEditor(session.id, null, session.endedAtMs ?: nowMs, "Moment", "") },
                    onEditMarker = { marker -> markerEditor = MarkerEditor(session.id, marker.id, marker.atMs, marker.type, marker.text) },
                    onEditStart = {
                        pickDateTime(context, session.startedAtMs, EARLIEST_EXPORT_MS,
                            session.endedAtMs ?: session.capturedEndedAtMs ?: session.startedAtMs) { value ->
                            collector.setSessionRange(session.id, value, requireNotNull(session.endedAtMs))
                            sessions = collector.sessions()
                        }
                    },
                    onEditEnd = {
                        pickDateTime(context, requireNotNull(session.endedAtMs), session.startedAtMs,
                            System.currentTimeMillis()) { value ->
                            collector.setSessionRange(session.id, session.startedAtMs, value)
                            sessions = collector.sessions()
                        }
                    },
                )
            }
        }
    }

    sessionPendingDelete?.let { session ->
        AlertDialog(
            onDismissRequest = { sessionPendingDelete = null },
            title = { Text(stringResource(R.string.ground_truth_delete_session_title)) },
            text = { Text(stringResource(R.string.ground_truth_delete_session_message, sessionTimeRange(session))) },
            dismissButton = {
                TextButton(onClick = { sessionPendingDelete = null }) {
                    Text(stringResource(R.string.ground_truth_cancel))
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    vm.ble.finishGroundTruthImuCapture(session.id)
                    collector.deleteSession(session.id)
                    sessions = collector.sessions()
                    state = collector.snapshot()
                    sessionPendingDelete = null
                }) {
                    Text(stringResource(R.string.ground_truth_delete_confirm), color = Palette.statusCritical)
                }
            },
        )
    }

    if (confirmDeleteAll) {
        AlertDialog(
            onDismissRequest = { confirmDeleteAll = false },
            title = { Text(stringResource(R.string.ground_truth_delete_all_title)) },
            text = { Text(stringResource(R.string.ground_truth_delete_all_message, sessions.size)) },
            dismissButton = {
                TextButton(onClick = { confirmDeleteAll = false }) {
                    Text(stringResource(R.string.ground_truth_cancel))
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    sessions.forEach { vm.ble.finishGroundTruthImuCapture(it.id) }
                    collector.deleteAllSessions()
                    sessions = collector.sessions()
                    state = collector.snapshot()
                    confirmDeleteAll = false
                }) {
                    Text(stringResource(R.string.ground_truth_delete_confirm), color = Palette.statusCritical)
                }
            },
        )
    }

    markerEditor?.let { editor ->
        AlertDialog(
            onDismissRequest = { markerEditor = null },
            title = { Text(if (editor.markerId == null) "Add marker" else "Edit marker") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(diagnosticTime(editor.atMs), style = NoopType.headline)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        NoopButton("−10 s", kind = NoopButtonKind.Secondary, modifier = Modifier.weight(1f),
                            onClick = { markerEditor = editor.copy(atMs = editor.atMs - 10_000) })
                        NoopButton("+10 s", kind = NoopButtonKind.Secondary, modifier = Modifier.weight(1f),
                            onClick = { markerEditor = editor.copy(atMs = editor.atMs + 10_000) })
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("Moment", "Start", "End", "Issue").forEach { type ->
                            NoopButton(type, kind = if (editor.type == type) NoopButtonKind.Primary else NoopButtonKind.Secondary,
                                modifier = Modifier.weight(1f), onClick = { markerEditor = editor.copy(type = type) })
                        }
                    }
                    OutlinedTextField(editor.text, { markerEditor = editor.copy(text = it.take(500)) },
                        label = { Text("Marker note") }, modifier = Modifier.fillMaxWidth(), minLines = 2)
                }
            },
            dismissButton = {
                Row {
                    if (editor.markerId != null) TextButton(onClick = {
                        collector.deleteMarker(editor.sessionId, editor.markerId); sessions = collector.sessions(); markerEditor = null
                    }) { Text("Delete", color = Palette.statusCritical) }
                    TextButton(onClick = { markerEditor = null }) { Text(stringResource(R.string.ground_truth_cancel)) }
                }
            },
            confirmButton = { TextButton(onClick = {
                if (editor.markerId == null) collector.addMarker(editor.sessionId, editor.atMs, editor.type, editor.text)
                else collector.updateMarker(editor.sessionId, GroundTruthCollector.Marker(editor.markerId, editor.atMs, editor.type, editor.text))
                sessions = collector.sessions(); markerEditor = null
            }) { Text("Save") } },
        )
    }
}

@Composable
private fun SessionCard(
    session: GroundTruthCollector.SessionSummary,
    latestSensorTs: Long?,
    stats: GroundTruthCollector.CaptureStats?,
    exporting: Boolean,
    onComment: (String) -> Unit,
    onExport: () -> Unit,
    onDelete: () -> Unit,
    onAddMarker: () -> Unit,
    onEditMarker: (GroundTruthCollector.Marker) -> Unit,
    onEditStart: () -> Unit,
    onEditEnd: () -> Unit,
) {
    val time = remember(session.startedAtMs, session.endedAtMs) { sessionTimeRange(session) }
    NoopCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val sensorCovered = session.endedAtMs?.let { end -> latestSensorTs?.times(1000)?.let { it >= end } } == true
            val captureReady = stats?.complete == true
            Text(
                when {
                    session.endedAtMs == null -> "Recording · ${stats?.let(::coverageText) ?: "waiting for IMU"}"
                    captureReady -> "Ready to export · complete"
                    stats != null && stats.coveredSeconds > 0 -> "Ready with ${stats.missingSeconds}s missing · history may repair it"
                    !sensorCovered -> "Waiting for history through ${diagnosticTime(requireNotNull(session.endedAtMs))}"
                    else -> "Ready without IMU data"
                },
                style = NoopType.caption,
                color = if (captureReady) Palette.statusPositive else Palette.statusCritical,
            )
            Text(time, style = NoopType.headline, color = Palette.textPrimary)
            Text("${formatDuration((session.endedAtMs ?: System.currentTimeMillis()) - session.startedAtMs)} · IMU ${formatBytes(stats?.bytes ?: 0)}",
                style = NoopType.caption, color = Palette.textSecondary)
            session.lastExportedAtMs?.let { Text("Last exported ${diagnosticTime(it)} · export remains available",
                style = NoopType.caption, color = Palette.statusPositive) }
            if (!session.active) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    NoopButton(
                        text = stringResource(R.string.ground_truth_edit_start),
                        kind = NoopButtonKind.Secondary,
                        modifier = Modifier.weight(1f),
                        onClick = onEditStart,
                    )
                    NoopButton(
                        text = stringResource(R.string.ground_truth_edit_end),
                        kind = NoopButtonKind.Secondary,
                        modifier = Modifier.weight(1f),
                        onClick = onEditEnd,
                    )
                }
            }
            Text(
                stringResource(
                    R.string.ground_truth_session_summary,
                    session.steps,
                    session.stairs,
                ),
                style = NoopType.caption,
                color = Palette.textSecondary,
            )
            NoopButton("Add marker", kind = NoopButtonKind.Secondary, fullWidth = true, onClick = onAddMarker)
            session.markers.forEach { marker ->
                TextButton(onClick = { onEditMarker(marker) }, modifier = Modifier.fillMaxWidth()) {
                    Text("${diagnosticTime(marker.atMs)} · ${marker.type}${marker.text.takeIf(String::isNotBlank)?.let { " · $it" } ?: ""}")
                }
            }
            OutlinedTextField(
                value = session.comment,
                onValueChange = onComment,
                label = { Text(stringResource(R.string.ground_truth_comment)) },
                modifier = Modifier.fillMaxWidth(),
                minLines = 2,
                maxLines = 4,
            )
            if (session.deviceId == null) {
                Text(stringResource(R.string.ground_truth_legacy_no_sensors), style = NoopType.caption, color = Palette.textSecondary)
            }
            NoopButton(
                text = if (exporting) stringResource(R.string.ground_truth_exporting) else stringResource(R.string.ground_truth_export),
                kind = NoopButtonKind.Secondary,
                fullWidth = true,
                enabled = !session.active && !exporting,
                onClick = onExport,
            )
            NoopButton(
                text = stringResource(R.string.ground_truth_delete_session),
                kind = NoopButtonKind.Destructive,
                fullWidth = true,
                enabled = !session.active && !exporting,
                onClick = onDelete,
            )
        }
    }
}

private fun sessionTimeRange(session: GroundTruthCollector.SessionSummary): String {
    val date = java.text.DateFormat.getDateInstance(java.text.DateFormat.SHORT)
    val time = java.text.DateFormat.getTimeInstance(java.text.DateFormat.SHORT)
    val from = java.util.Date(session.startedAtMs)
    val to = session.endedAtMs?.let { java.util.Date(it) }
    return if (to == null) {
        "${date.format(from)} · ${time.format(from)}–…"
    } else {
        "${date.format(from)} · ${time.format(from)}–${time.format(to)}"
    }
}

private fun pickDateTime(
    context: Context,
    initialMs: Long,
    minimumMs: Long,
    maximumMs: Long,
    onPicked: (Long) -> Unit,
) {
    val initial = java.util.Calendar.getInstance().apply { timeInMillis = initialMs }
    val dateDialog = DatePickerDialog(context, { _, year, month, day ->
        TimePickerDialog(context, { _, hour, minute ->
            val picked = java.util.Calendar.getInstance().apply {
                set(year, month, day, hour, minute, 0)
                set(java.util.Calendar.MILLISECOND, 0)
            }.timeInMillis.coerceIn(minimumMs, maximumMs)
            onPicked(picked)
        }, initial.get(java.util.Calendar.HOUR_OF_DAY), initial.get(java.util.Calendar.MINUTE),
            android.text.format.DateFormat.is24HourFormat(context)).show()
    }, initial.get(java.util.Calendar.YEAR), initial.get(java.util.Calendar.MONTH),
        initial.get(java.util.Calendar.DAY_OF_MONTH))
    dateDialog.datePicker.minDate = minimumMs
    dateDialog.datePicker.maxDate = maximumMs
    dateDialog.show()
}

@Composable
private fun CounterColumn(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier = modifier.padding(horizontal = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = NoopType.number(32f), color = Palette.textPrimary)
        Text(label, style = NoopType.footnote, color = Palette.textSecondary, textAlign = TextAlign.Center, maxLines = 2)
    }
}

private fun KeyEvent.toCollectorKey(): GroundTruthCollector.KeyInfo {
    val input = InputDevice.getDevice(deviceId)
    return GroundTruthCollector.KeyInfo(
        keyCode = keyCode,
        keyName = KeyEvent.keyCodeToString(keyCode),
        scanCode = scanCode,
        deviceId = deviceId,
        deviceName = input?.name ?: "unknown",
        source = source,
    )
}

private fun diagnosticTime(epochMs: Long): String =
    java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault()).format(java.util.Date(epochMs))

private fun diagnosticAge(epochMs: Long): String {
    val seconds = ((System.currentTimeMillis() - epochMs).coerceAtLeast(0L) / 1_000L)
    return when {
        seconds < 60 -> "${seconds}s"
        seconds < 3_600 -> "${seconds / 60}m ${seconds % 60}s"
        else -> "${seconds / 3_600}h ${(seconds % 3_600) / 60}m"
    }
}

private data class MarkerEditor(
    val sessionId: String,
    val markerId: String?,
    val atMs: Long,
    val type: String,
    val text: String,
)

private fun formatDuration(milliseconds: Long): String {
    val seconds = milliseconds.coerceAtLeast(0) / 1_000
    val hours = seconds / 3_600
    val minutes = (seconds % 3_600) / 60
    val tail = seconds % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, tail) else "%d:%02d".format(minutes, tail)
}

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1024L * 1024L -> "%.1f MB".format(bytes / (1024.0 * 1024.0))
    bytes >= 1024L -> "%.1f KB".format(bytes / 1024.0)
    else -> "$bytes B"
}

private fun coverageText(stats: GroundTruthCollector.CaptureStats): String {
    val percent = if (stats.expectedSeconds == 0) 0.0 else 100.0 * stats.coveredSeconds / stats.expectedSeconds
    val startup = if (stats.startupSeconds > 0) " · ${stats.startupSeconds}s startup" else ""
    return "${stats.coveredSeconds}/${stats.expectedSeconds}s · %.1f%% · ${stats.missingSeconds}s missing$startup".format(percent)
}

private const val EARLIEST_EXPORT_MS = 946_684_800_000L
