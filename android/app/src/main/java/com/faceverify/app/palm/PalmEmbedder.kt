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
 *  ROI_SIZE×ROI_SIZE palm crop, **grayscale, [0,1], NCHW (1 channel)** — matching the
 *  CCNet `getFeatureCode` export (1×1×128×128). Output: an L2-normalised embedding.
 *  Mirrors the server's palm/engine.py preprocessing for the CCNet encoder.
 *
 *  The model is a CCNet-family palm-print encoder exported to ONNX (see
 *  assets/README_PALM_MODEL.md). When the asset is absent, [load] throws and palm
 *  falls back to the built-in PalmGabor encoder — the face app is unaffected. */
class PalmEmbedder private constructor(
    private val env: OrtEnvironment,
    private val session: OrtSession,
) {
    private val inputName: String = session.inputNames.iterator().next()
    private val dim = PalmConfig.ROI_SIZE

    fun embed(roi: Bitmap): FloatArray {
        val px = IntArray(dim * dim)
        roi.getPixels(px, 0, dim, 0, 0, dim, dim)
        val buf = FloatBuffer.allocate(dim * dim)        // 1 channel (grayscale)
        val arr = buf.array()
        for (i in px.indices) {
            val p = px[i]
            val lum = 0.299f * ((p shr 16) and 0xFF) + 0.587f * ((p shr 8) and 0xFF) + 0.114f * (p and 0xFF)
            arr[i] = lum / 255f                          // [0,1]
        }
        buf.rewind()
        val shape = longArrayOf(1, 1, dim.toLong(), dim.toLong())
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
