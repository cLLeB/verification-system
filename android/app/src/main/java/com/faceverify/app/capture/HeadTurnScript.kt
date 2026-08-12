package com.faceverify.app.capture

/** Paces the head-turn challenge and says what to do, one instruction at a time.
 *
 *  This exists because of a fault the pilot exposed on the web client: the whole
 *  three-part challenge ran in under two seconds, and each instruction was shown AFTER
 *  its frames were already grabbed. "Turn left, turn right, look at the camera" is not
 *  performable in that time, so genuine users kept being told to turn their head more.
 *  The fix - ported here so the phone cannot drift back into the same trap - is that
 *  every instruction appears FIRST, holds for a lead-in so the person can read it and
 *  begin moving, and only then are its frames recorded.
 *
 *  Used by both capture paths. On the on-device builds the recorded frames feed the
 *  local liveness tracker and the embedder; on the online build they become the burst
 *  POSTed to /api/verify. Either way the person sees the same three instructions at
 *  the same pace. */
class HeadTurnScript {

    data class Step(val instruction: String, val frames: Int)

    private var stepIndex = 0
    private var stepStartedAt = 0L
    private var collectedInStep = 0
    private var lastAcceptedAt = 0L
    private var running = false

    val active: Boolean get() = running
    val done: Boolean get() = running && stepIndex >= STEPS.size

    /** What to show the person right now. */
    val instruction: String
        get() = when {
            !running -> ""
            stepIndex >= STEPS.size -> "Hold still…"
            else -> STEPS[stepIndex].instruction
        }

    /** 0..1 across the whole challenge, for the ring under the shutter. */
    val progress: Float
        get() {
            if (!running) return 0f
            val total = STEPS.sumOf { it.frames }.toFloat()
            val before = STEPS.take(stepIndex).sumOf { it.frames }
            return ((before + collectedInStep) / total).coerceIn(0f, 1f)
        }

    fun start(now: Long) {
        running = true
        stepIndex = 0
        stepStartedAt = now
        collectedInStep = 0
        lastAcceptedAt = 0L
    }

    fun reset() {
        running = false
        stepIndex = 0
        collectedInStep = 0
        lastAcceptedAt = 0L
    }

    /** Offer a camera frame at [now]. Returns true if it should be RECORDED - false
     *  while the instruction is still being read, or while waiting out the gap between
     *  frames, or once the script has finished. */
    fun offer(now: Long): Boolean {
        if (!running || stepIndex >= STEPS.size) return false
        if (now - stepStartedAt < LEAD_IN_MS) return false          // let them read it first
        if (lastAcceptedAt != 0L && now - lastAcceptedAt < FRAME_GAP_MS) return false

        lastAcceptedAt = now
        collectedInStep++
        if (collectedInStep >= STEPS[stepIndex].frames) {
            stepIndex++
            collectedInStep = 0
            stepStartedAt = now
            lastAcceptedAt = 0L
        }
        return true
    }

    companion object {
        /** Read the instruction, then move: nothing is recorded for this long after an
         *  instruction changes. Matches the web client's lead-in. */
        const val LEAD_IN_MS = 350L

        /** Spacing between recorded frames. 4+4+3 frames at this gap, plus three
         *  lead-ins, is about 3.8 seconds end to end - long enough to actually perform
         *  the turn, short enough that nobody gives up waiting. */
        const val FRAME_GAP_MS = 250L

        /** The same three phases, in the same order, with the same frame counts as
         *  static/app.js TURN_PHASES. A person who verified on the web meets an
         *  identical challenge here. */
        val STEPS = listOf(
            Step("⟵  Slowly turn your head LEFT", 4),
            Step("Now turn your head RIGHT  ⟶", 4),
            Step("Look straight at the camera", 3),
        )

        /** Total frames a completed run records. */
        val TOTAL_FRAMES: Int = STEPS.sumOf { it.frames }
    }
}
