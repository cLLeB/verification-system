package com.faceverify.app.glance

import android.content.Context
import com.faceverify.app.Config
import com.faceverify.app.data.Protect
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64

/** The on-device 1:N "glance" index (trust platform Phase 3) — one int8,
 *  protection-domain vector per person, brute-force matched in memory
 *  (100k x 512 multiply-adds well under a second on a phone).
 *
 *  Parsed from the server's `faceverify-glance-index` payload
 *  (`GET /v1/sync/index`, or the encrypted `/v1/export/glance-index` file for
 *  air-gapped devices). The 1:N threshold arrives calibrated but is CLAMPED
 *  on-device to [GLANCE_MIN_THRESHOLD, +GLANCE_CLAMP_BAND] — a bad or hostile
 *  calibration can never loosen matching below the floor. */
class GlanceIndex private constructor(
    val users: List<String>,
    private val rows: ByteArray,           // count x dim int8
    private val scales: FloatArray,        // per-row dequantization scale
    val dim: Int,
    val seed: ByteArray?,                  // protection-domain seed (null = raw rows)
    val threshold: Float,
    val margin: Float,
    val generated: Long,
    val seq: Long,
) {
    val count: Int get() = users.size

    data class Hit(val userId: String, val score: Float)

    /** Project a RAW live embedding into the index's domain. */
    fun probeFor(rawEmbedding: FloatArray): FloatArray =
        seed?.let { Protect.project(it, rawEmbedding) } ?: rawEmbedding

    /** Brute-force top-[k] over every row: int8 dot x per-row scale. */
    fun search(probe: FloatArray, k: Int = 5): List<Hit> {
        if (count == 0 || probe.size < dim) return emptyList()
        val scores = FloatArray(count)
        for (r in 0 until count) {
            var s = 0f
            val off = r * dim
            for (i in 0 until dim) s += rows[off + i] * probe[i]
            scores[r] = s * (scales[r] / 127f)
        }
        val order = (0 until count).sortedByDescending { scores[it] }.take(k)
        return order.map { Hit(users[it], scores[it]) }
    }

    /** Margin-checked 1:N decision (mirrors face_service/glance.py decide). */
    fun decide(hits: List<Hit>): Hit? {
        if (hits.isEmpty()) return null
        val top = hits[0]
        val second = if (hits.size > 1) hits[1].score else -1f
        return if (top.score >= threshold && (hits.size == 1 || top.score - second >= margin))
            top else null
    }

    companion object {
        /** Parse a glance-index payload. Throws [IllegalArgumentException] on a
         *  wrong format/version or inconsistent sizes (fail closed). */
        fun parse(payload: JSONObject): GlanceIndex {
            require(payload.optString("format") == "faceverify-glance-index") {
                "not a glance index file"
            }
            require(payload.optInt("version") == 1) { "unsupported glance index version" }
            val dim = payload.getInt("dim")
            val count = payload.getInt("count")
            val usersArr = payload.getJSONArray("users")
            require(usersArr.length() == count) { "user list / count mismatch" }
            val users = List(count) { usersArr.getString(it) }
            val rows = Base64.getDecoder().decode(payload.getString("data"))
            val scaleBytes = Base64.getDecoder().decode(payload.getString("scales"))
            require(rows.size == count * dim && scaleBytes.size == count * 4) {
                "index data size mismatch"
            }
            val scales = FloatArray(count)
            ByteBuffer.wrap(scaleBytes).order(ByteOrder.LITTLE_ENDIAN)
                .asFloatBuffer().get(scales)
            val seed = payload.optJSONObject("protection")?.optString("seed")
                ?.takeIf { it.isNotEmpty() }
                ?.let { Base64.getDecoder().decode(it) }
            // hard device floor is per-modality (palm needs a higher 1:N floor than
            // face) so a bad/hostile server threshold can't loosen matching below it
            val floor = Config.glanceFloor(payload.optString("modality", "face"))
            val thr = payload.optDouble("threshold", floor.toDouble()).toFloat()
                .coerceIn(floor, floor + Config.GLANCE_CLAMP_BAND)
            val margin = payload.optDouble("margin", Config.GLANCE_MARGIN.toDouble())
                .toFloat().coerceAtLeast(Config.GLANCE_MARGIN)
            return GlanceIndex(users, rows, scales, dim, seed, thr, margin,
                payload.optLong("generated"), payload.optLong("seq"))
        }
    }
}

/** File persistence for the glance index (raw payload JSON in app storage). */
object GlanceIndexStore {
    private const val FILE = "glance_index.json"

    fun save(ctx: Context, payload: JSONObject): GlanceIndex {
        val idx = GlanceIndex.parse(payload)           // validate BEFORE persisting
        File(ctx.filesDir, FILE).writeText(payload.toString())
        return idx
    }

    fun load(ctx: Context): GlanceIndex? {
        val f = File(ctx.filesDir, FILE)
        if (!f.exists()) return null
        return try {
            GlanceIndex.parse(JSONObject(f.readText()))
        } catch (e: Exception) {
            null                                       // corrupted: treat as absent
        }
    }
}
