package com.faceverify.app

/** Recognition + liveness tuning. Mirrors the server's face/config.py so behaviour
 *  is consistent across the web service and this on-device app. */
object Config {
    const val EMBED_DIM = 512
    const val FACE_SIZE = 112                 // ArcFace input is 112x112
    const val MIN_FACE_PX = 90                // smallest acceptable face (shorter side)

    // matching (cosine on L2-normalised embeddings)
    const val MATCH_THRESHOLD = 0.40f         // accept if best similarity >= this
    const val IDENTIFY_MARGIN = 0.06f         // 1:N: top must beat 2nd identity by this
    const val SAMPLES_PER_USER = 3            // permanent anchor captures per person

    // adaptive enrolment (track a person as they change; anti-drift)
    const val ADAPTIVE_UPDATE_THRESHOLD = 0.55f
    const val ADAPTIVE_MARGIN = 0.10f
    const val ADAPTIVE_MAX_SAMPLES = 8        // total stored embeddings cap (anchors + adaptive)
    const val ADAPTIVE_NOVELTY = 0.92f        // skip near-duplicate captures (>= this cosine)

    // active liveness (head-turn challenge), degrees of head yaw
    const val LIVE_MIN_FRAMES = 4             // frames with a detected face needed
    const val LIVE_FRONTAL_YAW = 16f          // |yaw| <= this counts as facing the camera
    const val LIVE_TURN_YAW = 18f             // need a frame with |yaw| >= this (a real turn)
    const val LIVE_SWING_YAW = 22f            // need (max - min) yaw span >= this

    // ID-document detection on enrolment (detect the document, not the face).
    // Mirrors face/id_document.py; OpenCV-only signals (card outline) are omitted
    // on-device, so detection leans on the ghost portrait + small face + text density.
    const val ID_DETECTION_ENABLED = true
    const val ID_CONFIDENCE_THRESHOLD = 0.45f
    const val ID_MIN_FACE_PX = 40             // looser than live capture (printed photos)
    const val ID_GHOST_RATIO = 0.7f           // 2nd face this much smaller => candidate ghost
    const val ID_GHOST_SIMILARITY = 0.45f     // ...and this similar => same person => ghost
}

/** Palm-print tuning. Mirrors the server's palm/config.py so the on-device palm
 *  modality behaves like the service. Palm data lives in its OWN encrypted DB
 *  (palmverify.db), fully isolated from face — never cross-matched. */
object PalmConfig {
    const val EMBED_DIM = 128                  // must match the exported palm ONNX output width
    const val ROI_SIZE = 128                   // square palm ROI fed to the encoder
    const val MIN_ROI_PX = 90                  // smallest acceptable ROI side in the source frame
    const val MIN_HAND_SCORE = 0.70f           // MediaPipe hand-presence confidence
    const val MIN_SHARPNESS = 35.0f            // variance-of-Laplacian floor (reject blur)
    const val MIN_FINGER_SPREAD = 0.55f        // open-palm gate (index↔pinky tip vs knuckle width)

    // STRICTER gates for ENROLMENT only (anchor quality). A weak verify frame costs
    // one retry; a weak enrolment anchor drags every future verify for that person
    // toward the impostor zone, permanently. Mirrors palm/config.py — keep in sync.
    const val ENROLL_MIN_SHARPNESS = 120.0f    // crisp anchors only (good light, held still)
    const val ENROLL_MIN_ROI_FRAC = 0.30f      // ROI side >= 30% of the frame's short side
    const val ENROLL_MIN_BRIGHTNESS = 70.0f    // ROI luminance mean floor (too dark)
    const val ENROLL_MAX_BRIGHTNESS = 235.0f   // ...and ceiling (blown out)

    // matching (cosine on L2-normalised embeddings) — palm-tuned.
    // RE-CALIBRATED 2026-07-02 against live production templates + local captures
    // (CCNet ONNX, max-over-anchors — the metric verify actually uses): genuine
    // cross-session 0.672-0.879 (blur-robust: a ghosted frame still hit 0.846) vs
    // impostor 0.603-0.623. The old 0.60 sat BELOW the impostor ceiling (observed
    // false-accepts); 0.65 is the measured-gap midpoint. Mirrors palm/calibration.json
    // — keep the two in sync.
    const val MATCH_THRESHOLD = 0.65f
    const val IDENTIFY_MARGIN = 0.05f
    const val SAMPLES_PER_USER = 3

    // adaptive enrolment
    const val ADAPTIVE_UPDATE_THRESHOLD = 0.45f
    const val ADAPTIVE_MARGIN = 0.08f
    const val ADAPTIVE_MAX_SAMPLES = 8
    const val ADAPTIVE_NOVELTY = 0.92f
}
