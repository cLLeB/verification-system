package com.faceverify.app.face

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PointF
import com.faceverify.app.Config

/** Aligns a detected face to the canonical ArcFace 112x112 layout using the 5
 *  landmarks and a least-squares 2D similarity transform (the same "norm_crop"
 *  insightface uses — uniform scale + rotation + translation, no shear). */
object FaceAligner {

    // Canonical destination points for a 112x112 ArcFace crop (insightface standard).
    private val REF = arrayOf(
        floatArrayOf(38.2946f, 51.6963f),  // left eye
        floatArrayOf(73.5318f, 51.5014f),  // right eye
        floatArrayOf(56.0252f, 71.7366f),  // nose
        floatArrayOf(41.5493f, 92.3655f),  // left mouth
        floatArrayOf(70.7299f, 92.2041f),  // right mouth
    )

    private val paint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)

    /** Returns a [Config.FACE_SIZE]² aligned face bitmap, or null if degenerate. */
    fun align(src: Bitmap, pts: Array<PointF>): Bitmap? {
        if (pts.size != 5) return null
        val n = 5
        var pmx = 0f; var pmy = 0f; var qmx = 0f; var qmy = 0f
        for (i in 0 until n) {
            pmx += pts[i].x; pmy += pts[i].y; qmx += REF[i][0]; qmy += REF[i][1]
        }
        pmx /= n; pmy /= n; qmx /= n; qmy /= n

        var sxx = 0f; var sxy = 0f; var denom = 0f
        for (i in 0 until n) {
            val px = pts[i].x - pmx; val py = pts[i].y - pmy
            val qx = REF[i][0] - qmx; val qy = REF[i][1] - qmy
            sxx += px * qx + py * qy
            sxy += px * qy - py * qx
            denom += px * px + py * py
        }
        if (denom < 1e-6f) return null
        val c = sxx / denom
        val d = sxy / denom
        val tx = qmx - (c * pmx - d * pmy)
        val ty = qmy - (d * pmx + c * pmy)

        val m = Matrix()
        m.setValues(floatArrayOf(c, -d, tx, d, c, ty, 0f, 0f, 1f))

        val out = Bitmap.createBitmap(Config.FACE_SIZE, Config.FACE_SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        canvas.drawColor(Color.BLACK)
        canvas.drawBitmap(src, m, paint)
        return out
    }
}
