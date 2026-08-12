package com.faceverify.app.capture

/** Which failures are worth trying again by themselves, and how many times.
 *
 *  A run that fails on POSITIONING ("turn your head a bit more", "move closer",
 *  "nothing detected") used to stop dead: the person reads the advice, repositions,
 *  and waits - without realising the attempt ended and the shutter needs pressing
 *  again. They had already tapped, so continuing is not a new consent decision; it
 *  finishes the attempt they started.
 *
 *  Only codes a person can FIX BY MOVING retry. A real decision - not recognised,
 *  access denied, consent withdrawn, duplicate, wrong hand - never does, because
 *  repeating those is pointless and buries the answer. The run is capped so it can
 *  never loop, and any manual tap takes over and resets the budget.
 *
 *  Mirrors RETRY_CODES in static/app.js - the same failure behaves the same way on
 *  the phone as in the browser. */
object RetryPolicy {

    /** Failures a person can fix by moving. */
    private val COACHABLE = setOf(
        "liveness",
        "low_quality",
        "multiple_faces",
        "no_biometric_detected",
        "no_hand",
        "palm_too_small",
        "palm_blurry",
        "fingers_not_spread",
        "palm_not_facing",
        "multiple_hands",
        "palm_enroll_blurry",
        "palm_enroll_too_far",
        "palm_enroll_too_dark",
        "palm_enroll_too_bright",
    )

    /** Automatic attempts allowed per deliberate tap. */
    const val MAX_ATTEMPTS = 3

    /** Long enough to read the advice and actually move before the next attempt. */
    const val DELAY_MS = 2600L

    fun isCoachable(code: String?): Boolean = code != null && code in COACHABLE
}
