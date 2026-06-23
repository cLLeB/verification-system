package com.faceverify.app.face

import android.graphics.Bitmap
import android.graphics.Rect
import com.faceverify.app.Config
import kotlin.math.abs
import kotlin.math.min

/** On-device ID-document detection for enrolment — the Kotlin port of the server's
 *  face/id_document.py. Detects the *document, not the face*: a card's faint ghost
 *  portrait, a face that is small inside a larger frame, and dense print/text around
 *  the face. (The server's OpenCV-only card-outline contour signal is omitted here;
 *  the ghost + small-face + text signals carry the decision on-device.)
 *
 *  Pure and model-free: the caller supplies the detected faces and the two largest
 *  embeddings; this object only does arithmetic + a lightweight pixel scan. */
object IdDocument {

    data class Signals(val ghostPortrait: Float, val smallFaceRatio: Float, val textDensity: Float) {
        fun describe(): String =
            "ghost=%.2f small=%.2f text=%.2f".format(ghostPortrait, smallFaceRatio, textDensity)
    }

    data class Assessment(val isId: Boolean, val confidence: Float, val signals: Signals)

    // Weights mirror the server (renormalised to the signals available on-device).
    private const val W_GHOST = 0.45f
    private const val W_TEXT = 0.30f
    private const val W_SMALL = 0.25f

    private fun clamp(x: Float, lo: Float = 0f, hi: Float = 1f) = x.coerceIn(lo, hi)

    private fun area(r: Rect) = (r.width().toFloat() * r.height().toFloat()).coerceAtLeast(0f)

    /** Cosine of two L2-normalised embeddings (the embedder already normalises). */
    private fun cosine(a: FloatArray, b: FloatArray): Float {
        var s = 0f
        val n = min(a.size, b.size)
        for (i in 0 until n) s += a[i] * b[i]
        return s
    }

    /** A clearly-smaller second face that is the SAME identity as the main one = a
     *  card's ghost portrait. Two comparable, different identities => 0 (two real
     *  people, which the normal single-face gate rejects elsewhere). */
    private fun ghost(faces: List<DetectedFace>, embByFace: Map<Int, FloatArray>): Float {
        if (faces.size < 2) return 0f
        val a1 = area(faces[0].box); val a2 = area(faces[1].box)
        if (a1 <= 0f) return 0f
        val ratio = a2 / a1
        val e0 = embByFace[0] ?: return 0f
        val e1 = embByFace[1] ?: return 0f
        val sim = cosine(e0, e1)
        return if (ratio <= Config.ID_GHOST_RATIO && sim >= Config.ID_GHOST_SIMILARITY) clamp(sim) else 0f
    }

    private fun smallFace(primary: Rect, w: Int, h: Int): Float {
        val frame = (w.toFloat() * h.toFloat())
        if (frame <= 0f) return 0f
        val r = area(primary) / frame
        return clamp((0.10f - r) / 0.10f)            // r>=0.10 -> 0, r->0 -> 1
    }

    /** Edge/text density OUTSIDE the face box, computed in pure Kotlin on a downscaled
     *  grayscale copy (no OpenCV). Documents are dense with print; selfie backgrounds
     *  are smooth. Returns 0..1. */
    fun textDensityOutsideFace(bitmap: Bitmap, faceBox: Rect): Float {
        val maxW = 200
        val scale = if (bitmap.width > maxW) maxW.toFloat() / bitmap.width else 1f
        val sw = (bitmap.width * scale).toInt().coerceAtLeast(1)
        val sh = (bitmap.height * scale).toInt().coerceAtLeast(1)
        val small = Bitmap.createScaledBitmap(bitmap, sw, sh, true)
        try {
            val px = IntArray(sw * sh)
            small.getPixels(px, 0, sw, 0, 0, sw, sh)
            val gray = IntArray(sw * sh)
            for (i in px.indices) {
                val p = px[i]
                gray[i] = (((p shr 16) and 0xFF) * 30 + ((p shr 8) and 0xFF) * 59 + (p and 0xFF) * 11) / 100
            }
            // face box (with padding) in downscaled coords -> excluded from the scan
            val fx1 = (faceBox.left * scale).toInt(); val fy1 = (faceBox.top * scale).toInt()
            val fx2 = (faceBox.right * scale).toInt(); val fy2 = (faceBox.bottom * scale).toInt()
            val padX = ((fx2 - fx1) * 0.15f).toInt(); val padY = ((fy2 - fy1) * 0.15f).toInt()
            var edges = 0; var counted = 0
            for (y in 1 until sh - 1) {
                for (x in 1 until sw - 1) {
                    if (x in (fx1 - padX)..(fx2 + padX) && y in (fy1 - padY)..(fy2 + padY)) continue
                    val i = y * sw + x
                    val gx = abs(gray[i + 1] - gray[i - 1])
                    val gy = abs(gray[i + sw] - gray[i - sw])
                    if (gx + gy > 40) edges++
                    counted++
                }
            }
            if (counted == 0) return 0f
            val density = edges.toFloat() / counted
            return clamp((density - 0.05f) / 0.15f)   // 0.05 -> 0, 0.20 -> 1
        } finally {
            if (small != bitmap) small.recycle()
        }
    }

    /** Combine the signals into an is-this-an-ID decision. A clear ghost portrait
     *  alone is decisive (overrides the weighted threshold) — it is essentially only
     *  seen on ID documents, never in a normal one-person live capture. */
    fun assess(
        bitmapW: Int,
        bitmapH: Int,
        faces: List<DetectedFace>,
        embByFace: Map<Int, FloatArray>,
        textDensity: Float,
    ): Assessment {
        if (faces.isEmpty()) return Assessment(false, 0f, Signals(0f, 0f, 0f))
        val sig = Signals(
            ghostPortrait = ghost(faces, embByFace),
            smallFaceRatio = smallFace(faces[0].box, bitmapW, bitmapH),
            textDensity = textDensity,
        )
        val score = (sig.ghostPortrait * W_GHOST + sig.textDensity * W_TEXT +
            sig.smallFaceRatio * W_SMALL) / (W_GHOST + W_TEXT + W_SMALL)
        val isId = score >= Config.ID_CONFIDENCE_THRESHOLD || sig.ghostPortrait >= 0.5f
        return Assessment(isId, score, sig)
    }
}
