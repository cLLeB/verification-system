package com.faceverify.app.credential

/** Base45 (RFC 9285) decoder - the QR-alphanumeric-friendly encoding used by
 *  FV1 credentials (same family as EU DCC). Strict: any character outside the
 *  alphabet or an invalid final chunk throws [IllegalArgumentException]. */
object Base45 {
    private const val ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ \$%*+-./:"
    private val REVERSE = IntArray(128) { -1 }.also {
        ALPHABET.forEachIndexed { i, c -> it[c.code] = i }
    }

    fun decode(text: String): ByteArray {
        require(text.length % 3 != 1) { "invalid base45 length" }
        val vals = IntArray(text.length) {
            val c = text[it].code
            val v = if (c < 128) REVERSE[c] else -1
            require(v >= 0) { "invalid base45 character '${text[it]}'" }
            v
        }
        val out = ByteArray(text.length / 3 * 2 + if (text.length % 3 == 2) 1 else 0)
        var o = 0
        var i = 0
        while (i + 2 < vals.size) {
            val n = vals[i] + vals[i + 1] * 45 + vals[i + 2] * 45 * 45
            require(n <= 0xFFFF) { "invalid base45 triple" }
            out[o++] = (n ushr 8).toByte()
            out[o++] = n.toByte()
            i += 3
        }
        if (vals.size % 3 == 2) {
            val n = vals[vals.size - 2] + vals[vals.size - 1] * 45
            require(n <= 0xFF) { "invalid base45 pair" }
            out[o] = n.toByte()
        }
        return out
    }
}
