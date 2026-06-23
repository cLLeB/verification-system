package com.faceverify.app.face

import android.graphics.Bitmap
import android.graphics.PointF
import android.graphics.Rect
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import com.google.mlkit.vision.face.FaceLandmark
import kotlinx.coroutines.tasks.await

/** One detected face: box, head pose, and the 5 landmarks ArcFace alignment needs. */
data class DetectedFace(
    val box: Rect,
    val yaw: Float,                  // head Euler Y (left/right) in degrees
    val pitch: Float,                // head Euler X (up/down)
    val leftEye: PointF?,
    val rightEye: PointF?,
    val noseBase: PointF?,
    val mouthLeft: PointF?,
    val mouthRight: PointF?,
) {
    val facepx: Int get() = minOf(box.width(), box.height())

    /** The 5 alignment points in insightface order, or null if any is missing. */
    fun fivePoints(): Array<PointF>? {
        val l = leftEye; val r = rightEye; val n = noseBase; val ml = mouthLeft; val mr = mouthRight
        if (l == null || r == null || n == null || ml == null || mr == null) return null
        return arrayOf(l, r, n, ml, mr)
    }
}

/** On-device face detection (ML Kit, bundled model — no network/download). */
class FaceDetectorMlKit {
    private val detector = FaceDetection.getClient(
        FaceDetectorOptions.Builder()
            .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
            .setLandmarkMode(FaceDetectorOptions.LANDMARK_MODE_ALL)
            .setContourMode(FaceDetectorOptions.CONTOUR_MODE_NONE)
            .setClassificationMode(FaceDetectorOptions.CLASSIFICATION_MODE_NONE)
            .setMinFaceSize(0.15f)
            .build()
    )

    /** Detect the most prominent face in [bitmap] (already upright). Null if none. */
    suspend fun detect(bitmap: Bitmap): DetectedFace? {
        val faces = detector.process(InputImage.fromBitmap(bitmap, 0)).await()
        val face = faces.maxByOrNull { it.boundingBox.width() * it.boundingBox.height() } ?: return null
        fun pt(type: Int): PointF? = face.getLandmark(type)?.position
        return DetectedFace(
            box = face.boundingBox,
            yaw = face.headEulerAngleY,
            pitch = face.headEulerAngleX,
            leftEye = pt(FaceLandmark.LEFT_EYE),
            rightEye = pt(FaceLandmark.RIGHT_EYE),
            noseBase = pt(FaceLandmark.NOSE_BASE),
            mouthLeft = pt(FaceLandmark.MOUTH_LEFT),
            mouthRight = pt(FaceLandmark.MOUTH_RIGHT),
        )
    }

    fun close() = detector.close()
}
