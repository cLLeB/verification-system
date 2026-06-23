package com.faceverify.app.ui

import android.app.Application
import android.graphics.Bitmap
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.faceverify.app.Config
import com.faceverify.app.face.FaceEngine
import com.faceverify.app.face.LivenessTracker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

enum class Mode { VERIFY, ENROLL }

data class ScanResult(val ok: Boolean, val title: String, val sub: String)

class ScannerViewModel(app: Application) : AndroidViewModel(app) {
    var ready by mutableStateOf(false); private set
    var engineError by mutableStateOf<String?>(null); private set
    var mode by mutableStateOf(Mode.VERIFY); private set
    var status by mutableStateOf("Starting…"); private set
    var result by mutableStateOf<ScanResult?>(null); private set
    var enrollName by mutableStateOf("")
    var captured by mutableStateOf(0); private set
    var livenessProgress by mutableStateOf(0f); private set
    var people by mutableStateOf<List<String>>(emptyList()); private set

    private lateinit var engine: FaceEngine
    private val processing = AtomicBoolean(false)
    private val captureRequested = AtomicBoolean(false)
    private val liveness = LivenessTracker()
    val enrollTarget = Config.SAMPLES_PER_USER

    init {
        viewModelScope.launch {
            try {
                engine = FaceEngine.create(getApplication())
                refreshPeople()
                ready = true
                status = if (mode == Mode.VERIFY) "Center your face, then turn your head" else "Enter a name, then Capture"
            } catch (e: Exception) {
                engineError = e.message ?: "Failed to start the face engine. Is the model in assets?"
            }
        }
    }

    fun selectMode(m: Mode) {
        mode = m; result = null; status = ""; captured = 0; livenessProgress = 0f
        liveness.reset(); captureRequested.set(false)
    }

    fun requestEnrollCapture() { captureRequested.set(true) }

    fun scanAgain() {
        result = null; liveness.reset(); livenessProgress = 0f
        if (mode == Mode.ENROLL) captureRequested.set(false)
    }

    fun refreshPeople() = viewModelScope.launch { people = engine.repo.listUsers() }

    fun deleteUser(id: String) = viewModelScope.launch { engine.repo.delete(id); refreshPeople() }

    /** Called by the camera analyzer: returns true and locks if a frame should be processed. */
    fun tryBeginFrame(): Boolean {
        if (!ready || result != null) return false
        return processing.compareAndSet(false, true)
    }

    /** Process one upright camera frame. Recycles the bitmap and unlocks when done. */
    fun processFrame(bitmap: Bitmap) {
        viewModelScope.launch(Dispatchers.Default) {
            try {
                val face = engine.detect(bitmap)
                if (face == null) { status = "No face detected — move into the frame"; return@launch }
                if (face.facepx < Config.MIN_FACE_PX) { status = "Move a little closer"; return@launch }
                val yaw = face.yaw
                if (mode == Mode.VERIFY) handleVerify(bitmap, face, yaw)
                else handleEnroll(bitmap, face, yaw)
            } catch (_: Exception) {
                status = "Hiccup — keep your face in view"
            } finally {
                if (!bitmap.isRecycled) bitmap.recycle()
                processing.set(false)
            }
        }
    }

    private suspend fun handleVerify(bitmap: Bitmap, face: com.faceverify.app.face.DetectedFace, yaw: Float) {
        liveness.record(yaw)
        livenessProgress = liveness.progress()
        status = liveness.hint(yaw)
        if (liveness.passed && abs(yaw) <= Config.LIVE_FRONTAL_YAW) {
            val emb = engine.embed(bitmap, face) ?: return
            val dec = engine.repo.identify(emb)
            engine.repo.maybeAdapt(dec, emb, null)
            result = if (dec.granted)
                ScanResult(true, "Access granted", "Welcome, ${dec.userId}")
            else
                ScanResult(false, "Access denied", "Face not recognised")
            liveness.reset(); livenessProgress = 0f
        }
    }

    private suspend fun handleEnroll(bitmap: Bitmap, face: com.faceverify.app.face.DetectedFace, yaw: Float) {
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        status = if (abs(yaw) <= Config.LIVE_FRONTAL_YAW) "Hold still — tap Capture" else "Look straight at the camera"
        if (captureRequested.compareAndSet(true, false) && abs(yaw) <= Config.LIVE_FRONTAL_YAW) {
            val emb = engine.embed(bitmap, face)
            if (emb == null) { status = "Couldn't read your face — try again"; return }
            val r = engine.repo.enroll(enrollName, emb)
            if (!r.success) { result = ScanResult(false, "Enrolment failed", r.message); return }
            captured = r.samples
            if (captured >= enrollTarget) {
                result = ScanResult(true, "Enrolled", "${enrollName.trim()} is ready to verify")
                refreshPeople()
            } else {
                status = "Captured $captured of $enrollTarget — tap Capture again"
            }
        }
    }
}
