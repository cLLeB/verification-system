package com.faceverify.app.face

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import com.faceverify.app.Config
import java.nio.FloatBuffer
import kotlin.math.sqrt

/** ArcFace embedding on-device via ONNX Runtime Mobile. Input: an aligned 112x112
 *  face. Output: a 512-d L2-normalised embedding. Matches insightface preprocessing
 *  (RGB, (x-127.5)/127.5, NCHW). */
class Embedder private constructor(
    private val env: OrtEnvironment,
    private val session: OrtSession,
) {
    private val inputName: String = session.inputNames.iterator().next()
    private val dim = Config.FACE_SIZE

    fun embed(aligned: Bitmap): FloatArray {
        val px = IntArray(dim * dim)
        aligned.getPixels(px, 0, dim, 0, 0, dim, dim)
        val buf = FloatBuffer.allocate(3 * dim * dim)
        val arr = buf.array()
        val plane = dim * dim
        for (y in 0 until dim) {
            for (x in 0 until dim) {
                val p = px[y * dim + x]
                val r = ((p shr 16) and 0xFF)
                val g = ((p shr 8) and 0xFF)
                val b = (p and 0xFF)
                val idx = y * dim + x
                arr[idx] = (r - 127.5f) / 127.5f             // R plane
                arr[plane + idx] = (g - 127.5f) / 127.5f     // G plane
                arr[2 * plane + idx] = (b - 127.5f) / 127.5f // B plane
            }
        }
        buf.rewind()
        val shape = longArrayOf(1, 3, dim.toLong(), dim.toLong())
        return OnnxTensor.createTensor(env, buf, shape).use { input ->
            session.run(mapOf(inputName to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val out = (result[0].value as Array<FloatArray>)[0]
                l2(out)
            }
        }
    }

    private fun l2(v: FloatArray): FloatArray {
        var s = 0f
        for (x in v) s += x * x
        val n = sqrt(s).coerceAtLeast(1e-10f)
        return FloatArray(v.size) { v[it] / n }
    }

    fun close() {
        session.close()
    }

    companion object {
        const val MODEL_ASSET = "w600k_r50.onnx"

        /** Load the model from assets. Throws if the asset is missing (see assets/README). */
        fun load(context: Context): Embedder {
            val bytes = context.assets.open(MODEL_ASSET).use { it.readBytes() }
            val env = OrtEnvironment.getEnvironment()
            val opts = OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(2)
                setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                // Use the phone's neural accelerator when available; harmless if not.
                try { addNnapi() } catch (_: Throwable) { }
            }
            val session = env.createSession(bytes, opts)
            return Embedder(env, session)
        }
    }
}
