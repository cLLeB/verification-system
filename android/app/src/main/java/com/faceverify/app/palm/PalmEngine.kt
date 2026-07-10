package com.faceverify.app.palm

import android.content.Context
import android.graphics.Bitmap
import com.faceverify.app.PalmConfig
import com.faceverify.app.data.PalmRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Outcome of trying to turn a frame into a palm embedding. */
data class PalmSample(
    val embedding: FloatArray?,
    val code: String,           // "" on success, else a quality/availability code
    val message: String,
    val handScore: Float = 0f,
    val roiPx: Int = 0,
    val handedness: String = "",  // 'Left' | 'Right' (MediaPipe; "" if unknown)
)

/** Ties the on-device palm pipeline together: detect hand → ROI → quality gate →
 *  embed, plus the (separate) palm store. Mirrors the server's palm/engine.py +
 *  palm/api.py. Created once and reused; palm data is isolated from face. */
class PalmEngine private constructor(
    val repo: PalmRepository,
    private val roi: PalmRoi,
    private val embedder: PalmEmbedder?,   // null => use the built-in Gabor encoder
) {
    private fun encode(roi: Bitmap): FloatArray =
        embedder?.embed(roi) ?: PalmGabor.encode(roi)
    /** Detect + quality-gate + embed a palm from a frame. Returns a [PalmSample]
     *  whose [PalmSample.embedding] is null (with a code) when the capture is
     *  unusable. Heavy (ONNX) — runs on the default dispatcher.
     *
     *  [forEnroll] applies the STRICT anchor-quality gate on top (crisp, well-lit,
     *  palm filling the frame) — a weak verify frame costs one retry, but a weak
     *  enrolment anchor degrades that person's matching forever. Mirrors the
     *  server's palm/roi.py enroll_quality_ok. */
    suspend fun embed(bitmap: Bitmap, forEnroll: Boolean = false): PalmSample = withContext(Dispatchers.Default) {
        val det = roi.detect(bitmap)
            ?: return@withContext PalmSample(null, "no_hand", "No palm detected — show an open hand.")
        if (det.handScore < PalmConfig.MIN_HAND_SCORE)
            return@withContext PalmSample(null, "no_hand", "Hold an open palm to the camera.", det.handScore, det.roiPx)
        if (det.roiPx < PalmConfig.MIN_ROI_PX)
            return@withContext PalmSample(null, "palm_too_small", "Move your hand closer.", det.handScore, det.roiPx)
        if (det.sharpness < PalmConfig.MIN_SHARPNESS)
            return@withContext PalmSample(null, "palm_blurry", "Hold steady — keep your palm in focus.", det.handScore, det.roiPx)
        if (det.fingerSpread < PalmConfig.MIN_FINGER_SPREAD)
            return@withContext PalmSample(null, "fingers_not_spread", "Spread your fingers and open your palm.", det.handScore, det.roiPx)
        if (forEnroll) {
            if (det.sharpness < PalmConfig.ENROLL_MIN_SHARPNESS)
                return@withContext PalmSample(null, "palm_enroll_blurry",
                    "Enrolment needs a crisp shot — brace your arm, add light, let the camera focus.",
                    det.handScore, det.roiPx)
            val frameShort = minOf(bitmap.width, bitmap.height).toFloat()
            if (det.roiPx < PalmConfig.ENROLL_MIN_ROI_FRAC * frameShort)
                return@withContext PalmSample(null, "palm_enroll_too_far",
                    "Bring your palm closer — fill most of the frame to enrol.", det.handScore, det.roiPx)
            val bright = brightness(det.roi)
            if (bright < PalmConfig.ENROLL_MIN_BRIGHTNESS)
                return@withContext PalmSample(null, "palm_enroll_too_dark",
                    "Too dark to enrol — face a window or add light.", det.handScore, det.roiPx)
            if (bright > PalmConfig.ENROLL_MAX_BRIGHTNESS)
                return@withContext PalmSample(null, "palm_enroll_too_bright",
                    "Too bright to enrol — avoid direct glare on your palm.", det.handScore, det.roiPx)
        }
        PalmSample(encode(det.roi), "", "ok", det.handScore, det.roiPx, det.handedness)
    }

    /** Mean luminance of the ROI, sampled on a small downscale (cheap, allocation-light). */
    private fun brightness(roi: Bitmap): Float {
        val s = Bitmap.createScaledBitmap(roi, 32, 32, true)
        val px = IntArray(32 * 32)
        s.getPixels(px, 0, 32, 0, 0, 32, 32)
        if (s !== roi) s.recycle()
        var sum = 0.0
        for (c in px) {
            val r = (c shr 16) and 0xFF; val g = (c shr 8) and 0xFF; val b = c and 0xFF
            sum += 0.299 * r + 0.587 * g + 0.114 * b
        }
        return (sum / px.size).toFloat()
    }

    /** Cheap presence probe for the on-device router: is there a usable hand? */
    suspend fun hasPalm(bitmap: Bitmap): Pair<Boolean, Float> = withContext(Dispatchers.Default) {
        val det = roi.detect(bitmap) ?: return@withContext false to 0f
        (det.handScore >= PalmConfig.MIN_HAND_SCORE) to det.handScore
    }

    companion object {
        /** Palm works with just the hand-landmarker task (free, ~7 MB). The palm
         *  ONNX is an optional accuracy upgrade — recognition falls back to the
         *  built-in Gabor encoder when it's absent, so palm is NOT gated on it. */
        fun available(context: Context): Boolean = PalmRoi.available(context)

        suspend fun create(context: Context): PalmEngine = withContext(Dispatchers.IO) {
            val embedder = if (PalmEmbedder.available(context)) PalmEmbedder.load(context) else null
            val roi = PalmRoi.load(context)
            val repo = PalmRepository(context).also { it.load() }
            PalmEngine(repo, roi, embedder)
        }
    }
}
