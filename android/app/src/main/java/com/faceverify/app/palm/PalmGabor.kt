package com.faceverify.app.palm

import android.graphics.Bitmap
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.sqrt

/** Built-in palm-print encoder — a Gabor texture descriptor, NO trained model.
 *  Mirrors the server's palm/classical.py so on-device palm works out of the box:
 *  bandpass the palm ROI at several orientations/scales, pool the responses over a
 *  spatial grid, L2-normalise. Deterministic (same ROI -> same vector). A bundled
 *  CCNet ONNX (PalmEmbedder) is an optional accuracy upgrade, not a requirement. */
object PalmGabor {
    private const val NORM = 64            // ROI resized to NORM×NORM before filtering
    private const val ORIENTS = 8
    private const val KSIZE = 13
    private val LAMBDAS = floatArrayOf(5f, 9f)
    private const val SIGMA = 3f
    private const val GAMMA = 0.5f
    private const val GRID = 8

    const val EMBED_DIM = GRID * GRID * ORIENTS * 2   // cells × orientations × scales

    private data class Kernel(val w: Int, val data: FloatArray)

    private val bank: List<Kernel> = buildBank()

    private fun buildBank(): List<Kernel> {
        val out = ArrayList<Kernel>(ORIENTS * LAMBDAS.size)
        val half = KSIZE / 2
        for (lambda in LAMBDAS) {
            for (o in 0 until ORIENTS) {
                val theta = PI * o / ORIENTS
                val data = FloatArray(KSIZE * KSIZE)
                var sum = 0f
                for (y in -half..half) for (x in -half..half) {
                    val xr = x * cos(theta) + y * Math.sin(theta)
                    val yr = -x * Math.sin(theta) + y * cos(theta)
                    val env = exp(-(xr * xr + GAMMA * GAMMA * yr * yr) / (2.0 * SIGMA * SIGMA))
                    val v = (env * cos(2.0 * PI * xr / lambda)).toFloat()
                    data[(y + half) * KSIZE + (x + half)] = v
                    sum += v
                }
                val mean = sum / data.size                 // zero-DC
                for (i in data.indices) data[i] -= mean
                out.add(Kernel(KSIZE, data))
            }
        }
        return out
    }

    fun encode(roi: Bitmap): FloatArray {
        val gray = toGrayNorm(roi)                         // NORM×NORM luminance [0,1]
        val feats = FloatArray(EMBED_DIM)
        val cell = NORM / GRID
        var fi = 0
        for (k in bank) {
            val resp = convAbs(gray, NORM, NORM, k)
            for (gy in 0 until GRID) {
                for (gx in 0 until GRID) {
                    var s = 0f
                    val y0 = gy * cell; val x0 = gx * cell
                    for (yy in 0 until cell) for (xx in 0 until cell) s += resp[(y0 + yy) * NORM + (x0 + xx)]
                    feats[fi++] = s / (cell * cell)
                }
            }
        }
        var n = 0f
        for (v in feats) n += v * v
        n = sqrt(n).coerceAtLeast(1e-10f)
        for (i in feats.indices) feats[i] /= n
        return feats
    }

    private fun toGrayNorm(roi: Bitmap): FloatArray {
        val scaled = Bitmap.createScaledBitmap(roi, NORM, NORM, true)
        val px = IntArray(NORM * NORM)
        scaled.getPixels(px, 0, NORM, 0, 0, NORM, NORM)
        val g = FloatArray(NORM * NORM)
        for (i in px.indices) {
            val p = px[i]
            g[i] = (0.299f * ((p shr 16) and 0xFF) + 0.587f * ((p shr 8) and 0xFF) + 0.114f * (p and 0xFF)) / 255f
        }
        return g
    }

    private fun convAbs(img: FloatArray, w: Int, h: Int, k: Kernel): FloatArray {
        val out = FloatArray(w * h)
        val half = k.w / 2
        for (y in 0 until h) {
            for (x in 0 until w) {
                var acc = 0f
                for (ky in -half..half) {
                    val yy = (y + ky).coerceIn(0, h - 1)
                    for (kx in -half..half) {
                        val xx = (x + kx).coerceIn(0, w - 1)
                        acc += img[yy * w + xx] * k.data[(ky + half) * k.w + (kx + half)]
                    }
                }
                out[y * w + x] = if (acc < 0) -acc else acc
            }
        }
        return out
    }
}
