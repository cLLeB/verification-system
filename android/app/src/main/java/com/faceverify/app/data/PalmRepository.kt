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

    /** Enrol a palm anchor for [userId]. A person has up to two palms; one identity
     *  may enrol both (present either to verify). Mirrors the server's palm/api.py:
     *  a capture matching an already-enrolled hand tops it up; one matching NEITHER
     *  hand needs confirmation ([hand] = "other"/"any") before it binds as hand two
     *  (otherwise a soft "different_hand" is returned); a third distinct hand is
     *  refused. The cross-user duplicate guard always runs. */
    suspend fun enroll(userId: String, emb: FloatArray, hand: String = "auto"): EnrollResult = mutex.withLock {
        val id = userId.trim()
        if (id.isEmpty()) return@withLock EnrollResult(false, "A name or ID is required.", "missing_user_id")
        val allowNewHand = hand == "other" || hand == "any" || hand == "second"

        // A palm already bound to a DIFFERENT identity can never be enrolled here.
        val dec = Matcher.decide(scoreAll(emb, snapshot().filter { it.first != id }),
            PalmConfig.MATCH_THRESHOLD, PalmConfig.IDENTIFY_MARGIN)
        if (dec.userId != null && dec.score >= PalmConfig.MATCH_THRESHOLD) {
            return@withLock EnrollResult(false, "This palm is already enrolled as '${dec.userId}'.", "duplicate")
        }

        val cap = PalmConfig.SAMPLES_PER_USER * PalmConfig.MAX_HANDS_PER_USER
        val existing = index[id]
        // First-ever capture for this name -> hand 1, sample 1.
        if (existing == null || existing.isEmpty()) {
            dao.insertPerson(Person(id))
            storeAnchor(id, emb, cap)
            return@withLock EnrollResult(true, "Enrolled hand 1 for '$id' (1 of ${PalmConfig.SAMPLES_PER_USER}).",
                "enrolled", samples = 1, hand = 1)
        }

        val probe = probeFor(id, emb, HashMap())
        val hands = PalmClusters.group(existing, PalmConfig.MATCH_THRESHOLD)
        val matched = PalmClusters.matchedHand(probe, existing, PalmConfig.MATCH_THRESHOLD)

        // Matches an already-enrolled hand -> top it up (no confirmation needed).
        if (matched >= 0) {
            val inHand = hands[matched].size
            if (inHand >= PalmConfig.SAMPLES_PER_USER) {
                return@withLock EnrollResult(true, "Hand ${matched + 1} for '$id' is already complete.",
                    "enrolled", samples = inHand, hand = matched + 1)
            }
            storeAnchor(id, emb, cap)
            return@withLock EnrollResult(true, "Enrolled hand ${matched + 1} for '$id' (${inHand + 1} of ${PalmConfig.SAMPLES_PER_USER}).",
                "enrolled", samples = inHand + 1, hand = matched + 1)
        }

        // Matches no enrolled hand -> a DIFFERENT hand.
        if (hands.size >= PalmConfig.MAX_HANDS_PER_USER) {
            return@withLock EnrollResult(false, "'$id' already has both hands enrolled — no more palms can be added.", "hands_full")
        }
        if (!allowNewHand) {
            return@withLock EnrollResult(false,
                "This looks like a different hand than the one enrolled for '$id'. Add it as their other hand?",
                "different_hand", hand = hands.size)
        }
        storeAnchor(id, emb, cap)
        EnrollResult(true, "Started this person's other hand for '$id' (1 of ${PalmConfig.SAMPLES_PER_USER}).",
            "enrolled", samples = 1, hand = hands.size + 1)
    }

    /** Persist one anchor (projected into the user's domain if protected), keeping at
     *  most [cap] anchors (oldest evicted) so two enrolled hands both survive. */
    private suspend fun storeAnchor(id: String, emb: FloatArray, cap: Int) {
        val stored = userSeeds[id]?.let { Protect.project(it, emb) } ?: emb
        dao.insertEmbedding(Embedding(ownerId = id, kind = "anchor",
            blob = Crypto.encrypt(Crypto.floatsToBytes(stored)), source = "live"))
        index.getOrPut(id) { mutableListOf() }.add(stored)
        val anchors = dao.anchorIds(id)
        if (anchors.size > cap) {
            val drop = anchors.size - cap
            for (i in 0 until drop) dao.deleteEmbedding(anchors[i])
            val list = index[id]!!
            repeat(drop) { if (list.isNotEmpty()) list.removeAt(0) }
        }
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
