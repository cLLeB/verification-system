package com.faceverify.app.palm

import android.graphics.Bitmap
import com.faceverify.app.data.EnrollResult
import com.faceverify.app.face.Decision
import com.faceverify.app.face.FaceEngine

/** On-device auto-router — the front door, mirroring the server's
 *  face_service/modality.py. The user never chooses face vs palm: every frame is
 *  detected and routed. Face is tried first and, when a face is present, the palm
 *  detector is skipped (the cheap short-circuit). Palm runs only when face is
 *  absent — or when palm is the only thing in frame. Falls through gracefully when
 *  the palm engine isn't installed (face-only device).
 *
 *  A `userId` may be enrolled with a face, a palm, or both; presenting either
 *  verifies them — a match is a match. Face and palm stores stay separate. */
class ModalityRouter(
    private val face: FaceEngine,
    private val palm: PalmEngine?,
) {
    enum class Modality { FACE, PALM, NONE }

    data class Outcome(
        val modality: Modality,
        val success: Boolean,
        val userId: String?,
        val score: Float,
        val code: String,
        val message: String,
    )

    /** Decide which modality a frame holds (face-first short-circuit). */
    suspend fun route(bitmap: Bitmap): Modality {
        if (face.detect(bitmap) != null) return Modality.FACE
        if (palm != null && palm.hasPalm(bitmap).first) return Modality.PALM
        return Modality.NONE
    }

    suspend fun enroll(userId: String, bitmap: Bitmap): Outcome = when (route(bitmap)) {
        Modality.FACE -> {
            val d = face.detect(bitmap)
            val emb = d?.let { face.embed(bitmap, it) }
            if (emb == null) Outcome(Modality.FACE, false, null, -1f, "low_quality", "Couldn't read the face — move closer, good light.")
            else face.repo.enroll(userId, emb).toOutcome(Modality.FACE)
        }
        Modality.PALM -> palmEnroll(userId, bitmap)
        Modality.NONE -> noBiometric()
    }

    suspend fun verify(userId: String, bitmap: Bitmap): Outcome = when (route(bitmap)) {
        Modality.FACE -> {
            val d = face.detect(bitmap); val emb = d?.let { face.embed(bitmap, it) }
            if (emb == null) faceUnreadable()
            else face.repo.verify(userId, emb).toOutcome(Modality.FACE).also {
                face.repo.maybeAdapt(face.repo.verify(userId, emb), emb, userId)
            }
        }
        Modality.PALM -> {
            val s = palm?.embed(bitmap) ?: return unavailable()
            if (s.embedding == null) palmBad(s)
            else palm.repo.verify(userId, s.embedding).toOutcome(Modality.PALM).also {
                palm.repo.maybeAdapt(it.toDecision(), s.embedding, userId)
            }
        }
        Modality.NONE -> noBiometric()
    }

    suspend fun identify(bitmap: Bitmap): Outcome = when (route(bitmap)) {
        Modality.FACE -> {
            val d = face.detect(bitmap); val emb = d?.let { face.embed(bitmap, it) }
            if (emb == null) faceUnreadable()
            else face.repo.identify(emb).toOutcome(Modality.FACE).also {
                if (it.success) face.repo.maybeAdapt(it.toDecision(), emb, null)
            }
        }
        Modality.PALM -> {
            val s = palm?.embed(bitmap) ?: return unavailable()
            if (s.embedding == null) palmBad(s)
            else palm.repo.identify(s.embedding).toOutcome(Modality.PALM).also {
                if (it.success) palm.repo.maybeAdapt(it.toDecision(), s.embedding, null)
            }
        }
        Modality.NONE -> noBiometric()
    }

    // --- helpers -----------------------------------------------------------
    private suspend fun palmEnroll(userId: String, bitmap: Bitmap): Outcome {
        val s = palm?.embed(bitmap) ?: return unavailable()
        return if (s.embedding == null) palmBad(s)
        else palm.repo.enroll(userId, s.embedding).toOutcome(Modality.PALM)
    }

    private fun Decision.toOutcome(m: Modality) = Outcome(
        m, granted, userId, score, if (granted) "match" else "no_match",
        if (granted) "Identity confirmed." else "Does not match.")

    private fun EnrollResult.toOutcome(m: Modality) = Outcome(m, success, null, 0f, code, message)

    private fun Outcome.toDecision() = Decision(success, userId, score, 0f)

    private fun noBiometric() = Outcome(Modality.NONE, false, null, -1f, "no_biometric_detected",
        "No face or palm detected — show one clearly, in good light.")
    private fun unavailable() = Outcome(Modality.PALM, false, null, -1f, "palm_unavailable",
        "Palm recognition isn't installed on this device.")
    private fun faceUnreadable() = Outcome(Modality.FACE, false, null, -1f, "low_quality",
        "Couldn't read the face — move closer, good light.")
    private fun palmBad(s: PalmSample) = Outcome(Modality.PALM, false, null, -1f, s.code, s.message)
}
