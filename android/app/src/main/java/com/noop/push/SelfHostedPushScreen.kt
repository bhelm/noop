package com.noop.push

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.noop.R
import com.noop.ui.NoopButton
import com.noop.ui.NoopButtonKind
import com.noop.ui.NoopType
import com.noop.ui.Palette
import com.noop.ui.ScreenScaffold
import com.noop.ui.SettingsCard
import com.noop.ui.SettingsToggleRow
import java.text.DateFormat
import java.util.Date

/** Experimental, explicit consent surface for raw one-way health-data egress. */
@Composable
fun SelfHostedPushScreen() {
    val context = LocalContext.current
    val settings = remember { SelfHostedPushSettings.from(context) }
    var endpoint by remember { mutableStateOf(settings.endpointText()) }
    var token by remember { mutableStateOf("") }
    var snapshot by remember { mutableStateOf(settings.snapshot()) }
    var validationMessage by remember { mutableStateOf<String?>(null) }

    val endpointValid = PushEndpointPolicy.validate(endpoint) is PushEndpointPolicy.Result.Valid
    val tokenAvailable = token.isNotBlank() || snapshot.hasToken

    ScreenScaffold(
        title = stringResource(R.string.push_title),
        subtitle = stringResource(R.string.push_subtitle),
    ) {
        SettingsCard(
            icon = Icons.Filled.CloudUpload,
            title = stringResource(R.string.push_destination_title),
            blurb = stringResource(R.string.push_disclosure),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    stringResource(R.string.push_one_way_warning),
                    style = NoopType.footnote,
                    color = Palette.statusWarning,
                )
                PushSecretField(
                    value = endpoint,
                    onValueChange = { endpoint = it; validationMessage = null },
                    label = stringResource(R.string.push_endpoint),
                )
                PushSecretField(
                    value = token,
                    onValueChange = { token = it },
                    label = if (snapshot.hasToken) stringResource(R.string.push_token_saved) else stringResource(R.string.push_token),
                )
                validationMessage?.let {
                    Text(it, style = NoopType.footnote, color = Palette.statusWarning)
                }
                NoopButton(
                    text = stringResource(R.string.push_save),
                    kind = NoopButtonKind.Secondary,
                    fullWidth = true,
                    enabled = endpointValid && tokenAvailable,
                    onClick = {
                        when (val result = settings.saveEndpoint(endpoint)) {
                            is PushEndpointPolicy.Result.Invalid -> validationMessage = result.reason
                            is PushEndpointPolicy.Result.Valid -> {
                                endpoint = result.endpoint.url
                                if (token.isNotBlank()) settings.saveToken(token)
                                token = ""
                                snapshot = settings.snapshot()
                                validationMessage = context.getString(R.string.push_saved)
                            }
                        }
                    },
                )
                if (snapshot.hasToken) {
                    NoopButton(
                        text = stringResource(R.string.push_clear_token),
                        kind = NoopButtonKind.Secondary,
                        fullWidth = true,
                        onClick = {
                            settings.saveToken("")
                            settings.setEnabled(false)
                            SelfHostedPushScheduler.cancel(context)
                            snapshot = settings.snapshot()
                            validationMessage = context.getString(R.string.push_token_cleared)
                        },
                    )
                }
                SettingsToggleRow(
                    title = stringResource(R.string.push_enabled),
                    detail = stringResource(R.string.push_enabled_detail),
                    checked = snapshot.enabled,
                    onCheckedChange = { requested ->
                        if (!requested) {
                            settings.setEnabled(false)
                            SelfHostedPushScheduler.cancel(context)
                        } else if (!settings.setEnabled(true)) {
                            validationMessage = context.getString(R.string.push_config_required)
                        } else {
                            SelfHostedPushScheduler.enqueueLaunchCatchUp(context)
                        }
                        snapshot = settings.snapshot()
                    },
                )
            }
        }

        SettingsCard(
            icon = Icons.Filled.CloudUpload,
            title = stringResource(R.string.push_status_title),
            blurb = stringResource(R.string.push_status_detail),
        ) {
            val success = snapshot.lastSuccessAt?.let {
                DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it))
            } ?: stringResource(R.string.push_never)
            Text(stringResource(R.string.push_last_success, success), style = NoopType.body, color = Palette.textPrimary)
            snapshot.lastError?.let {
                Text(stringResource(R.string.push_last_error, it), style = NoopType.footnote, color = Palette.statusWarning)
            }
        }
    }
}

@Composable
private fun PushSecretField(value: String, onValueChange: (String) -> Unit, label: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        textStyle = NoopType.mono(13f),
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = Palette.textPrimary,
            unfocusedTextColor = Palette.textPrimary,
            focusedBorderColor = Palette.accent,
            unfocusedBorderColor = Palette.hairline,
            cursorColor = Palette.accent,
            focusedContainerColor = Palette.surfaceInset,
            unfocusedContainerColor = Palette.surfaceInset,
        ),
    )
}
