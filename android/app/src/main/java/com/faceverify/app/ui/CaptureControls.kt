package com.faceverify.app.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.faceverify.app.capture.CaptureQuality
import com.faceverify.app.capture.Signal

/** The mode switch - the web's `.seg`: a muted track with a raised tab that slides.
 *
 *  Material's FilterChips were a different control with a different shape and a
 *  different selected colour, which is most of why the top of the screen did not look
 *  like the site. The web has two segments; this takes any number, because the
 *  on-device builds also offer "Check card" and "Glance". */
@Composable
fun SegmentedControl(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val pal = Tok.current
    val pad = 4.dp
    // `.seg` is `padding: 4px` around a 44px-min button: a fixed 52dp track. It must be
    // fixed - the thumb fills the track's height, and in a Column an unbounded track
    // would hand the thumb the whole screen and swallow everything below it.
    BoxWithConstraints(
        modifier
            .height(52.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(pal.bg2)
            .border(1.dp, pal.line, RoundedCornerShape(16.dp))
            .padding(pad),
    ) {
        val cell = (maxWidth - pad * 2) / options.size.coerceAtLeast(1)
        // .seg-thumb - transform .28s var(--ease)
        val offset by animateDpAsState(
            cell * selectedIndex,
            tween(280, easing = CubicBezierEasing(0.16f, 1f, 0.3f, 1f)),
            label = "segThumb",
        )
        Box(
            Modifier
                .offset(x = offset)
                .width(cell)
                .fillMaxHeight()
                .clip(RoundedCornerShape(12.dp))
                .background(pal.bg1)
                .border(1.dp, pal.line, RoundedCornerShape(12.dp)),
        )
        Row(Modifier.fillMaxSize()) {
            options.forEachIndexed { i, label ->
                val active = i == selectedIndex
                Box(
                    Modifier
                        .width(cell)
                        .fillMaxHeight()
                        .clip(RoundedCornerShape(12.dp))
                        .clickable { onSelect(i) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        label,
                        color = if (active) pal.txt else pal.txt2,
                        fontSize = 14.7.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}

/** The three framing chips: Lighting, Distance, Angle - the web's `.q-chip` row.
 *
 *  Each is backed by a real measurement, and each says which ONE thing to change. A
 *  neutral chip means "not measured yet": it never accuses the person of something the
 *  app has not actually checked, which is also why the row is not a wall of red before
 *  the camera has seen anything. */
@Composable
fun QualityChips(quality: CaptureQuality, compact: Boolean = false, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Chip("Lighting", quality.lighting, compact)
        Chip("Distance", quality.distance, compact)
        Chip("Angle", quality.angle, compact)
    }
}

@Composable
private fun Chip(label: String, signal: Signal, compact: Boolean = false) {
    val pal = Tok.current
    val target = when (signal) {
        Signal.GOOD -> pal.ok
        Signal.BAD -> pal.bad
        Signal.UNKNOWN -> pal.txtMuted
    }
    val tint by animateColorAsState(target, label = "chipTint")
    val neutral = signal == Signal.UNKNOWN
    // .q-chip: 12% / 14% tint fills, 45% / 55% borders, and the muted pill until a
    // signal has actually been measured.
    val fill by animateColorAsState(
        if (neutral) pal.bg1 else tint.copy(alpha = if (signal == Signal.GOOD) 0.12f else 0.14f),
        label = "chipFill",
    )
    val edge by animateColorAsState(
        if (neutral) pal.line else tint.copy(alpha = if (signal == Signal.GOOD) 0.45f else 0.55f),
        label = "chipEdge",
    )
    Row(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(fill)
            .border(1.dp, edge, RoundedCornerShape(999.dp))
            .padding(horizontal = if (compact) 8.dp else 10.dp, vertical = if (compact) 3.dp else 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // .q-chip i - a 6px dot, half-lit until the signal is real
        Box(
            Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(tint.copy(alpha = if (neutral) 0.5f else 1f)),
        )
        Text(
            label,
            color = tint,
            fontSize = if (compact) 11.2.sp else 11.8.sp,
            fontWeight = FontWeight.SemiBold,
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

/** The shutter: one deliberate tap starts one attempt - the web's `.shutter`.
 *
 *  76dp because it is the only control on the screen that matters. It goes green only
 *  when all three chips are in range, so the person can see the moment the shot is
 *  worth taking rather than guessing; while an attempt runs the core shrinks to the
 *  brand colour and an arc around the rim tracks the guided challenge. */
@Composable
fun Shutter(
    ready: Boolean,
    busy: Boolean,
    progress: Float,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    // `html[data-mode="enroll"] .shutter { width: 66px }` on a short viewport - enrol
    // carries a name row the verify screen does not, and this is one of the places the
    // web reclaims the height rather than letting the button fall off the bottom.
    compact: Boolean = false,
) {
    val diameter = if (compact) 66.dp else 76.dp
    val coreDiameter = if (compact) 50.dp else 58.dp
    val pal = Tok.current
    // .shutter-ring: 3px of --txt at 55%; --ok once ready
    val ringTarget = if (ready && enabled) pal.ok else pal.txt.copy(alpha = 0.55f)
    val ring by animateColorAsState(ringTarget, label = "shutterRing")
    // .shutter-core: --txt, --ok when ready, --brand while busy
    val coreTarget = when {
        busy -> pal.brand
        ready && enabled -> pal.ok
        else -> pal.txt
    }
    val core by animateColorAsState(coreTarget, label = "shutterCore")
    val coreScale by animateFloatAsState(if (busy) 0.7f else 1f, label = "shutterScale")
    val sweep by animateFloatAsState(progress.coerceIn(0f, 1f), label = "shutterProgress")

    Box(
        modifier
            .size(diameter)
            .drawBehind {
                val stroke = 3.dp.toPx()
                val inset = stroke / 2f
                val arc = Size(size.width - stroke, size.height - stroke)
                drawArc(                                    // .shutter-ring
                    color = ring.copy(alpha = ring.alpha * if (enabled) 1f else 0.45f),
                    startAngle = -90f, sweepAngle = 360f, useCenter = false,
                    topLeft = Offset(inset, inset), size = arc,
                    style = Stroke(width = stroke),
                )
                if (sweep > 0f) drawArc(                    // progress through the challenge
                    color = pal.brand,
                    startAngle = -90f, sweepAngle = 360f * sweep, useCenter = false,
                    topLeft = Offset(inset, inset), size = arc,
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
            }
            .then(if (enabled && !busy) Modifier.clickable(onClick = onClick) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier
                .size(coreDiameter * coreScale)
                .clip(CircleShape)
                .background(if (enabled) core else core.copy(alpha = 0.45f)),
        )
    }
}

/** Enrolment progress - the web's `.dots`.
 *
 *  Taken samples glow in the brand colour, the NEXT slot pulses so the person knows
 *  another shot is coming, and untaken slots stay muted. */
@Composable
fun Dots(captured: Int, total: Int, modifier: Modifier = Modifier) {
    val pal = Tok.current
    // soft-blink, once for the whole row rather than per dot
    val blink by rememberInfiniteTransition(label = "dots").animateFloat(
        1f, 0.35f, infiniteRepeatable(tween(800), RepeatMode.Reverse), label = "dotBlink",
    )
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(total) { i ->
            val done = i < captured
            val next = i == captured
            Box(
                Modifier
                    .size(12.dp)
                    .clip(CircleShape)
                    .background(
                        when {
                            done -> pal.brand
                            next -> pal.brand.copy(alpha = 0.4f * blink)
                            else -> pal.bg2
                        }
                    )
                    .then(
                        if (done || next) Modifier
                        else Modifier.border(1.dp, pal.line, CircleShape)
                    ),
            )
        }
    }
}
