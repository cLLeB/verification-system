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
import com.faceverify.app.PalmConfig
import com.faceverify.app.credential.CredentialVerifier
import com.faceverify.app.credential.TrustData
import com.faceverify.app.credential.TrustStoreManager
import com.faceverify.app.face.FaceEngine
import com.faceverify.app.face.LivenessTracker
import com.faceverify.app.palm.PalmEngine
import com.faceverify.app.sync.SyncManager
import com.faceverify.app.sync.SyncPrefs
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

enum class Mode { VERIFY, ENROLL, CREDENTIAL }

data class ScanResult(val ok: Boolean, val title: String, val sub: String)

/** Plain-language screens per typed failure code (spec 6.6) — the same copy as
 *  the web verifier, so every surface reads identically. */
private val CRED_FAIL_COPY = mapOf(
    "malformed_credential" to ("Not a valid credential" to "This QR isn't one of ours, or it's damaged. Ask for the original card."),
    "unsupported_version" to ("Newer credential" to "This credential needs an updated verifier app."),
    "bad_signature" to ("TAMPERED / FORGED" to "The signature check failed — do not accept this credential."),
    "unknown_issuer" to ("Issuer not trusted" to "This card's issuer isn't in this device's trust list."),
    "credential_expired" to ("Expired" to "This credential has passed its expiry date."),
    "credential_not_yet_valid" to ("Not yet valid" to "This credential's start date is in the future."),
    "credential_revoked" to ("REVOKED" to "The issuer cancelled this credential. Do not accept it."),
)

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
    private var palm: PalmEngine? = null          // null when palm assets aren't bundled
    private val processing = AtomicBoolean(false)
    private val captureRequested = AtomicBoolean(false)
    private val liveness = LivenessTracker()
    val enrollTarget = Config.SAMPLES_PER_USER

    init {
        viewModelScope.launch {
            try {
                engine = FaceEngine.create(getApplication())
                // Palm is optional: load it if its assets are bundled, else run face-only.
                palm = try {
                    if (PalmEngine.available(getApplication())) PalmEngine.create(getApplication()) else null
                } catch (_: Exception) { null }
                trustData = TrustStoreManager.load(getApplication())
                refreshPeople()
                ready = true
                status = if (mode == Mode.VERIFY) "Show your face (turn your head) — or your palm" else "Enter a name, then show face or palm"
            } catch (e: Exception) {
                engineError = e.message ?: "Failed to start the engine. Is the model in assets?"
            }
        }
    }

    fun selectMode(m: Mode) {
        mode = m; result = null; status = ""; captured = 0; livenessProgress = 0f
        liveness.reset(); captureRequested.set(false)
        credPayload = null
        if (m == Mode.CREDENTIAL) {
            status = if (trustData == null)
                "No trust list on this device yet — add one in Settings"
            else "Point the BACK camera at the QR on the card"
        }
    }

    fun requestEnrollCapture() { captureRequested.set(true) }

    fun scanAgain() {
        result = null; liveness.reset(); livenessProgress = 0f
        if (mode == Mode.ENROLL) captureRequested.set(false)
        if (mode == Mode.CREDENTIAL) {
            credPayload = null
            status = "Point the BACK camera at the QR on the card"
        }
    }

    fun refreshPeople() = viewModelScope.launch {
        // A person may be enrolled by face, palm, or both — show the union.
        val faces = engine.repo.listUsers()
        val palms = palm?.repo?.listUsers() ?: emptyList()
        people = (faces + palms).distinct().sorted()
    }

    fun deleteUser(id: String) = viewModelScope.launch {
        engine.repo.delete(id)
        palm?.repo?.delete(id)        // remove both modalities for this person
        refreshPeople()
    }

    /** Called by the camera analyzer: returns true and locks if a frame should be processed. */
    fun tryBeginFrame(): Boolean {
        if (!ready || result != null) return false
        return processing.compareAndSet(false, true)
    }

    /** Process one upright camera frame. Recycles the bitmap and unlocks when done. */
    fun processFrame(bitmap: Bitmap) {
        viewModelScope.launch(Dispatchers.Default) {
            try {
                if (mode == Mode.CREDENTIAL) {
                    handleCredentialFrame(bitmap)
                    return@launch
                }
                val face = engine.detect(bitmap)
                if (face != null && face.facepx >= Config.MIN_FACE_PX) {
                    val yaw = face.yaw
                    if (mode == Mode.VERIFY) handleVerify(bitmap, face, yaw)
                    else handleEnroll(bitmap, face, yaw)
                    return@launch
                }
                // No usable face — auto-route to palm (the user never has to choose).
                val p = palm
                if (p != null && p.hasPalm(bitmap).first) {
                    if (mode == Mode.VERIFY) handlePalmVerify(p, bitmap) else handlePalmEnroll(p, bitmap)
                    return@launch
                }
                status = when {
                    face != null -> "Move a little closer"
                    palm != null -> "Show your face or open palm"
                    else -> "No face detected — move into the frame"
                }
            } catch (_: Exception) {
                status = "Hiccup — keep your face or palm in view"
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

    /** Palm verify — no head-turn (that's a face challenge); a single good palm
     *  capture matches against the palm store. Mirrors the server's palm path. */
    private suspend fun handlePalmVerify(p: PalmEngine, bitmap: Bitmap) {
        val s = p.embed(bitmap)
        if (s.embedding == null) { status = s.message; return }
        val dec = p.repo.identify(s.embedding)
        p.repo.maybeAdapt(dec, s.embedding, null)
        result = if (dec.granted)
            ScanResult(true, "Access granted", "Welcome, ${dec.userId} (via palm)")
        else
            ScanResult(false, "Access denied", "Palm not recognised")
        livenessProgress = 0f
    }

    /** Palm enrol — tap Capture with an open palm shown (same 3-capture rhythm).
     *  Uses the STRICT anchor-quality gate: weak anchors degrade matching forever. */
    private suspend fun handlePalmEnroll(p: PalmEngine, bitmap: Bitmap) {
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        status = "Show your open palm — tap Capture"
        if (!captureRequested.compareAndSet(true, false)) return
        val s = p.embed(bitmap, forEnroll = true)
        if (s.embedding == null) { status = s.message; return }
        finishEnroll(p.repo.enroll(enrollName, s.embedding), fromId = false)
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
                    // No face — a gallery photo of a palm should enrol as palm
                    // (strict anchor-quality gate, same as live palm enrolment).
                    val p = palm
                    if (p != null) {
                        val s = p.embed(bmp, forEnroll = true)
                        if (s.embedding != null) {
                            finishEnroll(p.repo.enroll(enrollName, s.embedding), fromId = false); return@launch
                        }
                        result = ScanResult(false, "No face or palm found", s.message); return@launch
                    }
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

    // --- offline credential verifier (trust platform phase 2) -----------------
    // Scan the FV1 QR on someone's card/phone, check signature + expiry +
    // revocation against the on-device trust list, then match the LIVE person
    // against the template inside the credential — fully offline.
    var credPayload by mutableStateOf<CredentialVerifier.Payload?>(null); private set
    var trustData by mutableStateOf<TrustData?>(null); private set
    var trustMsg by mutableStateOf(""); private set
    var trustBusy by mutableStateOf(false); private set

    private val qrScanner by lazy {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }

    private fun credFail(code: String, fallback: String) {
        val (title, sub) = CRED_FAIL_COPY[code] ?: ("Verification failed" to fallback)
        result = ScanResult(false, title, sub)
    }

    private suspend fun handleCredentialFrame(bitmap: Bitmap) {
        val trust = trustData
        if (trust == null) {
            status = "No trust list on this device yet — add one in Settings"
            return
        }
        val payload = credPayload
        if (payload == null) {                        // phase 1: find + check the QR
            val codes = try {
                qrScanner.process(InputImage.fromBitmap(bitmap, 0)).await()
            } catch (e: Exception) {
                emptyList()
            }
            val text = codes.firstOrNull { it.rawValue?.startsWith("FV1:") == true }?.rawValue
            if (text == null) {
                status = "Point the BACK camera at the QR on the card"
                return
            }
            val p = try {
                CredentialVerifier.verify(text, { iss, kid -> trust.resolve(iss, kid) })
            } catch (e: CredentialVerifier.CredentialException) {
                credFail(e.code, e.message ?: ""); return
            }
            if (trust.isRevoked(p.issuer, p.cid.joinToString("") { "%02x".format(it) })) {
                credFail("credential_revoked", ""); return
            }
            credPayload = p
            liveness.reset(); livenessProgress = 0f
            status = "Card OK (${p.name ?: p.subject}, issuer ${p.issuer}) — " +
                if ("face" in p.modalities) "now the person: turn your head slowly"
                else "now the person's open palm"
            return
        }

        // phase 2: live capture, matched INSIDE the credential's domain
        if ("face" in payload.modalities) {
            val face = engine.detect(bitmap)
            if (face == null || face.facepx < Config.MIN_FACE_PX) {
                status = "Show the person's face — turn the head slowly"
                return
            }
            liveness.record(face.yaw)
            livenessProgress = liveness.progress()
            status = liveness.hint(face.yaw)
            if (liveness.passed && abs(face.yaw) <= Config.LIVE_FRONTAL_YAW) {
                val emb = engine.embed(bitmap, face) ?: return
                credVerdict(payload, "face",
                    CredentialVerifier.match(payload, "face", emb), Config.MATCH_THRESHOLD)
            }
            return
        }
        val p = palm
        if (p != null && "palm" in payload.modalities) {
            val s = p.embed(bitmap)
            if (s.embedding == null) { status = s.message; return }
            credVerdict(payload, "palm",
                CredentialVerifier.match(payload, "palm", s.embedding), PalmConfig.MATCH_THRESHOLD)
            return
        }
        result = ScanResult(false, "Can't check this card here",
            "This credential carries ${payload.modalities.joinToString("+")}, " +
                "which this device can't capture.")
    }

    private fun credVerdict(p: CredentialVerifier.Payload, modality: String,
                            score: Float, threshold: Float) {
        val attrs = p.attrs?.entries?.joinToString(" · ") { "${it.key}: ${it.value}" } ?: ""
        result = if (score >= threshold) {
            ScanResult(true, "VERIFIED — ${p.name ?: p.subject}",
                "${p.subject} · issuer ${p.issuer} · $modality" +
                    if (attrs.isNotEmpty()) "\n$attrs" else "")
        } else {
            ScanResult(false, "NOT the credential holder",
                "The live $modality does not match this card.")
        }
        liveness.reset(); livenessProgress = 0f
    }

    // --- trust list management (Settings) --------------------------------------
    fun trustSummary(): String {
        val t = trustData ?: return "not loaded"
        return "${t.issuerCount} issuer(s) · updated " +
            java.text.DateFormat.getDateTimeInstance().format(java.util.Date(t.generated * 1000))
    }

    /** Hybrid: fetch + verify the signed trust store from the configured server. */
    fun refreshTrust() {
        if (!syncPrefs.configured) { trustMsg = "Set the server URL and API key first."; return }
        if (trustBusy) return
        trustBusy = true; trustMsg = "Fetching…"
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val resp = com.faceverify.app.sync.SyncClient(
                    syncPrefs.serverUrl, syncPrefs.apiKey).get("/v1/trust-store")
                trustData = TrustStoreManager.save(getApplication(), resp)
                trustMsg = "Trust list updated: ${trustSummary()}."
            } catch (e: IllegalArgumentException) {
                trustMsg = e.message ?: "Trust store rejected."
            } catch (e: Exception) {
                trustMsg = "Fetch failed: ${e.message ?: "network error"}"
            } finally {
                trustBusy = false
            }
        }
    }

    /** All builds (incl. air-gapped): import a saved /v1/trust-store JSON file
     *  moved here out-of-band. Signature-checked against the pinned root. */
    fun importTrust(uri: Uri) {
        if (trustBusy) return
        trustBusy = true; trustMsg = "Importing…"
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val bytes = getApplication<Application>().contentResolver
                    .openInputStream(uri)?.use { it.readBytes() }
                if (bytes == null) { trustMsg = "Couldn't open that file."; return@launch }
                val resp = org.json.JSONObject(String(bytes, Charsets.UTF_8))
                trustData = TrustStoreManager.save(getApplication(), resp)
                trustMsg = "Trust list imported: ${trustSummary()}."
            } catch (e: IllegalArgumentException) {
                trustMsg = e.message ?: "Trust store rejected."
            } catch (e: Exception) {
                trustMsg = "Import failed — is this a saved trust-store file?"
            } finally {
                trustBusy = false
            }
        }
    }

    // --- offline provisioning bundle (works on ALL builds, incl. air-gapped) --
    // Bulk-load templates the admin exported on the server, moved here out-of-band
    // (USB / MDM). No network is used — the file is decrypted locally with the same
    // passphrase. This is how an air-gapped device gets a roster without enrolling
    // each person by hand, while staying fully offline.
    var bundleBusy by mutableStateOf(false); private set
    var bundleMsg by mutableStateOf(""); private set

    fun importBundle(uri: Uri, passphrase: String) {
        if (!ready) { bundleMsg = "Engine not ready yet."; return }
        if (passphrase.trim().length < 8) { bundleMsg = "Enter the bundle passphrase (8+ chars)."; return }
        if (bundleBusy) return
        bundleBusy = true; bundleMsg = "Importing…"
        viewModelScope.launch(Dispatchers.Default) {
            try {
                val bytes = getApplication<Application>().contentResolver
                    .openInputStream(uri)?.use { it.readBytes() }
                if (bytes == null) { bundleMsg = "Couldn't open that file."; return@launch }
                val parsed = com.faceverify.app.data.BundleImporter.parse(bytes, passphrase)
                val (f, pl) = com.faceverify.app.data.BundleImporter.apply(
                    parsed, engine.repo, palm?.repo)
                val skipped = if (palm == null && parsed.palm.isNotEmpty())
                    " (${parsed.palm.size} palm skipped — face-only build)" else ""
                bundleMsg = "Imported $f face + $pl palm identities$skipped."
                refreshPeople()
            } catch (e: com.faceverify.app.data.BundleImporter.BundleException) {
                bundleMsg = e.message ?: "Import failed."
            } catch (e: Exception) {
                bundleMsg = "Import failed — check the file and passphrase."
            } finally {
                bundleBusy = false
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
