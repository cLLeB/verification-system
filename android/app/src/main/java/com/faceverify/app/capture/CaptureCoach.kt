package com.faceverify.app.capture

import android.graphics.Bitmap
import com.faceverify.app.Config
import com.faceverify.app.PalmConfig
import com.faceverify.app.face.FaceDetectorMlKit
import com.faceverify.app.palm.PalmRoi
import kotlin.math.abs
import kotlin.math.min

/** Live framing guidance: what the Lighting / Distance / Angle chips report.
 *
 *  Runs on the DETECTORS only - ML Kit's face detector and MediaPipe's hand landmarker
 *  - never on the recognition models. That is deliberate: the detectors are small and
 *  bundled in every flavor including "online", so coaching is instant and free on every
 *  build, and the online build needs no network round-trip just to say "move closer".
 *
 *  Mirrors the server's /api/detect?coach=1 so the same frame lights the same chips on
 *  the phone and in the browser. Sizes are compared as a FRACTION of the frame's short
 *  side, not in pixels, because the coaching frame is downscaled.
 *
 *  Every path here is best-effort: a failure returns UNKNOWN chips and never throws
 *  into the capture path. Coaching must never be able to block a capture. */
class CaptureCoach(
    private val faces: FaceDetectorMlKit,
    private val palm: PalmRoi?,
) {
    /** Assess one frame. [palmFirst] mirrors the verify-side routing choice: probing
     *  the palm first stops a bystander's face hijacking a deliberate palm capture. */
    suspend fun assess(bitmap: Bitmap, palmFirst: Boolean = false): CaptureQuality {
        val lighting = try { Luma.signal(bitmap) } catch (_: Throwable) { Signal.UNKNOWN }
        val short = min(bitmap.width, bitmap.height).toFloat().coerceAtLeast(1f)

        if (palmFirst) {
            palmQuality(bitmap, short, lighting)?.let { return it }
            faceQuality(bitmap, short, lighting)?.let { return it }
        } else {
            faceQuality(bitmap, short, lighting)?.let { return it }
            palmQuality(bitmap, short, lighting)?.let { return it }
        }
        return CaptureQuality("none", lighting, Signal.UNKNOWN, Signal.UNKNOWN)
    }

    private suspend fun faceQuality(bitmap: Bitmap, short: Float, lighting: Signal): CaptureQuality? {
        val face = try { faces.detect(bitmap) } catch (_: Throwable) { null } ?: return null
        val frac = face.facepx / short
        return CaptureQuality(
            modality = "face",
            lighting = lighting,
            distance = if (frac >= Config.COACH_MIN_FACE_FRAC) Signal.GOOD else Signal.BAD,
            angle = if (abs(face.yaw) <= Config.COACH_MAX_YAW &&
                abs(face.pitch) <= Config.COACH_MAX_PITCH) Signal.GOOD else Signal.BAD,
        )
    }

    private fun palmQuality(bitmap: Bitmap, short: Float, lighting: Signal): CaptureQuality? {
        val p = palm ?: return null
        val det = try { p.detect(bitmap) } catch (_: Throwable) { null } ?: return null
        if (det.handScore < PalmConfig.MIN_HAND_SCORE) {
            det.roi.recycle()
            return null
        }
        val frac = det.roiPx / short
        det.roi.recycle()          // the coach only needs the measurements, not the crop
        return CaptureQuality(
            modality = "palm",
            lighting = lighting,
            // The server's coach chip predicts the ENROL gate in both modes: it is the
            // stricter of the two, so a frame that satisfies it satisfies verify too.
            distance = if (frac >= PalmConfig.ENROLL_MIN_ROI_FRAC) Signal.GOOD else Signal.BAD,
            angle = if (det.fingerSpread >= PalmConfig.MIN_FINGER_SPREAD) Signal.GOOD else Signal.BAD,
        )
    }
}
