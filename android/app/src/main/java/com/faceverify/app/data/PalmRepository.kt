package com.faceverify.app.data

import android.content.Context
import android.util.Base64
import com.faceverify.app.PalmConfig
import com.faceverify.app.face.Decision
import com.faceverify.app.face.Matcher
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** On-device source of truth for PALM enrolment + matching. Mirrors FaceRepository
 *  exactly, but backed by the isolated PalmDb and palm-tuned thresholds, with its
 *  own in-memory index (palm embeddings per user) for fast 1:N. Palm and face share
 *  only the `user_id` namespace conceptually — their vectors live apart and are
 *  matched only against their own kind (`Matcher` is dimension-agnostic).
 *
 *  Protected templates: bundled/synced palm rows arrive projected into a revocable
 *  domain with a seed (see Protect.kt); live probes for those users are projected
 *  with the same seed before matching. */
class PalmRepository(context: Context) {
    private val dao = PalmDb.get(context).dao()
    private val index = LinkedHashMap<String, MutableList<FloatArray>>()
    private val userSeeds = HashMap<String, ByteArray>()
    private val mutex = Mutex()

    suspend fun load() = mutex.withLock {
        index.clear()
        userSeeds.clear()
        for (e in dao.allEmbeddings()) {
            val vec = Crypto.bytesToFloats(Crypto.decrypt(e.blob))
            index.getOrPut(e.ownerId) { mutableListOf() }.add(vec)
        }
        for (p in dao.persons()) {
            index.getOrPut(p.userId) { mutableListOf() }
            p.seedBlob?.let { userSeeds[p.userId] = Crypto.decrypt(it) }
        }
    }

    suspend fun listUsers(): List<String> = mutex.withLock { index.keys.sorted() }
    suspend fun count(): Int = mutex.withLock { index.size }

    private fun snapshot(): List<Pair<String, List<FloatArray>>> =
        index.entries.filter { it.value.isNotEmpty() }.map { it.key to it.value.toList() }

    private fun probeFor(uid: String, raw: FloatArray,
                         cache: MutableMap<String, FloatArray>): FloatArray {
        val seed = userSeeds[uid] ?: return raw
        val key = Base64.encodeToString(seed, Base64.NO_WRAP)
        return cache.getOrPut(key) { Protect.project(seed, raw) }
    }

    private fun scoreAll(emb: FloatArray,
                         people: List<Pair<String, List<FloatArray>>>): List<Pair<String, Float>> {
        val cache = HashMap<String, FloatArray>()
        return people.map { (uid, embs) -> uid to Matcher.bestScore(probeFor(uid, emb, cache), embs) }
    }

    /** Enrol a palm anchor for [userId], with the same duplicate + self-consistency
     *  guards as the face repository (palm-tuned thresholds). */
    suspend fun enroll(userId: String, emb: FloatArray): EnrollResult = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty()) return@withLock EnrollResult(false, "A name or ID is required.", "missing_user_id")

        val dec = Matcher.decide(scoreAll(emb, snapshot().filter { it.first != id }),
            PalmConfig.MATCH_THRESHOLD, PalmConfig.IDENTIFY_MARGIN)
        if (dec.userId != null && dec.score >= PalmConfig.MATCH_THRESHOLD) {
            return@withLock EnrollResult(false, "This palm is already enrolled as '${dec.userId}'.", "duplicate")
        }
        val existing = index[id]
        if (existing != null && existing.isNotEmpty()) {
            if (Matcher.bestScore(probeFor(id, emb, HashMap()), existing) < PalmConfig.MATCH_THRESHOLD) {
                return@withLock EnrollResult(false, "This doesn't match the earlier palm — use the SAME hand.", "inconsistent")
            }
        } else {
            dao.insertPerson(Person(id))
        }
        val stored = userSeeds[id]?.let { Protect.project(it, emb) } ?: emb
        dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor", blob = Crypto.encrypt(Crypto.floatsToBytes(stored)), source = "live"))
        index.getOrPut(id) { mutableListOf() }.add(stored)

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
        Matcher.decide(scoreAll(emb, snapshot()),
            PalmConfig.MATCH_THRESHOLD, PalmConfig.IDENTIFY_MARGIN)
    }

    suspend fun verify(userId: String, emb: FloatArray): Decision = mutex.withLock {
        val id = userId.trim()
        val list = index[id] ?: return@withLock Decision(false, null, -1f, 0f)
        Matcher.verify(probeFor(id, emb, HashMap()), list, PalmConfig.MATCH_THRESHOLD)
    }

    suspend fun maybeAdapt(decision: Decision, emb: FloatArray, claimedId: String?): Boolean = mutex.withLock {
        if (!decision.granted) return@withLock false
        val uid = decision.userId ?: claimedId ?: return@withLock false
        if (decision.score < PalmConfig.ADAPTIVE_UPDATE_THRESHOLD) return@withLock false
        if (claimedId.isNullOrBlank() && decision.margin < PalmConfig.ADAPTIVE_MARGIN) return@withLock false
        val list = index[uid] ?: return@withLock false
        val stored = probeFor(uid, emb, HashMap())
        if (list.isNotEmpty() && Matcher.bestScore(stored, list) >= PalmConfig.ADAPTIVE_NOVELTY) return@withLock false

        dao.insertEmbedding(Embedding(ownerId = uid, kind = "adaptive", blob = Crypto.encrypt(Crypto.floatsToBytes(stored))))
        list.add(stored)
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
        userSeeds.remove(id)
        true
    }

    /** Upsert a user's palm embeddings from an offline provisioning bundle (replaces
     *  any existing set), tagged with provenance. Mirrors FaceRepository.replaceUser.
     *  The bundle is admin-signed provisioning data, so it bypasses the live guards.
     *  [seed] is the protection-domain seed for PROTECTED rows (null = raw). */
    suspend fun replaceUser(userId: String, embs: List<FloatArray>,
                            source: String = "bundle", seed: ByteArray? = null) = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty() || embs.isEmpty()) return@withLock
        if (index.containsKey(id)) dao.deletePerson(id)
        dao.insertPerson(Person(id, seedBlob = seed?.let { Crypto.encrypt(it) }))
        if (seed != null) userSeeds[id] = seed else userSeeds.remove(id)
        val list = mutableListOf<FloatArray>()
        for (e in embs.take(PalmConfig.ADAPTIVE_MAX_SAMPLES)) {
            dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor",
                blob = Crypto.encrypt(Crypto.floatsToBytes(e)), source = source))
            list.add(e)
        }
        index[id] = list
    }
}
