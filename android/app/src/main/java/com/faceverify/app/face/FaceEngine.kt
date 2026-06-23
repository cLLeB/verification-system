package com.faceverify.app.face

import android.content.Context
import android.graphics.Bitmap
import com.faceverify.app.Config
import com.faceverify.app.data.FaceRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Ties the on-device pipeline together: detect → align → embed, plus the store.
 *  Created once (model load + index load are slow) and reused. */
class FaceEngine private constructor(
    val repo: FaceRepository,
    private val detector: FaceDetectorMlKit,
    private val embedder: Embedder,
) {
    /** Cheap per-frame detection (box + head yaw + landmarks) for liveness/guidance. */
    suspend fun detect(bitmap: Bitmap): DetectedFace? = detector.detect(bitmap)

    /** Align + embed a face from a frame. Returns null if landmarks/size are unusable.
     *  Heavy (ONNX) — runs on the default dispatcher. */
    suspend fun embed(bitmap: Bitmap, face: DetectedFace): FloatArray? = withContext(Dispatchers.Default) {
        if (face.facepx < Config.MIN_FACE_PX) return@withContext null
        val pts = face.fivePoints() ?: return@withContext null
        val aligned = FaceAligner.align(bitmap, pts) ?: return@withContext null
        embedder.embed(aligned)
    }

    companion object {
        suspend fun create(context: Context): FaceEngine = withContext(Dispatchers.IO) {
            val embedder = Embedder.load(context)            // reads model from assets
            val repo = FaceRepository(context).also { it.load() }
            FaceEngine(repo, FaceDetectorMlKit(), embedder)
        }
    }
}
