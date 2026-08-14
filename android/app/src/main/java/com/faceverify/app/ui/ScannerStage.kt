package com.faceverify.app.ui

import android.graphics.Bitmap
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.BlurredEdgeTreatment
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import com.faceverify.app.R
import kotlinx.coroutines.delay

/** A true ellipse.
 *
 *  Not a detail: `CircleShape` is `RoundedCornerShape(50%)`, and a percentage corner in
 *  Compose resolves against the SMALLER dimension - so on the 7:9 camera box it drew a
 *  stadium with two straight sides, which is why the phone's viewfinder never looked
 *  like the web's `border-radius: 50%` oval. */
val EllipseShape: Shape = object : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density) =
        Outline.Generic(Path().apply { addOval(Rect(Offset.Zero, size)) })
}

/** The web's `filter: blur(34px)` expressed as a Compose blur radius.
 *  Skia takes `sigma = 0.57735 * radius + 0.5`, so 34px of sigma needs ~59dp of radius;
 *  below that the halo's conic gradient survives the blur and reads as a moving arc
 *  rather than as light. */
private val HALO_BLUR = 59.dp

/** The scanner stage, ported from the web client's `.scanner-stage`.
 *
 *  Every part of it exists on the site and is here for the same reason:
 *   * `.scan-halo`    - a rotating conic glow behind the oval
 *   * `.scanner`      - the camera, clipped to an ellipse, breathing on `pulse-ring`
 *   * `.scan-bar`     - a travelling scan light, so the camera reads as *scanning*
 *   * `.flash`        - a white blink at the moment of capture, so a shot is felt
 *   * `.scan-outline` - the dashed ellipse that says where to put your face
 *   * `.oval-swap`    - front/back camera, on the oval's lower right
 *
 *  [busy] is `.scanner.busy`: the pulse doubles in tempo and the dashed outline
 *  brightens, which is the whole feedback an attempt in progress gets on the web. */
@Composable
fun ScannerStage(
    lensFacing: Int,
    busy: Boolean,
    capturing: Boolean,
    showSwap: Boolean,
    onSwap: () -> Unit,
    shouldProcess: () -> Boolean,
    onBitmap: (Bitmap) -> Unit,
    modifier: Modifier = Modifier,
    overlay: @Composable BoxScope.() -> Unit = {},
) {
    val pal = Tok.current
    val ease = remember { CubicBezierEasing(0.16f, 1f, 0.3f, 1f) }
    val anim = rememberInfiniteTransition(label = "scanner")

    // The halo does not rotate. `shimmer-rotate` is deliberately not ported - see the
    // note where it is drawn. The web itself ships the still version under
    // `@media (prefers-reduced-motion: reduce)`.
    // .scanner - pulse-ring: scale 1 -> 1.03, opacity .55 -> .9 (1.3s while busy)
    val period = if (busy) 1300 else 2800
    val pulse by anim.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(period / 2, easing = ease), RepeatMode.Reverse),
        label = "pulse",
    )
    // .scan-bar - scanline 3.2s linear, translateY(-150% -> 150%) of its own height
    val sweep by anim.animateFloat(
        -1.5f, 1.5f, infiniteRepeatable(tween(3200, easing = LinearEasing)), label = "scanbar",
    )

    // .flash.go - a 350ms white fade, fired on the rising edge of an attempt
    var flash by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(capturing) {
        if (!capturing) return@LaunchedEffect
        val start = System.currentTimeMillis()
        while (true) {
            val t = (System.currentTimeMillis() - start) / 350f
            if (t >= 1f) break
            flash = 0.9f * (1f - t)
            delay(16)
        }
        flash = 0f
    }

    // The CALLER sizes the stage. On the web the oval is width-driven at 300px but
    // shrinks out of whatever height the rest of the screen leaves
    // (`clamp(150px, calc((100dvh - 398px) * 0.778), 300px)`), because the alternative
    // is a shutter pushed under the fold. Same rule here, expressed as a weight.
    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        val stageW = maxWidth
        val stageH = maxHeight

        // --- .scan-halo: the glow behind the oval -------------------------------
        // The web builds this from a conic gradient under `filter: blur(34px)`. That
        // construction does not survive the trip to Compose, and two attempts at it
        // proved the point: a conic gradient's colour boundaries are straight radial
        // lines, so a blur softens them across but never along, and Modifier.blur is
        // both weaker than the CSS filter (it takes a RADIUS, which Skia converts to a
        // sigma) and does not contain the rotated layer the way the CSS box does - the
        // result was a hard-edged rotating wedge, then a static diamond, neither of
        // which is what the site looks like.
        //
        // So this matches the RESULT rather than the technique: a radial glow, which
        // has no angular structure to survive a blur, no bounding box to leak, and
        // nothing to animate. It fades to fully transparent at its own edge, so its
        // softness is drawn rather than filtered and does not depend on RenderEffect
        // being available or strong enough.
        Canvas(Modifier.size(stageW * 1.34f)) {
            val r = size.minDimension / 2f
            drawCircle(
                brush = Brush.radialGradient(
                    0.00f to pal.brand.copy(alpha = 0.10f),
                    0.55f to pal.brand.copy(alpha = 0.20f),   // brightest just off the rim
                    1.00f to Color.Transparent,
                    center = center,
                    radius = r,
                ),
                radius = r,
            )
        }

        // --- .scanner: the camera, clipped to the ellipse -----------------------
        Box(
            Modifier
                .fillMaxSize()
                .graphicsLayer {
                    val s = 1f + 0.03f * pulse
                    scaleX = s
                    scaleY = s
                    alpha = 0.55f + 0.35f * pulse
                }
                .clip(EllipseShape)
                .background(Color(0xFF05080D))
                .border(4.dp, pal.bg1, EllipseShape),
            contentAlignment = Alignment.Center,
        ) {
            CameraPreview(
                modifier = Modifier.fillMaxSize(),
                lensFacing = lensFacing,
                shouldProcess = shouldProcess,
                onBitmap = onBitmap,
            )

            // .scan-bar: a 26%-tall band of brand light, travelling down the oval
            Box(
                Modifier
                    .fillMaxWidth()
                    .fillMaxHeight(0.26f)
                    .graphicsLayer { translationY = sweep * size.height }
                    .blur(3.dp)
                    .background(
                        Brush.verticalGradient(
                            listOf(Color.Transparent, pal.brand.copy(alpha = 0.42f), Color.Transparent)
                        )
                    ),
            )

            if (flash > 0f) Box(Modifier.fillMaxSize().background(Color.White.copy(alpha = flash)))

            overlay()
        }

        // --- .scan-outline: the dashed ellipse, drawn over the oval -------------
        val outlineColor = if (busy) pal.brand2 else pal.brand.copy(alpha = 0.6f)
        Canvas(Modifier.fillMaxSize()) {
            // viewBox 280x360, ellipse rx130 ry170 - the same 3.6%/2.8% inset, with the
            // dash pattern scaled out of those units so it reads identically at any size.
            val k = size.width / 280f
            val ix = size.width * 0.0357f
            val iy = size.height * 0.0278f
            drawOval(
                color = outlineColor,
                topLeft = Offset(ix, iy),
                size = Size(size.width - ix * 2, size.height - iy * 2),
                style = Stroke(
                    width = 1.5f * k,
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(4f * k, 6f * k), 0f),
                ),
            )
        }

        // --- .oval-swap: bottom 10%, right 8% of the stage ----------------------
        if (showSwap) {
            IconButton(
                onClick = onSwap,
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(end = stageW * 0.08f, bottom = stageH * 0.10f)
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(pal.bg0.copy(alpha = 0.88f))
                    .border(1.dp, pal.line, CircleShape),
            ) {
                Icon(
                    painterResource(R.drawable.ic_camera_swap),
                    contentDescription = "Switch front/back camera",
                    tint = pal.txt2,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}

/** `.result` - the verdict, filling the oval rather than the screen.
 *
 *  Granted takes the green-tinted wash the web uses (`--ok` at 28% over the page
 *  background); everything else takes the plain dark one. */
@Composable
fun ResultOverlay(ok: Boolean, title: String, sub: String, onAgain: () -> Unit) {
    val pal = Tok.current
    val tint = if (ok) pal.ok else pal.bad
    val wash = if (ok) pal.ok.copy(alpha = 0.28f).compositeOver(pal.bg0.copy(alpha = 0.86f))
               else pal.bg0.copy(alpha = 0.86f)
    Box(
        Modifier.fillMaxSize().clip(EllipseShape).background(wash),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // .result-badge: 76px circle, 2px border in the verdict colour
            Box(
                Modifier.size(76.dp).clip(CircleShape).border(2.dp, tint, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Canvas(Modifier.size(34.dp)) {
                    val w = 2.dp.toPx()
                    if (ok) {                       // ICON_OK: M20 6 9 17l-5-5
                        val p = Path().apply {
                            moveTo(size.width * 0.83f, size.height * 0.25f)
                            lineTo(size.width * 0.375f, size.height * 0.71f)
                            lineTo(size.width * 0.17f, size.height * 0.50f)
                        }
                        drawPath(p, tint, style = Stroke(width = w, cap = StrokeCap.Round))
                    } else {                        // ICON_BAD: M18 6 6 18M6 6l12 12
                        drawLine(tint, Offset(size.width * 0.75f, size.height * 0.25f),
                            Offset(size.width * 0.25f, size.height * 0.75f), w, StrokeCap.Round)
                        drawLine(tint, Offset(size.width * 0.25f, size.height * 0.25f),
                            Offset(size.width * 0.75f, size.height * 0.75f), w, StrokeCap.Round)
                    }
                }
            }
            Text(title, style = MaterialTheme.typography.titleLarge, color = pal.txt)
            Text(
                sub,
                style = MaterialTheme.typography.bodySmall,
                color = pal.txt2,
                textAlign = TextAlign.Center,
            )
            // .btn-contrast: the inverted "done" action - page text colour as the fill
            Button(
                onClick = onAgain,
                colors = ButtonDefaults.buttonColors(containerColor = pal.txt, contentColor = pal.bg0),
                shape = RoundedCornerShape(20.dp),
            ) { Text("Scan again") }
        }
    }
}
