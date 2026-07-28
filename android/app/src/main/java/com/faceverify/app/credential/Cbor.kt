package com.faceverify.app.credential

/** Minimal, strict CBOR (RFC 8949) decoder for FV1 credential payloads.
 *
 *  Supports exactly what the server's `cbor2` emits for credentials: unsigned/
 *  negative integers, byte strings, text strings, definite-length arrays and
 *  maps (string keys), and false/true/null. Anything else - indefinite lengths,
 *  tags, floats - is rejected (fail closed; a credential never contains them).
 *
 *  Decoded values map to: Long, ByteArray, String, List<Any?>, Map<String, Any?>,
 *  Boolean, null. */
object Cbor {

    class CborException(message: String) : Exception(message)

    fun decode(data: ByteArray): Any? {
        val r = Reader(data)
        val v = r.readItem(0)
        if (r.pos != data.size) throw CborException("trailing bytes after CBOR item")
        return v
    }

    private const val MAX_DEPTH = 16

    private class Reader(val buf: ByteArray) {
        var pos = 0

        fun readItem(depth: Int): Any? {
            if (depth > MAX_DEPTH) throw CborException("nesting too deep")
            val ib = byte()
            val major = ib ushr 5
            val info = ib and 0x1F
            return when (major) {
                0 -> length(info)                                   // uint
                1 -> -1L - length(info)                             // negint
                2 -> bytes(intLen(length(info)))                    // bstr
                3 -> String(bytes(intLen(length(info))), Charsets.UTF_8)  // tstr
                4 -> {                                              // array
                    val n = intLen(length(info))
                    List(n) { readItem(depth + 1) }
                }
                5 -> {                                              // map (string keys)
                    val n = intLen(length(info))
                    val out = LinkedHashMap<String, Any?>(n)
                    repeat(n) {
                        val k = readItem(depth + 1) as? String
                            ?: throw CborException("map keys must be strings")
                        if (out.containsKey(k)) throw CborException("duplicate map key '$k'")
                        out[k] = readItem(depth + 1)
                    }
                    out
                }
                7 -> when (info) {                                  // simple values only
                    20 -> false
                    21 -> true
                    22 -> null
                    else -> throw CborException("unsupported simple/float value $info")
                }
                else -> throw CborException("unsupported CBOR major type $major")
            }
        }

        private fun length(info: Int): Long = when {
            info < 24 -> info.toLong()
            info == 24 -> byte().toLong()
            info == 25 -> (byte().toLong() shl 8) or byte().toLong()
            info == 26 -> (0..3).fold(0L) { acc, _ -> (acc shl 8) or byte().toLong() }
            info == 27 -> (0..7).fold(0L) { acc, _ -> (acc shl 8) or byte().toLong() }
            else -> throw CborException("indefinite/reserved length not supported")
        }

        private fun intLen(n: Long): Int {
            if (n < 0 || n > buf.size - pos) throw CborException("length out of range")
            return n.toInt()
        }

        private fun byte(): Int {
            if (pos >= buf.size) throw CborException("truncated CBOR")
            return buf[pos++].toInt() and 0xFF
        }

        private fun bytes(n: Int): ByteArray {
            if (pos + n > buf.size) throw CborException("truncated CBOR")
            return buf.copyOfRange(pos, pos + n).also { pos += n }
        }
    }
}
