package com.faceverify.app.face

import android.content.Context
import android.graphics.Bitmap
import com.faceverify.app.Config
import com.faceverify.app.data.FaceRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.min

/** Result of assessing an enrolment frame for an ID document: every detected face,
 *  the is-this-an-ID verdict, and the largest face + its embedding ready to enrol. */
data class IdEnrollAssessment(
    val faces: List<DetectedFace>,
    val assessment: IdDocument.Assessment,
    val primaryFace: DetectedFace?,
    val primaryEmbedding: FloatArray?,
)

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
     *  Heavy (ONNX) — runs on the default dispatcher. [minPx] lets the ID path accept
     *  smaller (printed) faces than a live capture. */
    suspend fun embed(bitmap: Bitmap, face: DetectedFace, minPx: Int = Config.MIN_FACE_PX): FloatArray? =
        withContext(Dispatchers.Default) {
            if (face.facepx < minPx) return@withContext null
            val pts = face.fivePoints() ?: return@withContext null
            val aligned = FaceAligner.align(bitmap, pts) ?: return@withContext null
            embedder.embed(aligned)
        }

    /** Detect-the-document, not-the-face: assess whether this enrolment frame is an
     *  ID card/passport. Embeds the two largest faces (ghost check needs both), scans
     *  text density, and combines the signals. The largest face's embedding is returned
     *  so the ID branch can enrol it without re-embedding. */
    suspend fun assessIdForEnroll(bitmap: Bitmap): IdEnrollAssessment = withContext(Dispatchers.Default) {
        val faces = detector.detectAll(bitmap)
        if (faces.isEmpty()) {
            return@withContext IdEnrollAssessment(
                faces, IdDocument.Assessment(false, 0f, IdDocument.Signals(0f, 0f, 0f)), null, null)
        }
        val embByFace = HashMap<Int, FloatArray>()
        for (i in 0 until min(2, faces.size)) {
            embed(bitmap, faces[i], Config.ID_MIN_FACE_PX)?.let { embByFace[i] = it }
        }
        val text = IdDocument.textDensityOutsideFace(bitmap, faces[0].box)
        val a = IdDocument.assess(bitmap.width, bitmap.height, faces, embByFace, text)
        IdEnrollAssessment(faces, a, faces[0], embByFace[0])
    }

    companion object {
        suspend fun create(context: Context): FaceEngine = withContext(Dispatchers.IO) {
            val embedder = Embedder.load(context)            // reads model from assets
            val repo = FaceRepository(context).also { it.load() }
            FaceEngine(repo, FaceDetectorMlKit(), embedder)
        }
    }
}
