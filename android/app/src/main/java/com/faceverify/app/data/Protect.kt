package com.faceverify.app.data

import java.security.MessageDigest
import kotlin.math.sqrt

/** Cancelable template protection - port of the server's `biometric/core/protect.py`.
 *
 *  Synced/bundled templates arrive PROTECTED: projected into a revocable domain by
 *  three rounds of {seeded +/-1 sign flips -> orthonormal fast Walsh–Hadamard
 *  transform}. To match, the live embedding is projected with the SAME domain seed
 *  (shipped with the dataset); cosine similarity inside the domain is unchanged.
 *  The construction is deterministic from the 32-byte seed via a SHA-256 counter
 *  stream, so this port is bit-compatible with the server (see the golden-vector
 *  unit test). Inputs whose length is not a power of two are zero-padded. */
object Protect {

    const val SCHEME = "hd3-v1"
    private const val ROUNDS = 3

    /** Project [emb] into the domain of [seed]. Returns a new array (padded length). */
    fun project(seed: ByteArray, emb: FloatArray): FloatArray {
        val n = nextPow2(emb.size)
        val x = FloatArray(n)
        System.arraycopy(emb, 0, x, 0, emb.size)
        for (round in 0 until ROUNDS) {
            val signs = signs(seed, n, round)
            for (i in 0 until n) x[i] *= signs[i]
            wht(x)
        }
        return x
    }

    private fun nextPow2(n: Int): Int {
        var p = 1
        while (p < n) p = p shl 1
        return p
    }

    /** Deterministic +/-1 stream: bits (MSB-first) of SHA-256(seed || round || counter_be32). */
    private fun signs(seed: ByteArray, dim: Int, round: Int): FloatArray {
        val out = FloatArray(dim)
        var have = 0
        var counter = 0
        val md = MessageDigest.getInstance("SHA-256")
        while (have < dim) {
            md.reset()
            md.update(seed)
            md.update(round.toByte())
            md.update(byteArrayOf(
                (counter ushr 24).toByte(), (counter ushr 16).toByte(),
                (counter ushr 8).toByte(), counter.toByte()))
            val block = md.digest()
            for (b in block) {
                for (bit in 7 downTo 0) {
                    if (have >= dim) break
                    out[have++] = if ((b.toInt() ushr bit) and 1 == 0) 1f else -1f
                }
                if (have >= dim) break
            }
            counter++
        }
        return out
    }

    /** In-place orthonormal fast Walsh–Hadamard transform (x 1/sqrt(n)). */
    private fun wht(x: FloatArray) {
        val n = x.size
        var h = 1
        while (h < n) {
            var i = 0
            while (i < n) {
                for (j in i until i + h) {
                    val a = x[j]
                    val b = x[j + h]
                    x[j] = a + b
                    x[j + h] = a - b
                }
                i += 2 * h
            }
            h *= 2
        }
        val s = (1.0 / sqrt(n.toDouble())).toFloat()
        for (k in x.indices) x[k] *= s
    }
}
