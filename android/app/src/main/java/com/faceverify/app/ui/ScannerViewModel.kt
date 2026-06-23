package com.faceverify.app.ui

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.faceverify.app.BuildConfig
import com.faceverify.app.Config
import com.faceverify.app.face.FaceEngine
import com.faceverify.app.face.LivenessTracker
import com.faceverify.app.sync.SyncManager
import com.faceverify.app.sync.SyncPrefs
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
        if (!captureRequested.compareAndSet(true, false)) return

        // Detect-the-document: if this capture is an ID card/passport, branch — extract
        // the largest face, skip the live-only frontal gate, tag provenance "id".
        if (Config.ID_DETECTION_ENABLED) {
            val a = try { engine.assessIdForEnroll(bitmap) } catch (_: Exception) { null }
            if (a != null && a.assessment.isId) {
                val emb = a.primaryEmbedding
                if (emb == null) {
                    status = "Detected an ID, but the photo on it is too unclear — try a clearer image or a live face"
                    return
                }
                finishEnroll(engine.repo.enroll(enrollName, emb, source = "id"), fromId = true)
                return
            }
        }

        // Normal live path — needs a frontal pose.
        if (abs(yaw) > Config.LIVE_FRONTAL_YAW) { status = "Look straight at the camera"; return }
        val emb = engine.embed(bitmap, face)
        if (emb == null) { status = "Couldn't read your face — try again"; return }
        finishEnroll(engine.repo.enroll(enrollName, emb), fromId = false)
    }

    private fun finishEnroll(r: com.faceverify.app.data.EnrollResult, fromId: Boolean) {
        if (!r.success) { result = ScanResult(false, "Enrolment failed", r.message); return }
        captured = r.samples
        val idNote = if (fromId) " (from ID — add a live capture for best accuracy)" else ""
        if (captured >= enrollTarget) {
            result = ScanResult(true, "Enrolled", "${enrollName.trim()} is ready to verify$idNote")
            refreshPeople()
        } else {
            status = "Captured $captured of $enrollTarget$idNote — tap Capture again"
        }
    }

    /** Enrol from a photo the admin picked from the gallery (PIN-gated in the UI).
     *  Mirrors the web /api/enroll upload: ID cards auto-branch; a normal photo must
     *  be front-facing. No liveness (a still image can't perform a head turn), so this
     *  is enrolment-only — verification still requires the live head-turn challenge. */
    fun enrollFromPhoto(uri: Uri) {
        if (!ready) return
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        if (!processing.compareAndSet(false, true)) return
        viewModelScope.launch(Dispatchers.Default) {
            var bmp: Bitmap? = null
            try {
                bmp = decodeUri(uri)
                if (bmp == null) { result = ScanResult(false, "Couldn't open photo", "Try another image"); return@launch }
                val a = engine.assessIdForEnroll(bmp)
                if (a.faces.isEmpty() || a.primaryFace == null) {
                    result = ScanResult(false, "No face found", "No clear face in that photo"); return@launch
                }
                val fromId = a.assessment.isId
                val emb: FloatArray?
                if (fromId) {
                    emb = a.primaryEmbedding
                } else {
                    if (abs(a.primaryFace.yaw) > Config.LIVE_FRONTAL_YAW) {
                        result = ScanResult(false, "Use a front-facing photo", "Face should look straight ahead")
                        return@launch
                    }
                    emb = engine.embed(bmp, a.primaryFace)
                }
                if (emb == null) {
                    result = ScanResult(false, "Face too unclear", "Use a clearer, larger photo of the face")
                    return@launch
                }
                finishEnroll(engine.repo.enroll(enrollName, emb, source = if (fromId) "id" else "live"), fromId)
            } catch (_: Exception) {
                result = ScanResult(false, "Enrolment failed", "Could not process that photo")
            } finally {
                bmp?.let { if (!it.isRecycled) it.recycle() }
                processing.set(false)
            }
        }
    }

    // --- hybrid sync (only meaningful when BuildConfig.HYBRID) ----------------
    val isHybrid = BuildConfig.HYBRID
    private val syncPrefs by lazy { SyncPrefs(getApplication()) }
    private var syncManager: SyncManager? = null
    var syncBusy by mutableStateOf(false); private set
    var syncMsg by mutableStateOf(""); private set
    var syncConflicts by mutableStateOf<List<String>>(emptyList()); private set

    fun syncServerUrl(): String = syncPrefs.serverUrl
    fun syncApiKeySet(): Boolean = syncPrefs.apiKey.isNotEmpty()
    fun lastSyncLabel(): String {
        val t = syncPrefs.lastSyncMs
        return if (t == 0L) "never"
        else java.text.DateFormat.getDateTimeInstance().format(java.util.Date(t))
    }
    fun lastSyncMsg(): String = syncPrefs.lastMsg

    fun saveSyncConfig(url: String, key: String) {
        syncPrefs.serverUrl = url
        if (key.isNotBlank()) syncPrefs.apiKey = key
        syncPrefs.resetWatermark()                 // config changed → next pull re-fetches all
        syncMsg = "Saved. Connect with Test, then Pull."
    }

    private fun mgr(): SyncManager? {
        if (!::engine.isInitialized) return null
        if (syncManager == null) syncManager = SyncManager(engine.repo, syncPrefs)
        return syncManager
    }

    fun testSync() = runSync { mgr()?.test() }
    fun pullNow() = runSync { mgr()?.pull() }
    fun pushAll(onConflict: String) = runSync { mgr()?.push(null, onConflict) }

    private fun runSync(block: suspend () -> SyncManager.Result?) {
        if (!syncPrefs.configured) { syncMsg = "Set the server URL and API key first."; return }
        if (syncBusy) return
        syncBusy = true; syncMsg = "Working…"; syncConflicts = emptyList()
        viewModelScope.launch(Dispatchers.Default) {
            val r = try { block() } catch (e: Exception) { null }
            syncMsg = r?.summary ?: "Sync unavailable."
            syncConflicts = r?.conflicts ?: emptyList()
            if (r?.ok == true) refreshPeople()
            syncBusy = false
        }
    }

    /** Decode a picked image, downscaling very large photos to keep memory sane. */
    private fun decodeUri(uri: Uri): Bitmap? {
        val cr = getApplication<Application>().contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        cr.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        val maxDim = 1600
        while (bounds.outWidth / sample > maxDim || bounds.outHeight / sample > maxDim) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return cr.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
    }
}
