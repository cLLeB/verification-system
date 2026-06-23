package com.faceverify.app.palm

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import com.faceverify.app.PalmConfig
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.roundToInt

/** A normalised palm ROI plus the signals the quality gate needs. */
data class PalmDetection(
    val roi: Bitmap,
    val handScore: Float,
    val roiPx: Int,
    val sharpness: Float,
    val fingerSpread: Float,
)

/** Palm ROI extraction with MediaPipe Hands. Mirrors the server's palm/roi.py:
 *  detect the 21 hand landmarks, use the finger-base valleys to fix orientation +
 *  scale, then warp out a fixed-size palm crop. The HandLandmarker `.task` model is
 *  an asset (see assets/README_PALM_MODEL.md); when absent, [available] is false. */
class PalmRoi private constructor(private val landmarker: HandLandmarker) {

    fun detect(bitmap: Bitmap): PalmDetection? {
        val result: HandLandmarkerResult =
            landmarker.detect(BitmapImageBuilder(bitmap).build()) ?: return null
        if (result.landmarks().isEmpty()) return null
        val lms = result.landmarks()[0]
        val w = bitmap.width
        val h = bitmap.height
        val px = FloatArray(lms.size * 2)
        for (i in lms.indices) {
            px[i * 2] = lms[i].x() * w
            px[i * 2 + 1] = lms[i].y() * h
        }
        val score = result.handednesses().firstOrNull()?.firstOrNull()?.score() ?: 1f
        val (roi, roiPx) = extractRoi(bitmap, px) ?: return null
        return PalmDetection(roi, score, roiPx, sharpness(roi), fingerSpread(px))
    }

    private fun pt(px: FloatArray, i: Int) = Pair(px[i * 2], px[i * 2 + 1])

    private fun fingerSpread(px: FloatArray): Float {
        val (ix, iy) = pt(px, INDEX_MCP); val (px17, py17) = pt(px, PINKY_MCP)
        val mcpW = hypot((ix - px17).toDouble(), (iy - py17).toDouble()).toFloat() + 1e-6f
        val (tix, tiy) = pt(px, INDEX_TIP); val (tpx, tpy) = pt(px, PINKY_TIP)
        val tipW = hypot((tix - tpx).toDouble(), (tiy - tpy).toDouble()).toFloat()
        return tipW / mcpW
    }

    private fun extractRoi(src: Bitmap, px: FloatArray): Pair<Bitmap, Int>? {
        val (i5x, i5y) = pt(px, INDEX_MCP); val (i9x, i9y) = pt(px, MIDDLE_MCP)
        val (i13x, i13y) = pt(px, RING_MCP); val (i17x, i17y) = pt(px, PINKY_MCP)
        val v1x = (i5x + i9x) / 2f; val v1y = (i5y + i9y) / 2f
        val v2x = (i13x + i17x) / 2f; val v2y = (i13y + i17y) / 2f
        val bx = v2x - v1x; val by = v2y - v1y
        val span = hypot(bx.toDouble(), by.toDouble()).toFloat() + 1e-6f
        val ux = bx / span; val uy = by / span
        var perpX = -uy; var perpY = ux
        val midX = (v1x + v2x) / 2f; val midY = (v1y + v2y) / 2f
        val (wx, wy) = pt(px, WRIST)
        if (perpX * (wx - midX) + perpY * (wy - midY) < 0) { perpX = -perpX; perpY = -perpY }
        val side = span * 2f
        if (side < 1f) return null
        val cx = midX + perpX * (span * 0.9f); val cy = midY + perpY * (span * 0.9f)
        val angle = Math.toDegrees(atan2(uy.toDouble(), ux.toDouble())).toFloat()

        val size = PalmConfig.ROI_SIZE
        val s = size / side
        val m = Matrix().apply {
            postTranslate(-cx, -cy)
            postRotate(-angle)
            postScale(s, s)
            postTranslate(size / 2f, size / 2f)
        }
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        Canvas(out).drawBitmap(src, m, Paint(Paint.FILTER_BITMAP_FLAG))
        return Pair(out, side.roundToInt())
    }

    private fun sharpness(roi: Bitmap): Float {
        // variance of a 3x3 Laplacian on luminance — same idea as cv2.Laplacian.var()
        val w = roi.width; val h = roi.height
        val px = IntArray(w * h)
        roi.getPixels(px, 0, w, 0, 0, w, h)
        val lum = FloatArray(w * h)
        for (i in px.indices) {
            val p = px[i]
            lum[i] = 0.299f * ((p shr 16) and 0xFF) + 0.587f * ((p shr 8) and 0xFF) + 0.114f * (p and 0xFF)
        }
        var mean = 0f; var m2 = 0f; var n = 0
        for (y in 1 until h - 1) {
            for (x in 1 until w - 1) {
                val c = y * w + x
                val lap = 4 * lum[c] - lum[c - 1] - lum[c + 1] - lum[c - w] - lum[c + w]
                n++; val d = lap - mean; mean += d / n; m2 += d * (lap - mean)
            }
        }
        return if (n > 1) m2 / (n - 1) else 0f
    }

    fun close() = landmarker.close()

    companion object {
        const val MODEL_ASSET = "hand_landmarker.task"
        private const val WRIST = 0
        private const val INDEX_MCP = 5
        private const val MIDDLE_MCP = 9
        private const val RING_MCP = 13
        private const val PINKY_MCP = 17
        private const val INDEX_TIP = 8
        private const val PINKY_TIP = 20

        fun available(context: Context): Boolean = try {
            context.assets.open(MODEL_ASSET).use { true }
        } catch (_: Throwable) {
            false
        }

        fun load(context: Context): PalmRoi {
            val base = BaseOptions.builder().setModelAssetPath(MODEL_ASSET).build()
            val opts = HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(base)
                .setRunningMode(RunningMode.IMAGE)
                .setNumHands(2)
                .setMinHandDetectionConfidence(0.5f)
                .build()
            return PalmRoi(HandLandmarker.createFromOptions(context, opts))
        }
    }
}
