package com.faceverify.app.ui

import android.graphics.Bitmap
import android.graphics.Matrix
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import java.util.concurrent.Executors

private fun ImageProxy.toUprightBitmap(): Bitmap {
    val bmp = toBitmap()
    val deg = imageInfo.rotationDegrees
    if (deg == 0) return bmp
    val m = Matrix().apply { postRotate(deg.toFloat()) }
    val rotated = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
    if (rotated != bmp) bmp.recycle()
    return rotated
}

/**
 * Live camera preview that streams frames to [onBitmap] — but only when
 * [shouldProcess] returns true (so we never queue work while busy or showing a
 * result). Each delivered bitmap is upright and owned by the consumer (recycle it).
 */
@Composable
fun CameraPreview(
    modifier: Modifier = Modifier,
    lensFacing: Int = CameraSelector.LENS_FACING_FRONT,
    shouldProcess: () -> Boolean,
    onBitmap: (Bitmap) -> Unit,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val previewView = rememberPreviewView()
    val analysisExecutor = rememberExecutor()

    DisposableEffect(lensFacing) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
            analysis.setAnalyzer(analysisExecutor) { proxy ->
                try {
                    if (shouldProcess()) onBitmap(proxy.toUprightBitmap())
                } catch (_: Exception) {
                    // never crash the camera loop
                } finally {
                    proxy.close()
                }
            }
            val selector = CameraSelector.Builder().requireLensFacing(lensFacing).build()
            try {
                provider.unbindAll()
                provider.bindToLifecycle(lifecycleOwner, selector, preview, analysis)
            } catch (_: Exception) {
            }
        }, ContextCompat.getMainExecutor(context))

        onDispose {
            runCatching { ProcessCameraProvider.getInstance(context).get().unbindAll() }
        }
    }

    AndroidView(factory = { previewView }, modifier = modifier)
}

@Composable
private fun rememberPreviewView(): PreviewView {
    val ctx = LocalContext.current
    return remember { PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER } }
}

@Composable
private fun rememberExecutor() = remember { Executors.newSingleThreadExecutor() }
