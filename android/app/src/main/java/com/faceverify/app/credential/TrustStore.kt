package com.faceverify.app.credential

import android.content.Context
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.Base64

/** The verifier's trust data: every issuer's public keys + revocation lists,
 *  parsed from the server's signed `/v1/trust-store` response.
 *
 *  The signature covers the EXACT bytes in `payload_b64` (no JSON
 *  canonicalization anywhere). The root key is pinned on first use (TOFU) —
 *  a later refresh or file import signed by a different root is rejected.
 *  Verifiers bundle/import this once and refresh opportunistically; a stale
 *  copy still verifies credentials, it just lags on revocations. */
class TrustData private constructor(
    val generated: Long,
    private val keys: Map<String, ByteArray>,          // "iss/kid" -> 32-byte public key
    private val revocations: Map<String, JSONObject>,  // iss -> revocation object
) {
    val issuerCount: Int get() = revocations.size

    /** resolve (iss, kid) -> public key for CredentialVerifier.verify. */
    fun resolve(iss: String, kid: String): ByteArray? = keys["$iss/$kid"]

    /** Revocation check (spec 6.5): exact list, or Bloom filter — a Bloom hit
     *  means REVOKED (no false negatives; malformed data fails closed). */
    fun isRevoked(iss: String, cidHex: String): Boolean {
        val rev = revocations[iss] ?: return false
        rev.optJSONArray("exact")?.let { exact ->
            for (i in 0 until exact.length()) if (exact.optString(i) == cidHex) return true
            return false
        }
        val bloom = rev.optJSONObject("bloom") ?: return false
        val m = bloom.optInt("m", -1)
        val k = bloom.optInt("k", -1)
        val bits = try {
            Base64.getDecoder().decode(bloom.optString("bits"))
        } catch (e: Exception) {
            return true                                   // malformed: fail closed
        }
        if (m <= 0 || k <= 0 || bits.size < (m + 7) / 8) return true
        return bloomPositions(cidHex, m, k).all { pos ->
            bits[pos / 8].toInt() and (1 shl (pos % 8)) != 0
        }
    }

    companion object {
        /** Double-hash positions in 64-bit WRAPPED unsigned arithmetic — must
         *  mirror face_service/credentials.py exactly (it masks to 64 bits). */
        internal fun bloomPositions(cidHex: String, m: Int, k: Int): List<Int> {
            val cid = ByteArray(cidHex.length / 2) {
                cidHex.substring(it * 2, it * 2 + 2).toInt(16).toByte()
            }
            val d = MessageDigest.getInstance("SHA-256").digest(cid)
            var h1 = 0UL
            for (i in 0 until 8) h1 = (h1 shl 8) or (d[i].toULong() and 0xFFUL)
            var h2 = 0UL
            for (i in 8 until 16) h2 = (h2 shl 8) or (d[i].toULong() and 0xFFUL)
            h2 = h2 or 1UL
            return (0 until k).map { i ->
                ((h1 + i.toULong() * h2) % m.toULong()).toInt()
            }
        }

        /** Verify the root signature over the exact payload bytes, then parse.
         *  Throws [IllegalArgumentException] on any failure. */
        fun verifyAndParse(payloadB64: String, sigB64: String, rootKeyB64: String): TrustData {
            val payload = Base64.getDecoder().decode(payloadB64)
            val sig = Base64.getDecoder().decode(sigB64)
            val root = Base64.getDecoder().decode(rootKeyB64)
            require(root.size == 32 && sig.size == 64) { "malformed trust store" }
            val ok = try {
                val signer = Ed25519Signer()
                signer.init(false, Ed25519PublicKeyParameters(root, 0))
                signer.update(payload, 0, payload.size)
                signer.verifySignature(sig)
            } catch (e: Exception) {
                false
            }
            require(ok) { "trust store signature check failed" }

            val body = JSONObject(String(payload, Charsets.UTF_8))
            val keys = HashMap<String, ByteArray>()
            val revs = HashMap<String, JSONObject>()
            val tenants = body.optJSONArray("tenants") ?: throw IllegalArgumentException("no tenants")
            for (i in 0 until tenants.length()) {
                val t = tenants.getJSONObject(i)
                val iss = t.getString("tenant")
                val karr = t.optJSONArray("keys")
                if (karr != null) for (j in 0 until karr.length()) {
                    val k = karr.getJSONObject(j)
                    keys["$iss/${k.getString("kid")}"] =
                        Base64.getDecoder().decode(k.getString("key"))
                }
                t.optJSONObject("revocations")?.let { revs[iss] = it }
            }
            return TrustData(body.optLong("generated"), keys, revs)
        }
    }
}

/** Context-bound persistence + root-key pinning for [TrustData]. */
object TrustStoreManager {
    private const val FILE = "trust_store.json"
    private const val PREFS = "faceverify_trust"
    private const val PIN = "pinned_root"

    /** Verify a full `/v1/trust-store` response (fetched or imported as a file)
     *  against the pinned root — pinning it on first use — then persist it.
     *  Throws [IllegalArgumentException] on a bad signature or root mismatch. */
    fun save(ctx: Context, response: JSONObject): TrustData {
        val root = response.optString("root_key")
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val pinned = prefs.getString(PIN, null)
        if (pinned != null) {
            require(pinned == root) {
                "This trust store is signed by a DIFFERENT root than before — refusing it."
            }
        }
        val data = TrustData.verifyAndParse(
            response.optString("payload_b64"), response.optString("sig"), root)
        if (pinned == null) prefs.edit().putString(PIN, root).apply()
        File(ctx.filesDir, FILE).writeText(response.toString())
        return data
    }

    fun load(ctx: Context): TrustData? {
        val f = File(ctx.filesDir, FILE)
        if (!f.exists()) return null
        return try {
            val response = JSONObject(f.readText())
            TrustData.verifyAndParse(response.optString("payload_b64"),
                response.optString("sig"), response.optString("root_key"))
        } catch (e: Exception) {
            null                                          // corrupted store: treat as absent
        }
    }
}
