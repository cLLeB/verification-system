package com.faceverify.app.face

import com.faceverify.app.Config
import kotlin.math.abs

/** Active head-turn liveness: a flat photo/screen can't perform a real 3D turn.
 *  Feed it the head yaw of each detected frame; it passes once the user has faced
 *  the camera AND turned far enough to either side across enough frames. */
class LivenessTracker {
    private var frames = 0
    private var minYaw = Float.MAX_VALUE
    private var maxYaw = -Float.MAX_VALUE
    private var sawFrontal = false
    private var sawTurn = false

    fun record(yaw: Float) {
        frames++
        if (yaw < minYaw) minYaw = yaw
        if (yaw > maxYaw) maxYaw = yaw
        if (abs(yaw) <= Config.LIVE_FRONTAL_YAW) sawFrontal = true
        if (abs(yaw) >= Config.LIVE_TURN_YAW) sawTurn = true
    }

    val swing: Float get() = if (frames == 0) 0f else (maxYaw - minYaw)

    val passed: Boolean
        get() = frames >= Config.LIVE_MIN_FRAMES && sawFrontal && sawTurn &&
            swing >= Config.LIVE_SWING_YAW

    /** Rough 0..1 progress for the UI. */
    fun progress(): Float {
        val parts = listOf(
            (frames.toFloat() / Config.LIVE_MIN_FRAMES).coerceAtMost(1f),
            if (sawFrontal) 1f else 0f,
            if (sawTurn) 1f else 0f,
            (swing / Config.LIVE_SWING_YAW).coerceAtMost(1f),
        )
        return parts.average().toFloat()
    }

    /** Live guidance for the user given the current frame's yaw. */
    fun hint(currentYaw: Float): String = when {
        passed -> "Hold still…"
        !sawFrontal -> "Look straight at the camera"
        !sawTurn -> "Now slowly turn your head left, then right"
        else -> "Turn a little more"
    }

    fun reset() {
        frames = 0; minYaw = Float.MAX_VALUE; maxYaw = -Float.MAX_VALUE
        sawFrontal = false; sawTurn = false
    }
}
