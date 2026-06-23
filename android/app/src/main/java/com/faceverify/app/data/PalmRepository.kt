package com.faceverify.app.data

import android.content.Context
import com.faceverify.app.PalmConfig
import com.faceverify.app.face.Decision
import com.faceverify.app.face.Matcher
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** On-device source of truth for PALM enrolment + matching. Mirrors FaceRepository
 *  exactly, but backed by the isolated PalmDb and palm-tuned thresholds, with its
 *  own in-memory index (palm embeddings per user) for fast 1:N. Palm and face share
 *  only the `user_id` namespace conceptually — their vectors live apart and are
 *  matched only against their own kind (`Matcher` is dimension-agnostic). */
class PalmRepository(context: Context) {
    private val dao = PalmDb.get(context).dao()
    private val index = LinkedHashMap<String, MutableList<FloatArray>>()
    private val mutex = Mutex()

    suspend fun load() = mutex.withLock {
        index.clear()
        for (e in dao.allEmbeddings()) {
            val vec = Crypto.bytesToFloats(Crypto.decrypt(e.blob))
            index.getOrPut(e.ownerId) { mutableListOf() }.add(vec)
        }
        for (p in dao.persons()) index.getOrPut(p.userId) { mutableListOf() }
    }

    suspend fun listUsers(): List<String> = mutex.withLock { index.keys.sorted() }
    suspend fun count(): Int = mutex.withLock { index.size }

    private fun snapshot(): List<Pair<String, List<FloatArray>>> =
        index.entries.filter { it.value.isNotEmpty() }.map { it.key to it.value.toList() }

    /** Enrol a palm anchor for [userId], with the same duplicate + self-consistency
     *  guards as the face repository (palm-tuned thresholds). */
    suspend fun enroll(userId: String, emb: FloatArray): EnrollResult = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty()) return@withLock EnrollResult(false, "A name or ID is required.", "missing_user_id")

        val dec = Matcher.identify(emb, snapshot().filter { it.first != id },
            PalmConfig.MATCH_THRESHOLD, PalmConfig.IDENTIFY_MARGIN)
        if (dec.userId != null && dec.score >= PalmConfig.MATCH_THRESHOLD) {
            return@withLock EnrollResult(false, "This palm is already enrolled as '${dec.userId}'.", "duplicate")
        }
        val existing = index[id]
        if (existing != null && existing.isNotEmpty()) {
            if (Matcher.bestScore(emb, existing) < PalmConfig.MATCH_THRESHOLD) {
                return@withLock EnrollResult(false, "This doesn't match the earlier palm — use the SAME hand.", "inconsistent")
            }
        } else {
            dao.insertPerson(Person(id))
        }
        dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor", blob = Crypto.encrypt(Crypto.floatsToBytes(emb)), source = "live"))
        index.getOrPut(id) { mutableListOf() }.add(emb)

        val anchors = dao.anchorIds(id)
        if (anchors.size > PalmConfig.SAMPLES_PER_USER) {
            val drop = anchors.size - PalmConfig.SAMPLES_PER_USER
            for (i in 0 until drop) dao.deleteEmbedding(anchors[i])
            val list = index[id]!!
            repeat(drop) { if (list.isNotEmpty()) list.removeAt(0) }
        }
        EnrollResult(true, "Enrolled palm for '$id'.", "enrolled", samples = (index[id]?.size ?: 0))
    }

    suspend fun identify(emb: FloatArray): Decision = mutex.withLock {
        Matcher.identify(emb, snapshot(), PalmConfig.MATCH_THRESHOLD, PalmConfig.IDENTIFY_MARGIN)
    }

    suspend fun verify(userId: String, emb: FloatArray): Decision = mutex.withLock {
        val list = index[userId.trim()] ?: return@withLock Decision(false, null, -1f, 0f)
        Matcher.verify(emb, list, PalmConfig.MATCH_THRESHOLD)
    }

    suspend fun maybeAdapt(decision: Decision, emb: FloatArray, claimedId: String?): Boolean = mutex.withLock {
        if (!decision.granted) return@withLock false
        val uid = decision.userId ?: claimedId ?: return@withLock false
        if (decision.score < PalmConfig.ADAPTIVE_UPDATE_THRESHOLD) return@withLock false
        if (claimedId.isNullOrBlank() && decision.margin < PalmConfig.ADAPTIVE_MARGIN) return@withLock false
        val list = index[uid] ?: return@withLock false
        if (list.isNotEmpty() && Matcher.bestScore(emb, list) >= PalmConfig.ADAPTIVE_NOVELTY) return@withLock false

        dao.insertEmbedding(Embedding(ownerId = uid, kind = "adaptive", blob = Crypto.encrypt(Crypto.floatsToBytes(emb))))
        list.add(emb)
        val total = list.size
        if (total > PalmConfig.ADAPTIVE_MAX_SAMPLES) {
            val adaptive = dao.adaptiveIds(uid)
            val drop = total - PalmConfig.ADAPTIVE_MAX_SAMPLES
            for (i in 0 until minOf(drop, adaptive.size)) dao.deleteEmbedding(adaptive[i])
            val anchorCount = dao.anchorIds(uid).size
            repeat(minOf(drop, adaptive.size)) { if (list.size > anchorCount) list.removeAt(anchorCount) }
        }
        true
    }

    suspend fun delete(userId: String): Boolean = mutex.withLock {
        val id = userId.trim()
        if (!index.containsKey(id)) return@withLock false
        dao.deletePerson(id)
        index.remove(id)
        true
    }
}
