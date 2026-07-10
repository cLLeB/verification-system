package com.faceverify.app.data

import android.content.Context
import android.util.Base64
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
    val hand: Int = 0,          // palm: which hand this capture belongs to (1 or 2)
)

/** Single on-device source of truth for enrolment + matching. Keeps an in-memory
 *  index (decrypted embeddings per user) for fast 1:N, and persists encrypted to Room.
 *  Mirrors the server's api.py / storage.py behaviour (anchors + adaptive, anti-drift).
 *
 *  Protected templates: users pulled from sync or a bundle arrive PROTECTED (projected
 *  into a revocable domain — see Protect.kt) and carry a domain seed. Matching scores
 *  each user against the right probe: the raw live embedding for local enrolments, or
 *  the embedding projected with that user's seed for synced/bundled rows. */
class FaceRepository(context: Context) {
    private val dao = FaceDb.get(context).dao()
    private val index = LinkedHashMap<String, MutableList<FloatArray>>()
    private val userSeeds = HashMap<String, ByteArray>()      // uid -> domain seed (protected rows)
    private val mutex = Mutex()

    /** Load all embeddings into the in-memory index. Call once at startup. */
    suspend fun load() = mutex.withLock {
        index.clear()
        userSeeds.clear()
        val all = dao.allEmbeddings()
        for (e in all) {
            val vec = Crypto.bytesToFloats(Crypto.decrypt(e.blob))
            index.getOrPut(e.ownerId) { mutableListOf() }.add(vec)
        }
        // make sure people with no embeddings still appear; pick up domain seeds
        for (p in dao.persons()) {
            index.getOrPut(p.userId) { mutableListOf() }
            p.seedBlob?.let { userSeeds[p.userId] = Crypto.decrypt(it) }
        }
    }

    suspend fun listUsers(): List<String> = mutex.withLock { index.keys.sorted() }
    suspend fun count(): Int = mutex.withLock { index.size }

    private fun snapshot(): List<Pair<String, List<FloatArray>>> =
        index.entries.filter { it.value.isNotEmpty() }.map { it.key to it.value.toList() }

    /** The probe to use for [uid]: the raw embedding, or (for a protected user)
     *  the embedding projected into their domain. Projections are cached per seed
     *  within one call via [cache]. */
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

    /** Enrol an anchor capture for [userId]. Guards against enrolling the same face
     *  under a different name, and against inconsistent captures for the same name. */
    suspend fun enroll(userId: String, emb: FloatArray, source: String = "live"): EnrollResult = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty()) return@withLock EnrollResult(false, "A name or ID is required.", "missing_user_id")

        // duplicate-person guard (each candidate scored in their own domain)
        val dec = Matcher.decide(scoreAll(emb, snapshot().filter { it.first != id }))
        if (dec.userId != null && dec.score >= Config.MATCH_THRESHOLD) {
            return@withLock EnrollResult(false, "This face is already enrolled as '${dec.userId}'.", "duplicate")
        }
        // self-consistency for an existing person
        val existing = index[id]
        if (existing != null && existing.isNotEmpty()) {
            val probe = probeFor(id, emb, HashMap())
            if (Matcher.bestScore(probe, existing) < Config.MATCH_THRESHOLD) {
                return@withLock EnrollResult(false, "This doesn't match the earlier capture — use the SAME person.", "inconsistent")
            }
        } else {
            dao.insertPerson(Person(id))
        }
        // a protected (synced) user gaining a local anchor: store it in THEIR domain
        // so the row set stays single-domain
        val stored = userSeeds[id]?.let { Protect.project(it, emb) } ?: emb
        val src = if (source == "id") "id" else "live"
        dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor", blob = Crypto.encrypt(Crypto.floatsToBytes(stored)), source = src))
        index.getOrPut(id) { mutableListOf() }.add(stored)

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

    suspend fun identify(emb: FloatArray): Decision = mutex.withLock {
        Matcher.decide(scoreAll(emb, snapshot()))
    }

    suspend fun verify(userId: String, emb: FloatArray): Decision = mutex.withLock {
        val id = userId.trim()
        val list = index[id] ?: return@withLock Decision(false, null, -1f, 0f)
        Matcher.verify(probeFor(id, emb, HashMap()), list)
    }

    /** Fold a confident live capture into the matched user's rolling set (anti-drift:
     *  anchors are never touched; near-duplicates skipped; total capped). */
    suspend fun maybeAdapt(decision: Decision, emb: FloatArray, claimedId: String?): Boolean = mutex.withLock {
        if (!decision.granted) return@withLock false
        val uid = decision.userId ?: claimedId ?: return@withLock false
        if (decision.score < Config.ADAPTIVE_UPDATE_THRESHOLD) return@withLock false
        if (claimedId.isNullOrBlank() && decision.margin < Config.ADAPTIVE_MARGIN) return@withLock false
        val list = index[uid] ?: return@withLock false
        val stored = probeFor(uid, emb, HashMap())            // user's domain (raw if local)
        if (list.isNotEmpty() && Matcher.bestScore(stored, list) >= Config.ADAPTIVE_NOVELTY) return@withLock false

        dao.insertEmbedding(Embedding(ownerId = uid, kind = "adaptive", blob = Crypto.encrypt(Crypto.floatsToBytes(stored))))
        list.add(stored)
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
        userSeeds.remove(id)
        true
    }

    // --- hybrid sync support (only used by the hybrid build) -----------------

    /** On-device templates as (userId, embeddings) for pushing to the server.
     *  Only RAW local enrolments are pushed — protected mirrors came FROM the
     *  server (it already has them, and it expects raw vectors on push). */
    suspend fun exportTemplates(selected: Set<String>? = null): List<Pair<String, List<FloatArray>>> =
        mutex.withLock {
            snapshot().filter { (uid, _) ->
                uid !in userSeeds && (selected == null || uid in selected)
            }
        }

    /** Upsert a user's embeddings from a server pull (replaces any existing set),
     *  tagged with provenance. [seed] is the protection-domain seed for PROTECTED
     *  rows (null = raw). */
    suspend fun replaceUser(userId: String, embs: List<FloatArray>,
                            source: String = "synced", seed: ByteArray? = null) = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty() || embs.isEmpty()) return@withLock
        if (index.containsKey(id)) dao.deletePerson(id)   // cascade old embeddings
        dao.insertPerson(Person(id, seedBlob = seed?.let { Crypto.encrypt(it) }))
        if (seed != null) userSeeds[id] = seed else userSeeds.remove(id)
        val list = mutableListOf<FloatArray>()
        for (e in embs.take(Config.ADAPTIVE_MAX_SAMPLES)) {
            dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor",
                blob = Crypto.encrypt(Crypto.floatsToBytes(e)), source = source))
            list.add(e)
        }
        index[id] = list
    }

    /** Drop every user that came from sync (used when the server's protection
     *  domain changes — the mirror is re-pulled from scratch). */
    suspend fun deleteSynced(): Int = mutex.withLock {
        var removed = 0
        val synced = dao.allEmbeddings().filter { it.source == "synced" }.map { it.ownerId }.toSet()
        for (uid in synced) {
            dao.deletePerson(uid)
            index.remove(uid)
            userSeeds.remove(uid)
            removed++
        }
        removed
    }
}
