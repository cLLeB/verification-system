package com.faceverify.app.data

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/** Imports an OFFLINE provisioning bundle produced by the server
 *  (`face_service/bundle.py` / `POST /v1/export/bundle`): a passphrase-encrypted,
 *  integrity-protected file of biometric *templates* (embeddings, never images).
 *
 *  This is the ONLY way to bulk-load an air-gapped device: the admin exports the
 *  bundle on the server, moves the file across out-of-band (USB / MDM), and imports
 *  it here with the same passphrase. No network path to this device is opened.
 *
 *  Crypto mirrors the server exactly: PBKDF2WithHmacSHA256 (200k iters) derives a
 *  256-bit key from the passphrase + salt; AES-256-GCM (128-bit tag appended to the
 *  ciphertext) decrypts and authenticates. A wrong passphrase or any tampering fails
 *  the GCM tag and raises [BundleException]. */
object BundleImporter {

    private const val TAG_BITS = 128

    data class Person(
        val userId: String,
        val embeddings: List<FloatArray>,
        /** Protection-domain seed for this person's PROTECTED rows (null = raw). */
        val seed: ByteArray? = null,
    )
    data class Parsed(val tenant: String, val face: List<Person>, val palm: List<Person>)

    class BundleException(message: String) : Exception(message)

    /** Decrypt any server-packed file (`bundle.pack`) to its JSON payload —
     *  template bundles AND glance-index exports share this envelope. Throws
     *  [BundleException] on a bad passphrase, tampering, or malformed content. */
    fun decryptToJson(raw: ByteArray, passphrase: String): JSONObject {
        if (passphrase.trim().length < 8) throw BundleException("Passphrase is too short.")
        val outer = try {
            JSONObject(String(raw, StandardCharsets.UTF_8))
        } catch (e: Exception) {
            throw BundleException("Not a valid bundle file.")
        }
        if (outer.optString("format") != "faceverify-bundle") {
            throw BundleException("Unrecognised bundle format.")
        }
        val kdf = outer.optJSONObject("kdf") ?: throw BundleException("Malformed bundle (kdf).")
        val cipherObj = outer.optJSONObject("cipher") ?: throw BundleException("Malformed bundle (cipher).")
        val salt = b64(kdf.optString("salt"))
        val iterations = kdf.optInt("iterations", 200_000)
        val iv = b64(cipherObj.optString("iv"))
        val ct = b64(cipherObj.optString("ct"))
        if (salt.isEmpty() || iv.isEmpty() || ct.isEmpty()) throw BundleException("Malformed bundle.")

        val key = deriveKey(passphrase, salt, iterations)
        val plain = try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, iv))
            cipher.doFinal(ct)                       // ct = ciphertext || 16-byte GCM tag
        } catch (e: Exception) {
            throw BundleException("Wrong passphrase or corrupted bundle.")
        }
        return try {
            JSONObject(String(plain, StandardCharsets.UTF_8))
        } catch (e: Exception) {
            throw BundleException("Bundle payload is not valid JSON.")
        }
    }

    /** Decrypt + parse a TEMPLATE bundle's bytes. */
    fun parse(raw: ByteArray, passphrase: String): Parsed {
        val payload = decryptToJson(raw, passphrase)
        val mods = payload.optJSONObject("modalities") ?: JSONObject()
        // Protected bundles carry a per-modality domain seed (used to project live
        // probes for matching); individually reissued people override it per row.
        val prot = payload.optJSONObject("protection")
        return Parsed(
            tenant = payload.optString("tenant", ""),
            face = people(mods.optJSONArray("face"), seedOf(prot, "face")),
            palm = people(mods.optJSONArray("palm"), seedOf(prot, "palm")),
        )
    }

    private fun seedOf(prot: JSONObject?, modality: String): ByteArray? {
        val s = prot?.optJSONObject(modality)?.optString("seed") ?: return null
        return if (s.isEmpty()) null else b64(s)
    }

    /** Import a parsed bundle into the on-device stores (upsert per person). Returns
     *  (facePeople, palmPeople) actually imported. ``palmRepo`` may be null on a
     *  face-only build, in which case palm templates are skipped. */
    suspend fun apply(parsed: Parsed, faceRepo: FaceRepository, palmRepo: PalmRepository?): Pair<Int, Int> {
        var f = 0
        for (p in parsed.face) {
            if (p.userId.isNotBlank() && p.embeddings.isNotEmpty()) {
                faceRepo.replaceUser(p.userId, p.embeddings, source = "bundle", seed = p.seed); f++
            }
        }
        var pl = 0
        if (palmRepo != null) {
            for (p in parsed.palm) {
                if (p.userId.isNotBlank() && p.embeddings.isNotEmpty()) {
                    palmRepo.replaceUser(p.userId, p.embeddings, source = "bundle", seed = p.seed); pl++
                }
            }
        }
        return f to pl
    }

    private fun deriveKey(passphrase: String, salt: ByteArray, iterations: Int): SecretKeySpec {
        val spec = PBEKeySpec(passphrase.toCharArray(), salt, iterations, 256)
        val skf = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
        return SecretKeySpec(skf.generateSecret(spec).encoded, "AES")
    }

    private fun b64(s: String?): ByteArray =
        if (s.isNullOrEmpty()) ByteArray(0) else Base64.decode(s, Base64.NO_WRAP)

    private fun people(arr: JSONArray?, modalitySeed: ByteArray?): List<Person> {
        if (arr == null) return emptyList()
        val out = ArrayList<Person>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val uid = o.optString("user_id", "")
            val embArr = o.optJSONArray("embeddings") ?: continue
            val embs = ArrayList<FloatArray>(embArr.length())
            for (j in 0 until embArr.length()) {
                val vecArr = embArr.optJSONArray(j) ?: continue
                val vec = FloatArray(vecArr.length()) { k -> vecArr.optDouble(k, 0.0).toFloat() }
                if (vec.isNotEmpty()) embs.add(vec)
            }
            val rowSeed = o.optString("seed", "").takeIf { it.isNotEmpty() }?.let { b64(it) }
            if (uid.isNotBlank() && embs.isNotEmpty()) {
                out.add(Person(uid, embs, seed = rowSeed ?: modalitySeed))
            }
        }
        return out
    }
}
