package com.noop.ui

import android.os.Build
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
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
    var excludeMinutes by remember { mutableStateOf("5") }
    var sessions by remember { mutableStateOf(collector.sessions()) }
    var latestSensorTs by remember { mutableStateOf<Long?>(null) }

    LaunchedEffect(vm.activeStrapId) {
        while (true) {
            latestSensorTs = vm.activeStrapId.takeIf(String::isNotBlank)?.let { vm.repo.latestHrSampleTs(it) }
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
                Text("Capture coverage", style = NoopType.headline, color = Palette.textPrimary)
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
                    latestSensorTs?.let { "Sensor data through: ${diagnosticTime(it * 1000)} (${diagnosticAge(it * 1000)} behind)" }
                        ?: "Sensor data through: none",
                    style = NoopType.body,
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
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = excludeMinutes,
                    onValueChange = { value -> excludeMinutes = value.filter(Char::isDigit).take(3) },
                    label = { Text(stringResource(R.string.ground_truth_minutes)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                NoopButton(
                    text = stringResource(R.string.ground_truth_exclude_recent),
                    kind = NoopButtonKind.Secondary,
                    modifier = Modifier.weight(2f),
                    enabled = excludeMinutes.toIntOrNull() in 1..240,
                    onClick = {
                        excludeMinutes.toIntOrNull()?.let { collector.excludeLastMinutes(it) }
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
        Text(stringResource(R.string.ground_truth_sessions), style = NoopType.title2, color = Palette.textPrimary)
        if (sessions.isEmpty()) {
            Text(stringResource(R.string.ground_truth_no_sessions), style = NoopType.body, color = Palette.textSecondary)
        } else {
            sessions.forEach { session ->
                SessionCard(
                    session = session,
                    latestSensorTs = latestSensorTs,
                    realtimeImuBytes = collector.realtimeImuBytes(session.id),
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
                )
            }
        }
    }
}

@Composable
private fun SessionCard(
    session: GroundTruthCollector.SessionSummary,
    latestSensorTs: Long?,
    realtimeImuBytes: Long,
    exporting: Boolean,
    onComment: (String) -> Unit,
    onExport: () -> Unit,
) {
    val time = remember(session.startedAtMs) {
        java.text.DateFormat.getDateTimeInstance(java.text.DateFormat.SHORT, java.text.DateFormat.SHORT)
            .format(java.util.Date(session.startedAtMs))
    }
    NoopCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val sensorCovered = session.endedAtMs?.let { end -> latestSensorTs?.times(1000)?.let { it >= end } } == true
            val captureReady = sensorCovered && realtimeImuBytes > 0
            Text(
                when {
                    session.endedAtMs == null -> "Export status: recording"
                    captureReady -> "Export status: ready (history covered, IMU ${realtimeImuBytes} bytes)"
                    !sensorCovered -> "Export status: wait for history sync through ${diagnosticTime(requireNotNull(session.endedAtMs))}"
                    else -> "Export status: no realtime IMU packets captured"
                },
                style = NoopType.caption,
                color = if (captureReady) Palette.statusPositive else Palette.statusCritical,
            )
            Text(time, style = NoopType.headline, color = Palette.textPrimary)
            Text(
                stringResource(
                    R.string.ground_truth_session_summary,
                    session.steps,
                    session.stairs,
                    session.excludedWindows,
                ),
                style = NoopType.caption,
                color = Palette.textSecondary,
            )
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
        }
    }
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
