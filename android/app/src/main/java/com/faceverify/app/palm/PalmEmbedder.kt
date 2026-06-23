package com.faceverify.app.palm

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import com.faceverify.app.PalmConfig
import java.nio.FloatBuffer
import kotlin.math.sqrt

/** Palm-print embedding on-device via ONNX Runtime Mobile. Input: a normalised
 *  ROI_SIZE×ROI_SIZE palm crop (RGB, [0,1], NCHW). Output: an L2-normalised
 *  EMBED_DIM-d embedding. Mirrors the server's palm/engine.py preprocessing.
 *
 *  The model is a CCNet-family palm-print encoder exported to ONNX (see
 *  assets/README_PALM_MODEL.md). When the asset is absent, [load] throws and the
 *  palm modality is simply unavailable — the face app is unaffected. */
class PalmEmbedder private constructor(
    private val env: OrtEnvironment,
    private val session: OrtSession,
) {
    private val inputName: String = session.inputNames.iterator().next()
    private val dim = PalmConfig.ROI_SIZE

    fun embed(roi: Bitmap): FloatArray {
        val px = IntArray(dim * dim)
        roi.getPixels(px, 0, dim, 0, 0, dim, dim)
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
                arr[idx] = r / 255f                    // R plane, [0,1]
                arr[plane + idx] = g / 255f            // G plane
                arr[2 * plane + idx] = b / 255f        // B plane
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

    fun close() = session.close()

    companion object {
        const val MODEL_ASSET = "palm_ccnet.onnx"

        fun available(context: Context): Boolean = try {
            context.assets.open(MODEL_ASSET).use { true }
        } catch (_: Throwable) {
            false
        }

        /** Load the palm model from assets. Throws if the asset is missing. */
        fun load(context: Context): PalmEmbedder {
            val bytes = context.assets.open(MODEL_ASSET).use { it.readBytes() }
            val env = OrtEnvironment.getEnvironment()
            val opts = OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(2)
                setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                try { addNnapi() } catch (_: Throwable) { }
            }
            return PalmEmbedder(env, env.createSession(bytes, opts))
        }
    }
}
