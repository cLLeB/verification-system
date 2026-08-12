package com.faceverify.app.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.unit.dp
import com.faceverify.app.capture.CaptureQuality
import com.faceverify.app.capture.Signal

/** The three framing chips: Lighting, Distance, Angle.
 *
 *  Each is backed by a real measurement, and each says which ONE thing to change. A
 *  neutral chip means "not measured yet" - it never accuses the person of something
 *  the app has not actually checked. */
@Composable
fun QualityChips(quality: CaptureQuality, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Chip("Lighting", quality.lighting)
        Chip("Distance", quality.distance)
        Chip("Angle", quality.angle)
    }
}

@Composable
private fun Chip(label: String, signal: Signal) {
    val target = when (signal) {
        Signal.GOOD -> Ok
        Signal.BAD -> Bad
        Signal.UNKNOWN -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    val tint by animateColorAsState(target, label = "chipTint")
    Box(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(tint.copy(alpha = if (signal == Signal.UNKNOWN) 0.10f else 0.18f))
            .border(1.dp, tint.copy(alpha = 0.55f), RoundedCornerShape(999.dp))
            .padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        Text(label, color = tint, style = MaterialTheme.typography.labelMedium)
    }
}

/** The shutter: one deliberate tap starts one attempt.
 *
 *  It goes green only when all three chips are in range, so the person can see the
 *  moment the shot is worth taking rather than guessing. While an attempt runs, an arc
 *  around the rim tracks the guided challenge, and the button stops accepting taps. */
@Composable
fun Shutter(
    ready: Boolean,
    busy: Boolean,
    progress: Float,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val rim = MaterialTheme.colorScheme.outline
    val accent = MaterialTheme.colorScheme.primary
    val targetFill = when {
        !enabled -> MaterialTheme.colorScheme.surfaceVariant
        busy -> accent
        ready -> Ok
        else -> MaterialTheme.colorScheme.surfaceVariant
    }
    val fill by animateColorAsState(targetFill, label = "shutterFill")
    val sweep by animateFloatAsState(progress.coerceIn(0f, 1f), label = "shutterProgress")

    Box(
        modifier
            .size(76.dp)
            .drawBehind {
                val stroke = 4.dp.toPx()
                val inset = stroke / 2f
                val arcSize = Size(size.width - stroke, size.height - stroke)
                drawArc(                                   // the track
                    color = rim,
                    startAngle = -90f, sweepAngle = 360f, useCenter = false,
                    topLeft = androidx.compose.ui.geometry.Offset(inset, inset),
                    size = arcSize,
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
                if (sweep > 0f) drawArc(                    // progress through the challenge
                    color = accent,
                    startAngle = -90f, sweepAngle = 360f * sweep, useCenter = false,
                    topLeft = androidx.compose.ui.geometry.Offset(inset, inset),
                    size = arcSize,
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
            }
            .padding(10.dp)
            .clip(CircleShape)
            .background(fill)
            .then(if (enabled && !busy) Modifier.clickable(onClick = onClick) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier
                .size(46.dp)
                .clip(CircleShape)
                .background(Color.White.copy(alpha = if (enabled) 0.92f else 0.35f)),
        )
    }
}
