package com.noop.ui

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.text.NumberFormat
import kotlin.math.ceil

internal fun stepsAxisMaximum(values: List<Double>): Double =
    (ceil((values.filter { it.isFinite() }.maxOrNull() ?: 0.0) / 5000.0) * 5000.0).coerceAtLeast(5000.0)

/** A fixed readout above the plot stays visible while a finger scrubs across daily/aggregated bars. */
@Composable
internal fun StepsDetailChart(series: StepsDetailUiSeries, showValues: Boolean) {
    val values = series.points.map { it.second }
    var selected by remember(series) { mutableIntStateOf(values.lastIndex) }
    var holding by remember(series) { mutableStateOf(false) }
    val format = remember { NumberFormat.getIntegerInstance() }
    val density = LocalDensity.current
    val axisWidth = with(density) { 54.dp.toPx() }
    val labelPaint = remember(density) { Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Palette.textSecondary.toArgb()
        textSize = with(density) { 10.sp.toPx() }
    } }
    Column(Modifier.fillMaxWidth().semantics { contentDescription = series.accessibilitySummary }) {
        Text(series.selectionLabels.getOrNull(selected).orEmpty(), fontSize = 20.sp, color = Palette.textPrimary)
        Text(values.getOrNull(selected)?.let { stepsBucketValueLabel(it, series.granularity) }.orEmpty(),
            fontSize = 24.sp, color = Palette.metricCyan)
        Spacer(Modifier.height(12.dp))
        Canvas(Modifier.fillMaxWidth().height(230.dp).pointerInput(series, axisWidth) {
            awaitEachGesture {
                val down = awaitFirstDown(requireUnconsumed = false)
                fun select(x: Float) {
                    if (values.isNotEmpty() && size.width > axisWidth) {
                        selected = (((x - axisWidth) / (size.width - axisWidth)) * values.size)
                            .toInt().coerceIn(0, values.lastIndex)
                    }
                }
                select(down.position.x)
                holding = true
                try {
                    do {
                        val event = awaitPointerEvent()
                        val pointer = event.changes.firstOrNull { it.id == down.id } ?: break
                        select(pointer.position.x)
                        // Own horizontal scrubbing; the surrounding screen still scrolls outside the plot.
                        pointer.consume()
                    } while (pointer.pressed)
                } finally { holding = false }
            }
        }) {
            val top = 22.dp.toPx()
            val bottom = size.height - 12.dp.toPx()
            val plotHeight = bottom - top
            val maximum = stepsAxisMaximum(values)
            val plotWidth = (size.width - axisWidth).coerceAtLeast(1f)
            val slot = plotWidth / values.size.coerceAtLeast(1)
            val tickCount = (maximum / 5000).toInt()
            for (tick in 0..tickCount) {
                val value = tick * 5000.0
                val y = bottom - (value / maximum).toFloat() * plotHeight
                drawLine(Palette.hairline, Offset(axisWidth, y), Offset(size.width, y))
                drawContext.canvas.nativeCanvas.drawText(format.format(value), 0f, y + 3.dp.toPx(), labelPaint)
            }
            values.forEachIndexed { index, value ->
                if (!value.isFinite()) return@forEachIndexed
                val x = axisWidth + slot * (index + 0.5f)
                val y = bottom - (value / maximum).toFloat() * plotHeight
                val tint = if (holding && index != selected) Palette.metricCyan.copy(alpha = 0.22f) else Palette.metricCyan
                drawRect(tint, Offset(x - slot * 0.32f, y), androidx.compose.ui.geometry.Size(slot * 0.64f, (bottom - y).coerceAtLeast(0f)))
                if (holding && index == selected) drawLine(Palette.metricCyan, Offset(x, y), Offset(x, 0f),
                    strokeWidth = 1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx())))
                if (showValues) {
                    val label = format.format(value)
                    val oldSize = labelPaint.textSize
                    // Fit 14 five-digit counts without truncating or colliding with adjacent bars.
                    val measured = labelPaint.measureText(label)
                    if (measured > slot - 2.dp.toPx()) labelPaint.textSize *= ((slot - 2.dp.toPx()) / measured).coerceAtLeast(0.5f)
                    drawContext.canvas.nativeCanvas.drawText(label, x - labelPaint.measureText(label) / 2, y - 4.dp.toPx(), labelPaint)
                    labelPaint.textSize = oldSize
                }
            }
        }
    }
}
