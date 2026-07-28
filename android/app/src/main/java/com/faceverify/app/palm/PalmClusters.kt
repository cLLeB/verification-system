package com.faceverify.app.palm

import com.faceverify.app.face.Matcher

/** Group a palm identity's stored anchors into distinct hands. Mirrors the server's
 *  palm/clusters.py. One identity may enrol both palms; left and right score like
 *  impostors against each other (well below MATCH_THRESHOLD) while repeat captures of
 *  the same hand score well above it, so anchors form tight, well-separated clusters -
 *  one per hand. Used so enrolment can count hands (cap at two), size each hand, and
 *  tell a genuine "other hand" from an accidental wrong-person capture. */
object PalmClusters {

    /** Greedily group anchor indices into hands: an anchor joins the first existing
     *  hand it matches (>= threshold to any member), else starts a new hand. */
    fun group(embeddings: List<FloatArray>, threshold: Float): List<MutableList<Int>> {
        val hands = mutableListOf<MutableList<Int>>()
        val members = mutableListOf<MutableList<FloatArray>>()
        for ((i, e) in embeddings.withIndex()) {
            var placed = false
            for (h in members.indices) {
                if (members[h].maxOf { Matcher.cosine(e, it) } >= threshold) {
                    hands[h].add(i); members[h].add(e); placed = true; break
                }
            }
            if (!placed) { hands.add(mutableListOf(i)); members.add(mutableListOf(e)) }
        }
        return hands
    }

    /** Index (into group(...)) of the hand this probe belongs to, or -1 for a new hand. */
    fun matchedHand(probe: FloatArray, embeddings: List<FloatArray>, threshold: Float): Int {
        val hands = group(embeddings, threshold)
        var bestH = -1
        var bestS = threshold
        for (h in hands.indices) {
            val s = hands[h].maxOf { Matcher.cosine(probe, embeddings[it]) }
            if (s >= bestS) { bestH = h; bestS = s }
        }
        return bestH
    }
}
