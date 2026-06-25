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
     *  unusable. Heavy (ONNX) — runs on the default dispatcher. */
    suspend fun embed(bitmap: Bitmap): PalmSample = withContext(Dispatchers.Default) {
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
        PalmSample(encode(det.roi), "", "ok", det.handScore, det.roiPx)
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
