package com.faceverify.app.face

import com.faceverify.app.Config

data class Decision(
    val granted: Boolean,
    val userId: String?,
    val score: Float,
    val margin: Float,
)

/** Cosine matching + 1:1 / 1:N decisions. Mirrors the server's face/matcher.py. */
object Matcher {

    fun cosine(a: FloatArray, b: FloatArray): Float {
        var s = 0f
        val n = minOf(a.size, b.size)
        for (i in 0 until n) s += a[i] * b[i]
        return s
    }

    fun bestScore(probe: FloatArray, embeddings: List<FloatArray>): Float {
        var best = -1f
        for (e in embeddings) { val c = cosine(probe, e); if (c > best) best = c }
        return best
    }

    /** 1:1 - does the probe match this person's stored embeddings? Face uses the
     *  default threshold; palm passes its own (the logic is modality-agnostic). */
    fun verify(probe: FloatArray, embeddings: List<FloatArray>,
               matchThreshold: Float = Config.MATCH_THRESHOLD): Decision {
        val s = bestScore(probe, embeddings)
        return Decision(s >= matchThreshold, null, s, 0f)
    }

    /** 1:N decision over ALREADY-SCORED identities: top must clear the threshold
     *  AND beat the runner-up by the margin, so look-alikes don't slip through.
     *  Split out so callers can score each user with a per-domain probe
     *  (protected templates - see Protect.kt). */
    fun decide(scored: List<Pair<String, Float>>,
               matchThreshold: Float = Config.MATCH_THRESHOLD,
               identifyMargin: Float = Config.IDENTIFY_MARGIN): Decision {
        if (scored.isEmpty()) return Decision(false, null, -1f, 0f)
        val ranked = scored.sortedByDescending { it.second }
        val (topId, top) = ranked[0]
        val second = if (ranked.size > 1) ranked[1].second else -1f
        val margin = top - second
        val granted = top >= matchThreshold &&
            (ranked.size == 1 || margin >= identifyMargin)
        return Decision(granted, if (granted) topId else null, top, margin)
    }

    /** 1:N - who is this? (single-domain convenience over [decide]). */
    fun identify(probe: FloatArray, people: List<Pair<String, List<FloatArray>>>,
                 matchThreshold: Float = Config.MATCH_THRESHOLD,
                 identifyMargin: Float = Config.IDENTIFY_MARGIN): Decision =
        decide(people.map { it.first to bestScore(probe, it.second) },
               matchThreshold, identifyMargin)
}
