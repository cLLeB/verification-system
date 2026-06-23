package com.faceverify.app.data

import android.content.Context
import com.faceverify.app.Config
import com.faceverify.app.face.Decision
import com.faceverify.app.face.Matcher
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class EnrollResult(
    val success: Boolean,
    val message: String,
    val code: String,
    val samples: Int = 0,
)

/** Single on-device source of truth for enrolment + matching. Keeps an in-memory
 *  index (decrypted embeddings per user) for fast 1:N, and persists encrypted to Room.
 *  Mirrors the server's api.py / storage.py behaviour (anchors + adaptive, anti-drift). */
class FaceRepository(context: Context) {
    private val dao = FaceDb.get(context).dao()
    private val index = LinkedHashMap<String, MutableList<FloatArray>>()
    private val mutex = Mutex()

    /** Load all embeddings into the in-memory index. Call once at startup. */
    suspend fun load() = mutex.withLock {
        index.clear()
        val all = dao.allEmbeddings()
        for (e in all) {
            val vec = Crypto.bytesToFloats(Crypto.decrypt(e.blob))
            index.getOrPut(e.ownerId) { mutableListOf() }.add(vec)
        }
        // make sure people with no embeddings still appear
        for (p in dao.persons()) index.getOrPut(p.userId) { mutableListOf() }
    }

    suspend fun listUsers(): List<String> = mutex.withLock { index.keys.sorted() }
    suspend fun count(): Int = mutex.withLock { index.size }

    private fun snapshot(): List<Pair<String, List<FloatArray>>> =
        index.entries.filter { it.value.isNotEmpty() }.map { it.key to it.value.toList() }

    /** Enrol an anchor capture for [userId]. Guards against enrolling the same face
     *  under a different name, and against inconsistent captures for the same name. */
    suspend fun enroll(userId: String, emb: FloatArray): EnrollResult = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty()) return@withLock EnrollResult(false, "A name or ID is required.", "missing_user_id")

        // duplicate-person guard
        val dec = Matcher.identify(emb, snapshot().filter { it.first != id })
        if (dec.userId != null && dec.score >= Config.MATCH_THRESHOLD) {
            return@withLock EnrollResult(false, "This face is already enrolled as '${dec.userId}'.", "duplicate")
        }
        // self-consistency for an existing person
        val existing = index[id]
        if (existing != null && existing.isNotEmpty()) {
            if (Matcher.bestScore(emb, existing) < Config.MATCH_THRESHOLD) {
                return@withLock EnrollResult(false, "This doesn't match the earlier capture — use the SAME person.", "inconsistent")
            }
        } else {
            dao.insertPerson(Person(id))
        }
        dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor", blob = Crypto.encrypt(Crypto.floatsToBytes(emb))))
        index.getOrPut(id) { mutableListOf() }.add(emb)

        // keep at most SAMPLES_PER_USER anchors (drop oldest)
        val anchors = dao.anchorIds(id)
        if (anchors.size > Config.SAMPLES_PER_USER) {
            val drop = anchors.size - Config.SAMPLES_PER_USER
            for (i in 0 until drop) dao.deleteEmbedding(anchors[i])
            // index holds anchors first (load order) — drop the oldest from the front
            val list = index[id]!!
            repeat(drop) { if (list.isNotEmpty()) list.removeAt(0) }
        }
        EnrollResult(true, "Enrolled '$id'.", "enrolled", samples = (index[id]?.size ?: 0))
    }

    suspend fun identify(emb: FloatArray): Decision = mutex.withLock { Matcher.identify(emb, snapshot()) }

    suspend fun verify(userId: String, emb: FloatArray): Decision = mutex.withLock {
        val list = index[userId.trim()] ?: return@withLock Decision(false, null, -1f, 0f)
        Matcher.verify(emb, list)
    }

    /** Fold a confident live capture into the matched user's rolling set (anti-drift:
     *  anchors are never touched; near-duplicates skipped; total capped). */
    suspend fun maybeAdapt(decision: Decision, emb: FloatArray, claimedId: String?): Boolean = mutex.withLock {
        if (!decision.granted) return@withLock false
        val uid = decision.userId ?: claimedId ?: return@withLock false
        if (decision.score < Config.ADAPTIVE_UPDATE_THRESHOLD) return@withLock false
        if (claimedId.isNullOrBlank() && decision.margin < Config.ADAPTIVE_MARGIN) return@withLock false
        val list = index[uid] ?: return@withLock false
        if (list.isNotEmpty() && Matcher.bestScore(emb, list) >= Config.ADAPTIVE_NOVELTY) return@withLock false

        dao.insertEmbedding(Embedding(ownerId = uid, kind = "adaptive", blob = Crypto.encrypt(Crypto.floatsToBytes(emb))))
        list.add(emb)
        // cap total embeddings; evict oldest ADAPTIVE first (anchors stay)
        val total = list.size
        if (total > Config.ADAPTIVE_MAX_SAMPLES) {
            val adaptive = dao.adaptiveIds(uid)
            val drop = total - Config.ADAPTIVE_MAX_SAMPLES
            for (i in 0 until minOf(drop, adaptive.size)) dao.deleteEmbedding(adaptive[i])
            // reflect in index: remove the matching number of oldest adaptive (after anchors)
            val anchorCount = dao.anchorIds(uid).size
            repeat(minOf(drop, adaptive.size)) {
                if (list.size > anchorCount) list.removeAt(anchorCount)
            }
        }
        true
    }

    suspend fun delete(userId: String): Boolean = mutex.withLock {
        val id = userId.trim()
        if (!index.containsKey(id)) return@withLock false
        dao.deletePerson(id)               // cascades to embeddings
        index.remove(id)
        true
    }
}
