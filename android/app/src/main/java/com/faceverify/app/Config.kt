package com.faceverify.app

/** Recognition + liveness tuning. Mirrors the server's face/config.py so behaviour
 *  is consistent across the web service and this on-device app. */
object Config {
    const val EMBED_DIM = 512
    const val FACE_SIZE = 112                 // ArcFace input is 112x112
    const val MIN_FACE_PX = 90                // smallest acceptable face (shorter side)

    // Template protection (cancelable biometrics). Synced/bundled templates arrive
    // projected into a revocable domain; live probes are projected with the shipped
    // domain seed before matching. Mirrors biometric/core/protect.py - the scheme id
    // must match the server's or pulled datasets are rejected as unmatchable.
    const val PROTECT_SCHEME = "hd3-v1"

    // Server template-envelope (BE1) version this app understands. Sync/bundle
    // payloads are decoded JSON today, but Phase 2 credentials carry BE1 envelopes;
    // mirrors biometric/core/envelope.py VERSION - keep in sync.
    const val ENVELOPE_VERSION = 1

    // matching (cosine on L2-normalised embeddings)
    const val MATCH_THRESHOLD = 0.40f         // accept if best similarity >= this
    const val IDENTIFY_MARGIN = 0.06f         // 1:N: top must beat 2nd identity by this
    const val SAMPLES_PER_USER = 3            // permanent anchor captures per person

    // Glance (continuous on-device 1:N over the synced index) - calibrated
    // SEPARATELY from 1:1: at scale the impostor MAX creeps up and glance has no
    // liveness. The server ships a calibrated threshold; the device CLAMPS it to
    // [GLANCE_MIN_THRESHOLD, +GLANCE_CLAMP_BAND] so a bad calibration can never
    // loosen below the floor. Mirrors face_service/glance.py - keep in sync.
    const val GLANCE_MIN_THRESHOLD = 0.45f        // face 1:N floor
    const val GLANCE_MIN_THRESHOLD_PALM = 0.68f   // palm 1:N floor (mirrors glance.py GLANCE_FLOORS)
    const val GLANCE_CLAMP_BAND = 0.12f
    const val GLANCE_MARGIN = 0.08f

    /** Hard device-side 1:N floor for an index of [modality] - the clamp uses this
     *  so a bad/hostile server-sent threshold can never loosen matching below it. */
    fun glanceFloor(modality: String): Float =
        if (modality == "palm") GLANCE_MIN_THRESHOLD_PALM else GLANCE_MIN_THRESHOLD

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

    // Live capture coaching - the Lighting / Distance / Angle chips.
    //
    // These predict the gate rather than enforcing it: they tell the person what is
    // wrong while they are still framing, instead of failing the shot and leaving them
    // to guess. Coaching is ADVISORY everywhere - it must never block a capture.
    //
    // Mirrors the web client (static/app.js) and the server's /api/detect?coach=1, so
    // all three surfaces light the same chip for the same frame. Sizes are a FRACTION
    // of the frame's short side, never pixels: the coaching frame is deliberately
    // downscaled, so absolute pixels are not comparable between surfaces.
    const val COACH_INTERVAL_MS = 900L        // one coaching pass per ~0.9s while idle
    const val COACH_LUMA_LOW = 55f            // usable exposure band, mean luma of the
    const val COACH_LUMA_HIGH = 240f          // ...centre 60% of the frame (Rec.709)
    const val COACH_MIN_FACE_FRAC = 0.18f     // a face under ~18% of the short side is
                                              // too far for a reliable embedding
    const val COACH_MAX_YAW = 35f             // mirrors face/config.py max_yaw_deg
    const val COACH_MAX_PITCH = 30f           // ...and max_pitch_deg

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
 *  (palmverify.db), fully isolated from face - never cross-matched. */
object PalmConfig {
    const val EMBED_DIM = 128                  // must match the exported palm ONNX output width
    const val ROI_SIZE = 128                   // square palm ROI fed to the encoder
    const val MIN_ROI_PX = 90                  // smallest acceptable ROI side in the source frame
    const val MIN_HAND_SCORE = 0.70f           // MediaPipe hand-presence confidence
    const val MIN_SHARPNESS = 35.0f            // variance-of-Laplacian floor (reject blur)
    const val MIN_FINGER_SPREAD = 0.55f        // open-palm gate (index↔pinky tip vs knuckle width)

    // STRICTER gates for ENROLMENT only (anchor quality). A weak verify frame costs
    // one retry; a weak enrolment anchor drags every future verify for that person
    // toward the impostor zone, permanently. Mirrors palm/config.py - keep in sync.
    // 2026-07-10: dropped 120 -> 50. Variance-of-Laplacian is resolution-dependent
    // and camera frames are downscaled before the ROI, so the old 120 floor was
    // unreachable on real captures and rejected every enrolment. 50 stays stricter
    // than verify (35), clears careful live captures, and rejects motion-ghosting.
    const val ENROLL_MIN_SHARPNESS = 50.0f     // reachable anchor floor on downscaled live frames
    const val ENROLL_MIN_ROI_FRAC = 0.30f      // ROI side >= 30% of the frame's short side
    const val ENROLL_MIN_BRIGHTNESS = 70.0f    // ROI luminance mean floor (too dark)
    const val ENROLL_MAX_BRIGHTNESS = 235.0f   // ...and ceiling (blown out)

    // matching (cosine on L2-normalised embeddings) - palm-tuned.
    // RE-CALIBRATED 2026-07-27 (late) on captures/ - clean, hand-curated, known-label,
    // known-time data. An earlier same-night pass calibrated on LIVE FIELD data gave
    // 0.75 and was wrong: field labels are only as good as the name typed at
    // enrolment, and several frames were filed under the wrong person (frames
    // enrolled as 'caleb' score 0.26 against caleb and 0.67-0.71 against Edwina).
    // Those pairs inflated the apparent impostor ceiling and pushed the threshold up
    // until it rejected real people.
    // Encoder input scaling. MUST equal the server's PALM_INPUT_NORM (palm/config.py
    // input_norm) whenever the two ever share templates - a hybrid build that embedded
    // differently from the server would sync vectors that are not comparable. CCNet is
    // trained through NormSingleROI (zero mean / unit std over non-zero ROI pixels);
    // feeding it plain [0,1] measurably costs matching on the HARD pairs. Flip this and
    // the server env var TOGETHER, and re-enrol - the two modes are different spaces.
    const val INPUT_NORM_ROI = true

    // Threshold for INPUT_NORM_ROI = true, chosen to hold the false-accept rate the
    // previous 0.68 gave rather than to look good: on 191 real pilot captures (15
    // people, 955 genuine attempts, hand-aware) 0.68 in the old space ran at FAR 5.45%,
    // and 0.625 in this space runs at the same 5.45% while accepting 73.3% vs 69.6%.
    //
    // The old note here - "time is NOT the variable" - STANDS, and a previous revision
    // of this comment wrongly overrode it with a "palm decays across days" claim. It
    // does not. In the pilot data every long-gap pair is ALSO a different-capture-device
    // pair, so day and device are perfectly confounded and the data cannot separate
    // them; and WITHIN different-device pairs more time does not lower the score
    // (0.600 under an hour vs 0.651 over a day). Same-device pairs sit at 0.842.
    // What is real, and what the threshold above is calibrated against, is that SOME
    // genuine pairs are hard (different sessions/conditions) while two different people
    // (Mariam/Edwina) reach 0.759 - i.e. the hard genuine pairs overlap the impostor
    // ceiling. NormSingleROI narrows that overlap; it does not fix ageing, because
    // there is no ageing to fix.
    // Mirrors palm/calibration.json - keep the two in sync.
    const val MATCH_THRESHOLD = 0.625f
    const val IDENTIFY_MARGIN = 0.08f
    const val SAMPLES_PER_USER = 3            // anchor captures per HAND
    // A person has at most two palms; one identity may enrol both (present either to
    // verify). Mirrors palm/config.py max_hands_per_user - keep in sync.
    const val MAX_HANDS_PER_USER = 2
    // No one has two right (or two left) hands: reject a second hand whose detected
    // side equals an already-enrolled hand's side. Mirrors reject_same_side_hand.
    const val REJECT_SAME_SIDE_HAND = true

    // adaptive enrolment
    const val ADAPTIVE_UPDATE_THRESHOLD = 0.45f
    const val ADAPTIVE_MARGIN = 0.08f
    const val ADAPTIVE_MAX_SAMPLES = 8
    const val ADAPTIVE_NOVELTY = 0.92f
}
