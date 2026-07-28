package com.faceverify.app.credential

import com.faceverify.app.data.Protect
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlin.math.sqrt

/** Offline verification of FV1 credentials - port of the server's
 *  `biometric/core/credential.py` pipeline:
 *
 *      scan -> base45 -> CBOR [payload bytes, sig] -> Ed25519 over the exact
 *      received payload bytes -> schema/expiry -> live capture -> project the
 *      live embedding with the credential's PUBLIC per-cid seed -> cosine match.
 *
 *  Every failure carries one of the server's typed codes (spec 6.6) so all
 *  surfaces show the same plain-language screens. */
object CredentialVerifier {

    const val VERSION = 1L
    private const val PREFIX = "FV1:"
    private const val CID_LEN = 16
    private val SEED_LABEL = "faceverify-cred-v1:".toByteArray(Charsets.US_ASCII)

    class CredentialException(val code: String, message: String) : Exception(message)

    data class Template(val modality: String, val vector: FloatArray)
    data class Payload(
        val cid: ByteArray,
        val issuer: String,
        val kid: String,
        val subject: String,
        val name: String?,
        val attrs: Map<String, Any?>?,
        val modalities: List<String>,
        val templates: List<Template>,
        val issuedAt: Long,
        val expires: Long,
    )

    /** Decode + signature + validity. [resolveKey] returns the issuer's 32-byte
     *  public key for (iss, kid), or null for an unknown/untrusted issuer. */
    fun verify(text: String, resolveKey: (iss: String, kid: String) -> ByteArray?,
               nowSec: Long = System.currentTimeMillis() / 1000): Payload {
        val trimmed = text.trim()
        if (!trimmed.startsWith(PREFIX)) {
            throw CredentialException("malformed_credential", "Not a FaceVerify credential.")
        }
        val outer = try {
            Cbor.decode(Base45.decode(trimmed.substring(PREFIX.length)))
        } catch (e: Exception) {
            throw CredentialException("malformed_credential", "Undecodable credential.")
        }
        val list = outer as? List<*> ?: throw malformed()
        val payloadBytes = list.getOrNull(0) as? ByteArray ?: throw malformed()
        val sig = list.getOrNull(1) as? ByteArray ?: throw malformed()
        if (list.size != 2 || sig.size != 64) throw malformed()

        val payload = parse(payloadBytes)
        val pk = resolveKey(payload.issuer, payload.kid)
            ?: throw CredentialException("unknown_issuer",
                "Issuer '${payload.issuer}' is not trusted here.")
        if (!ed25519Verify(pk, payloadBytes, sig)) {
            throw CredentialException("bad_signature",
                "Signature check failed - tampered or forged.")
        }
        if (nowSec > payload.expires) {
            throw CredentialException("credential_expired", "This credential has expired.")
        }
        if (nowSec + 300 < payload.issuedAt) {
            throw CredentialException("credential_not_yet_valid",
                "This credential is not yet valid.")
        }
        return payload
    }

    /** The credential's protection-domain seed - a PUBLIC function of the cid
     *  (mirrors credential.py cred_seed). */
    fun credSeed(cid: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(SEED_LABEL + cid)

    /** Best cosine between a RAW live embedding (projected into the credential's
     *  domain) and the credential's templates for [modality]. */
    fun match(payload: Payload, modality: String, liveRawEmbedding: FloatArray): Float {
        val probe = Protect.project(credSeed(payload.cid), liveRawEmbedding)
        var best = -1f
        for (t in payload.templates) {
            if (t.modality != modality || t.vector.size != probe.size) continue
            var s = 0f
            for (i in probe.indices) s += probe[i] * t.vector[i]
            if (s > best) best = s
        }
        return best
    }

    // --- internals ---------------------------------------------------------
    private fun malformed() =
        CredentialException("malformed_credential", "Credential structure is invalid.")

    private fun ed25519Verify(pk: ByteArray, message: ByteArray, sig: ByteArray): Boolean =
        try {
            val signer = Ed25519Signer()
            signer.init(false, Ed25519PublicKeyParameters(pk, 0))
            signer.update(message, 0, message.size)
            signer.verifySignature(sig)
        } catch (e: Exception) {
            false
        }

    @Suppress("UNCHECKED_CAST")
    private fun parse(payloadBytes: ByteArray): Payload {
        val map = try {
            Cbor.decode(payloadBytes) as? Map<String, Any?> ?: throw malformed()
        } catch (e: Cbor.CborException) {
            throw malformed()
        }
        if ((map["v"] as? Long) != VERSION) {
            throw CredentialException("unsupported_version",
                "This credential needs an updated verifier.")
        }
        val cid = map["cid"] as? ByteArray ?: throw malformed()
        if (cid.size != CID_LEN) throw malformed()
        val iss = map["iss"] as? String ?: throw malformed()
        val kid = map["kid"] as? String ?: throw malformed()
        val sub = map["sub"] as? String ?: throw malformed()
        val mods = (map["mod"] as? List<*>)?.map { it as? String ?: throw malformed() }
            ?: throw malformed()
        val iat = map["iat"] as? Long ?: throw malformed()
        val exp = map["exp"] as? Long ?: throw malformed()
        if (iss.isEmpty() || sub.isEmpty() || mods.isEmpty() || exp <= iat) throw malformed()
        val tplBlobs = (map["tpl"] as? List<*>)?.map { it as? ByteArray ?: throw malformed() }
            ?: throw malformed()
        if (tplBlobs.isEmpty() || tplBlobs.size > 4) throw malformed()
        return Payload(
            cid = cid, issuer = iss, kid = kid, subject = sub,
            name = map["name"] as? String,
            attrs = map["attrs"] as? Map<String, Any?>,
            modalities = mods,
            templates = tplBlobs.map { parseEnvelope(it) },
            issuedAt = iat, expires = exp,
        )
    }

    /** BE1 envelope (biometric/core/envelope.py) -> dequantized unit vector. */
    @Suppress("UNCHECKED_CAST")
    private fun parseEnvelope(blob: ByteArray): Template {
        if (blob.size < 4 || String(blob, 0, 3, Charsets.US_ASCII) != "BE1") throw malformed()
        val env = try {
            Cbor.decode(blob.copyOfRange(3, blob.size)) as? Map<String, Any?>
                ?: throw malformed()
        } catch (e: Cbor.CborException) {
            throw malformed()
        }
        if (env["kind"] != "quantized-protected" || env["dtype"] != "i8") throw malformed()
        val mod = env["mod"] as? String ?: throw malformed()
        val data = env["data"] as? ByteArray ?: throw malformed()
        if (data.size < 5) throw malformed()
        val scale = ByteBuffer.wrap(data, 0, 4).order(ByteOrder.LITTLE_ENDIAN).float
        val v = FloatArray(data.size - 4) { data[4 + it] * (scale / 127f) }
        var n = 0f
        for (x in v) n += x * x
        if (n > 0f) {
            val inv = 1f / sqrt(n)
            for (i in v.indices) v[i] *= inv
        }
        return Template(mod, v)
    }
}
