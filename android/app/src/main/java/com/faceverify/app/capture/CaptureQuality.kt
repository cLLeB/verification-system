package com.faceverify.app.capture

import android.graphics.Bitmap
import com.faceverify.app.Config

/** One coaching chip's state. UNKNOWN is not a failure - it means "not measured yet",
 *  and the chip stays neutral rather than accusing the person of something. */
enum class Signal { GOOD, BAD, UNKNOWN }

/** What the live capture guidance knows about the current frame.
 *
 *  Three signals, each backed by a real measurement rather than a guess:
 *    lighting - mean luma of the centre of the frame (computed on this device, free).
 *    distance - how much of the frame the detected face/palm fills.
 *    angle    - head yaw/pitch inside the accept range, or a palm facing the camera
 *               with its fingers spread.
 *
 *  Mirrors the web client's three chips so a person who has used the web verifier
 *  reads the same three words here. */
data class CaptureQuality(
    val modality: String = "none",           // "face" | "palm" | "none"
    val lighting: Signal = Signal.UNKNOWN,
    val distance: Signal = Signal.UNKNOWN,
    val angle: Signal = Signal.UNKNOWN,
) {
    /** All three measured and in range - the shutter goes green. */
    val allGood: Boolean
        get() = lighting == Signal.GOOD && distance == Signal.GOOD && angle == Signal.GOOD

    /** The single most useful thing to say, or null when nothing needs saying.
     *  One instruction at a time: a list of three complaints is not actionable. */
    fun hint(): String? = when {
        allGood -> null
        modality == "none" -> "Show your face - or your open hand"
        lighting == Signal.BAD -> "Find better light"
        distance == Signal.BAD -> "Move a little closer"
        angle == Signal.BAD ->
            if (modality == "palm") "Open your hand, palm flat to the camera"
            else "Look straight at the camera"
        else -> null
    }
}

/** Exposure measured the same way on every surface: mean Rec.709 luma over the centre
 *  60% of the frame, which is roughly what the capture oval covers and where the
 *  subject actually is. Sampled down to a tiny grid first - this runs per coaching
 *  pass and must stay far cheaper than the detector. */
object Luma {
    private const val GRID_W = 48
    private const val GRID_H = 60

    /** Mean luma 0..255, or null if the bitmap is unusable. */
    fun centreMean(bitmap: Bitmap): Float? {
        if (bitmap.isRecycled || bitmap.width < 2 || bitmap.height < 2) return null
        val cw = (bitmap.width * 0.6f).toInt().coerceAtLeast(1)
        val ch = (bitmap.height * 0.6f).toInt().coerceAtLeast(1)
        val left = (bitmap.width - cw) / 2
        val top = (bitmap.height - ch) / 2

        var sum = 0f
        var n = 0
        for (gy in 0 until GRID_H) {
            val y = top + (gy * ch) / GRID_H
            for (gx in 0 until GRID_W) {
                val x = left + (gx * cw) / GRID_W
                val p = bitmap.getPixel(x, y)
                sum += 0.2126f * ((p shr 16) and 0xFF) +
                    0.7152f * ((p shr 8) and 0xFF) +
                    0.0722f * (p and 0xFF)
                n++
            }
        }
        return if (n == 0) null else sum / n
    }

    /** The lighting chip for a frame. */
    fun signal(bitmap: Bitmap): Signal {
        val luma = centreMean(bitmap) ?: return Signal.UNKNOWN
        return if (luma >= Config.COACH_LUMA_LOW && luma <= Config.COACH_LUMA_HIGH)
            Signal.GOOD else Signal.BAD
    }
}
