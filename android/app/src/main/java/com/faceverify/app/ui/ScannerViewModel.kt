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
import com.faceverify.app.capture.CaptureCoach
import com.faceverify.app.capture.CaptureQuality
import com.faceverify.app.capture.HeadTurnScript
import com.faceverify.app.capture.RetryPolicy
import com.faceverify.app.credential.CredentialVerifier
import com.faceverify.app.credential.TrustData
import com.faceverify.app.credential.TrustStoreManager
import com.faceverify.app.face.FaceDetectorMlKit
import com.faceverify.app.face.FaceEngine
import com.faceverify.app.face.LivenessTracker
import com.faceverify.app.online.ApiClient
import com.faceverify.app.online.OnlinePrefs
import com.faceverify.app.online.RemoteBackend
import com.faceverify.app.palm.PalmEngine
import com.faceverify.app.palm.PalmRoi
import com.faceverify.app.sync.SyncManager
import com.faceverify.app.sync.SyncPrefs
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

enum class Mode { VERIFY, ENROLL, CREDENTIAL, GLANCE }

data class ScanResult(val ok: Boolean, val title: String, val sub: String)

/** Plain-language screens per typed failure code (spec 6.6) - the same copy as
 *  the web verifier, so every surface reads identically. */
private val CRED_FAIL_COPY = mapOf(
    "malformed_credential" to ("Not a valid credential" to "This QR isn't one of ours, or it's damaged. Ask for the original card."),
    "unsupported_version" to ("Newer credential" to "This credential needs an updated verifier app."),
    "bad_signature" to ("TAMPERED / FORGED" to "The signature check failed - do not accept this credential."),
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
    // Set when a palm capture matches neither of this name's enrolled hands: the UI
    // asks whether to bind it as the person's OTHER hand (see confirmOtherHand).
    var pendingOtherHand by mutableStateOf<String?>(null); private set
    private var pendingHandEmb: FloatArray? = null
    private var pendingHandSide: String = ""
    var livenessProgress by mutableStateOf(0f); private set
    var people by mutableStateOf<List<String>>(emptyList()); private set

    private lateinit var engine: FaceEngine
    private var palm: PalmEngine? = null          // null when palm assets aren't bundled
    private val processing = AtomicBoolean(false)
    private val captureRequested = AtomicBoolean(false)
    private val liveness = LivenessTracker()
    val enrollTarget = Config.SAMPLES_PER_USER

    // --- live capture guidance (all flavors) ---------------------------------
    // The chips run off the DETECTORS only, so they work on every build - including
    // the online one, which bundles no recognition model. Advisory throughout: a
    // failed coaching pass leaves the chips as they were and never blocks a capture.
    /** Lighting / Distance / Angle for the current frame. */
    var quality by mutableStateOf(CaptureQuality()); private set
    private var coach: CaptureCoach? = null
    private var lastCoachAt = 0L

    /** True from the moment the shutter is tapped until a verdict is shown. */
    var capturing by mutableStateOf(false); private set

    /** 0..1 across the head-turn challenge, for the ring around the shutter. */
    var captureProgress by mutableStateOf(0f); private set

    private val script = HeadTurnScript()

    // Automatic retry after a COACHABLE failure - the person already tapped, so
    // finishing the attempt they started is not a new consent decision.
    private var retriesLeft = RetryPolicy.MAX_ATTEMPTS
    private var retryJob: Job? = null
    /** Seconds until the automatic retry, or 0 when none is pending. */
    var retryIn by mutableStateOf(0); private set

    // --- online build --------------------------------------------------------
    /** This build sends frames to a server and stores nothing locally. */
    val isOnline = BuildConfig.ONLINE
    private val onlinePrefs by lazy { OnlinePrefs(getApplication()) }
    private var remote: RemoteBackend? = null
    /** Frames collected for the current online burst, already JPEG+base64 encoded so
     *  no bitmap is held past the frame callback that produced it. */
    private val burst = mutableListOf<String>()
    private var burstToken: String? = null
    private var burstIsPalm = false
    private var lastBurstAt = 0L

    // Offline mirror of the service gates (policies / guests / consent /
    // guardians). Null until first synced - all gates pass, exactly as before.
    private var serviceState: com.faceverify.app.data.ServiceState? = null

    init {
        viewModelScope.launch {
            try {
                if (isOnline) startOnline() else startOnDevice()
                ready = true
                status = idleStatus()
            } catch (e: Exception) {
                engineError = e.message ?: "Failed to start the engine. Is the model in assets?"
            }
        }
    }

    /** Offline / hybrid: the whole pipeline runs here, so load every model. */
    private suspend fun startOnDevice() {
        engine = FaceEngine.create(getApplication())
        // Palm is optional: load it if its assets are bundled, else run face-only.
        palm = try {
            if (PalmEngine.available(getApplication())) PalmEngine.create(getApplication()) else null
        } catch (_: Exception) { null }
        coach = CaptureCoach(engine.detector, palm?.roi)
        trustData = TrustStoreManager.load(getApplication())
        serviceState = com.faceverify.app.data.ServiceState.load(getApplication())
        glanceFace = com.faceverify.app.glance.GlanceIndexStore.load(getApplication(), "face")
        glancePalm = com.faceverify.app.glance.GlanceIndexStore.load(getApplication(), "palm")
        refreshPeople()
    }

    /** Online: the server recognises, so no recognition model is loaded (none is even
     *  bundled). The DETECTORS still load - they are small, they ship in every flavor,
     *  and they are what makes the framing chips instant without a network round-trip.
     *  Nothing is stored on this device, so there is no local roster to read. */
    private fun startOnline() {
        remote = RemoteBackend(onlinePrefs)
        val palmRoi = try {
            if (PalmRoi.available(getApplication())) PalmRoi.load(getApplication()) else null
        } catch (_: Exception) { null }
        coach = CaptureCoach(FaceDetectorMlKit(), palmRoi)
    }

    private fun idleStatus(): String =
        if (mode == Mode.ENROLL) "Enter a name, then tap the shutter"
        else "Show your face - or your open hand - then tap to verify"

    fun selectMode(m: Mode) {
        mode = m; result = null; status = ""; captured = 0; livenessProgress = 0f
        liveness.reset(); captureRequested.set(false)
        endCapture()                       // a mode switch cancels any pending retry
        quality = CaptureQuality()
        credPayload = null
        glanceHit = null
        if (m == Mode.VERIFY || m == Mode.ENROLL) status = idleStatus()
        if (m == Mode.CREDENTIAL) {
            status = if (trustData == null)
                "No trust list on this device yet - add one in Settings"
            else "Point the BACK camera at the QR on the card"
        }
        if (m == Mode.GLANCE) {
            status = if (glanceFace == null && glancePalm == null && people.isEmpty())
                "No glance index yet - update it in Settings"
            else "Glance: point at a face - or hold up an open hand"
        }
    }

    /** The shutter. One deliberate tap starts one attempt, in either mode - so a person
     *  always knows when they are being judged, instead of the camera deciding for them.
     *  A manual tap always takes over: it cancels a pending automatic retry and restores
     *  the full retry budget. */
    fun requestCapture() {
        if (!ready || capturing) return
        if (mode == Mode.ENROLL && enrollName.isBlank()) {
            status = "Enter a name first"
            return
        }
        cancelRetry()
        retriesLeft = RetryPolicy.MAX_ATTEMPTS
        beginCapture()
    }

    /** Start one attempt without touching the retry budget (used by the auto-retry). */
    private fun beginCapture() {
        result = null
        liveness.reset()
        livenessProgress = 0f
        captureProgress = 0f
        burst.clear()
        burstToken = null
        burstIsPalm = quality.modality == "palm"
        capturing = true
        captureRequested.set(true)
        // A palm needs no head-turn - that is a face challenge. The script only runs
        // for a face, and only when the decision maker actually asks for liveness.
        if (!burstIsPalm) script.start(System.currentTimeMillis()) else script.reset()
        status = if (burstIsPalm) "Hold your open hand steady…" else script.instruction

        if (isOnline) {
            viewModelScope.launch(Dispatchers.IO) {
                burstToken = if (burstIsPalm) null else try { remote?.challenge() } catch (_: Exception) { null }
            }
        }
    }

    /** Clear all capture state: no run in progress, no retry pending. */
    private fun endCapture() {
        capturing = false
        captureRequested.set(false)
        captureProgress = 0f
        script.reset()
        scriptDoneAt = 0L
        burst.clear()
        burstToken = null
        cancelRetry()
    }

    private fun cancelRetry() {
        retryJob?.cancel()
        retryJob = null
        retryIn = 0
    }

    /** Schedule another attempt after a failure the person can fix by moving. The
     *  countdown is shown so the advice is readable and the retry is never a surprise. */
    private fun scheduleRetry(advice: String) {
        if (retriesLeft <= 0) {                       // budget spent: hand control back
            status = "$advice - tap to try again"
            return
        }
        retriesLeft--
        cancelRetry()
        retryJob = viewModelScope.launch {
            var left = (RetryPolicy.DELAY_MS / 1000).toInt()
            retryIn = left
            status = advice
            while (left > 0) {
                delay(1000)
                left--
                retryIn = left
            }
            retryIn = 0
            beginCapture()
        }
    }

    /** A failed attempt: either coach-and-retry, or show the verdict and stop. */
    private fun failAttempt(code: String, message: String, title: String) {
        endCapture()
        if (RetryPolicy.isCoachable(code)) scheduleRetry(message)
        else result = ScanResult(false, title, message)
    }

    fun scanAgain() {
        result = null; liveness.reset(); livenessProgress = 0f
        endCapture()
        retriesLeft = RetryPolicy.MAX_ATTEMPTS
        if (mode == Mode.CREDENTIAL) {
            credPayload = null
            status = "Point the BACK camera at the QR on the card"
        } else {
            status = idleStatus()
        }
    }

    /** Why the People list is empty, when the reason is not "nobody is enrolled".
     *  The online build reads the roster from the server, which needs the admin
     *  password - saying so beats showing a silently empty list. */
    var peopleMsg by mutableStateOf(""); private set

    fun refreshPeople() = viewModelScope.launch {
        if (isOnline) {
            people = try {
                peopleMsg = ""
                remote?.listUsers() ?: emptyList()
            } catch (e: ApiClient.AuthRequired) {
                // Not a misconfiguration, and not affected by open enrolment: the server
                // gates the roster on an admin session ALWAYS (/api/users), because
                // listing everyone enrolled is not a walk-up action even where adding
                // yourself is. Say so, so this doesn't read as a broken setting.
                peopleMsg = "The roster is always admin-only, even while enrolment is open. " +
                    "Add the admin password in Settings to see who is enrolled."
                emptyList()
            } catch (e: Exception) {
                peopleMsg = "Could not reach the server."
                emptyList()
            }
            return@launch
        }
        // A person may be enrolled by face, palm, or both - show the union.
        val faces = engine.repo.listUsers()
        val palms = palm?.repo?.listUsers() ?: emptyList()
        people = (faces + palms).distinct().sorted()
    }

    fun deleteUser(id: String) = viewModelScope.launch {
        if (isOnline) {
            try { remote?.delete(id) } catch (e: ApiClient.AuthRequired) {
                peopleMsg = "Add the admin password in Settings to remove people."
            } catch (e: Exception) {
                peopleMsg = "Could not reach the server."
            }
            refreshPeople()
            return@launch
        }
        engine.repo.delete(id)
        palm?.repo?.delete(id)        // remove both modalities for this person
        refreshPeople()
    }

    /** Called by the camera analyzer: returns true and locks if a frame should be processed. */
    fun tryBeginFrame(): Boolean {
        if (!ready || result != null) return false
        return processing.compareAndSet(false, true)
    }

    /** Process one upright camera frame. Recycles the bitmap and unlocks when done.
     *
     *  While idle the frame feeds the framing chips; once the shutter has been tapped
     *  it feeds the actual attempt. Credential and Glance are continuous by design and
     *  keep their own loops. */
    fun processFrame(bitmap: Bitmap) {
        viewModelScope.launch(Dispatchers.Default) {
            try {
                when {
                    mode == Mode.CREDENTIAL -> handleCredentialFrame(bitmap)
                    mode == Mode.GLANCE -> handleGlanceFrame(bitmap)
                    capturing && isOnline -> onlineCaptureFrame(bitmap)
                    capturing -> localCaptureFrame(bitmap)
                    else -> maybeCoach(bitmap)
                }
            } catch (_: Exception) {
                status = "Hiccup - keep your face or hand in view"
            } finally {
                if (!bitmap.isRecycled) bitmap.recycle()
                processing.set(false)
            }
        }
    }

    /** Idle framing guidance, at a slow cadence so it never competes with a capture. */
    private suspend fun maybeCoach(bitmap: Bitmap) {
        val now = System.currentTimeMillis()
        if (now - lastCoachAt < Config.COACH_INTERVAL_MS) return
        lastCoachAt = now
        val c = coach ?: return
        // Probe the palm first, exactly as the server does: presenting an open, spread
        // hand is deliberate, while a face in shot is often a bystander. Face-first
        // meant a palm presented in a busy room routed to face and was then asked for
        // a head-turn. Mirrors face_service/modality.py route(primary="palm").
        val q = c.assess(bitmap, palmFirst = true)
        quality = q
        if (retryIn == 0) q.hint()?.let { status = it } ?: run { status = idleStatus() }
    }

    /** On-device capture: the existing pipeline, now started by the shutter and guided
     *  by the head-turn script instead of firing whenever liveness happened to pass. */
    private suspend fun localCaptureFrame(bitmap: Bitmap) {
        // Palm first (see maybeCoach) - a deliberate open hand outranks a bystander.
        val p = palm
        if (p != null && p.hasPalm(bitmap).first) {
            if (mode == Mode.VERIFY) handlePalmVerify(p, bitmap) else handlePalmEnroll(p, bitmap)
            return
        }
        val face = engine.detect(bitmap)
        if (face != null && face.facepx >= Config.MIN_FACE_PX) {
            if (mode == Mode.VERIFY) handleVerify(bitmap, face, face.yaw)
            else handleEnroll(bitmap, face, face.yaw)
            return
        }
        status = when {
            face != null -> "Move a little closer"
            palm != null -> "Show your face or open hand"
            else -> "No face detected - move into the frame"
        }
    }

    /** Online capture: collect the burst this device is responsible for, then hand the
     *  decision to the server. Frames are encoded immediately so no bitmap outlives the
     *  callback that produced it. */
    private suspend fun onlineCaptureFrame(bitmap: Bitmap) {
        val now = System.currentTimeMillis()
        val target: Int
        if (burstIsPalm) {
            target = RemoteBackend.PALM_BURST_FRAMES
            if (burst.isNotEmpty() && now - lastBurstAt < RemoteBackend.PALM_BURST_GAP_MS) return
        } else {
            target = HeadTurnScript.TOTAL_FRAMES
            if (!script.offer(now)) {
                status = script.instruction
                return
            }
            captureProgress = script.progress
        }
        lastBurstAt = now
        burst.add(ApiClient.dataUrl(bitmap, RemoteBackend.FRAME_WIDTH, RemoteBackend.FRAME_QUALITY))
        status = if (burstIsPalm) "Hold your open hand steady…" else script.instruction
        if (burst.size < target) return

        // Burst complete - stop taking frames and ask the server.
        captureRequested.set(false)
        captureProgress = 1f
        status = "Checking…"
        val frames = burst.toList()
        burst.clear()
        submitOnline(frames)
    }

    /** Send a completed burst and turn the server's answer into a verdict. */
    private suspend fun submitOnline(frames: List<String>) {
        val r = remote ?: return
        val outcome = try {
            // Verifying sends no user_id: this is walk-up 1:N identify, the same call
            // the web verifier makes. A deployment with open identify disabled answers
            // "identify_disabled", which is shown as-is rather than guessed at.
            if (mode == Mode.ENROLL) r.enroll(enrollName, frames)
            else r.verify("", frames, burstToken)
        } catch (e: ApiClient.AuthRequired) {
            endCapture()
            result = ScanResult(false, "Admin password needed", e.message ?: "Add it in Settings.")
            return
        } catch (e: Exception) {
            endCapture()
            result = ScanResult(false, "Can't reach the server",
                "Check the connection and the server address in Settings.")
            return
        }

        if (!outcome.success) {
            failAttempt(outcome.code, outcome.message.ifBlank { "Try again." },
                if (mode == Mode.ENROLL) "Enrolment failed" else "Access denied")
            return
        }
        endCapture()
        if (mode == Mode.ENROLL) {
            result = ScanResult(true, "Enrolled",
                "${enrollName.trim()} is ready to verify")
            refreshPeople()
        } else {
            result = grantedResult(true, outcome.userId, "Not recognised")
        }
    }

    /** Grace after the guided turn finishes, before calling it a liveness failure. The
     *  last phase is "look straight at the camera", so the frontal frames the embedding
     *  needs arrive right at the end - failing the instant the script ends would throw
     *  away the very frames the attempt was building toward. */
    private val livenessGraceMs = 1200L
    private var scriptDoneAt = 0L

    private suspend fun handleVerify(bitmap: Bitmap, face: com.faceverify.app.face.DetectedFace, yaw: Float) {
        val now = System.currentTimeMillis()
        // The coach may have predicted a palm (so the script was not started) and the
        // person then presented a face. Without this the challenge would have no
        // instructions and no end condition, and the attempt would never finish.
        if (!script.active) script.start(now)

        liveness.record(yaw)
        livenessProgress = liveness.progress()

        script.offer(now)                       // advance the instruction timeline
        captureProgress = script.progress
        if (script.done && scriptDoneAt == 0L) scriptDoneAt = now
        status = if (script.done) "Hold still…" else script.instruction

        if (liveness.passed && abs(yaw) <= Config.LIVE_FRONTAL_YAW) {
            val emb = engine.embed(bitmap, face)
            if (emb == null) {
                scriptDoneAt = 0L
                failAttempt("low_quality", "Could not read your face - hold still and try again.",
                    "Verification failed")
                return
            }
            val dec = engine.repo.identify(emb)
            engine.repo.maybeAdapt(dec, emb, null)
            scriptDoneAt = 0L
            endCapture()
            liveness.reset(); livenessProgress = 0f
            result = grantedResult(dec.granted, dec.userId, "Face not recognised")
            return
        }
        if (script.done && now - scriptDoneAt > livenessGraceMs) {
            scriptDoneAt = 0L
            failAttempt("liveness", "Turn your head a bit more, side to side.",
                "Verification failed")
        }
    }

    /** Shared verify verdict: applies the offline service gates (guest expiry ->
     *  consent -> access policy, mirroring the server) to a granted match, and
     *  notes who a verified guardian may collect for. */
    private fun grantedResult(granted: Boolean, userId: String?, denyMsg: String): ScanResult {
        if (!granted || userId == null) return ScanResult(false, "Access denied", denyMsg)
        val st = serviceState
        val gate = st?.gate(userId)
        if (gate != null) {
            val title = when (gate.code) {
                "access_denied" -> "Not allowed right now"
                "identity_expired" -> "Guest pass expired"
                else -> "Consent required"
            }
            return ScanResult(false, title, gate.message)
        }
        val wards = st?.wardsOf(userId) ?: emptyList()
        val note = if (wards.isEmpty()) "" else " - may collect for: ${wards.joinToString(", ")}"
        return ScanResult(true, "Access granted", "Welcome, $userId$note")
    }

    private suspend fun handleEnroll(bitmap: Bitmap, face: com.faceverify.app.face.DetectedFace, yaw: Float) {
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        if (!captureRequested.compareAndSet(true, false)) return

        // Detect-the-document: if this capture is an ID card/passport, branch - extract
        // the largest face, skip the live-only frontal gate, tag provenance "id".
        if (Config.ID_DETECTION_ENABLED) {
            val a = try { engine.assessIdForEnroll(bitmap) } catch (_: Exception) { null }
            if (a != null && a.assessment.isId) {
                val emb = a.primaryEmbedding
                if (emb == null) {
                    failAttempt("low_quality",
                        "Detected an ID, but the photo on it is too unclear - try a clearer image or a live face.",
                        "Enrolment failed")
                    return
                }
                finishEnroll(engine.repo.enroll(enrollName, emb, source = "id"), fromId = true)
                return
            }
        }

        // Normal live path - needs a frontal pose.
        if (abs(yaw) > Config.LIVE_FRONTAL_YAW) {
            failAttempt("low_quality", "Look straight at the camera.", "Enrolment failed")
            return
        }
        val emb = engine.embed(bitmap, face)
        if (emb == null) {
            failAttempt("low_quality", "Could not read your face - hold still and try again.",
                "Enrolment failed")
            return
        }
        finishEnroll(engine.repo.enroll(enrollName, emb), fromId = false)
    }

    /** Palm verify - no head-turn (that's a face challenge); a single good palm
     *  capture matches against the palm store. Mirrors the server's palm path. */
    private suspend fun handlePalmVerify(p: PalmEngine, bitmap: Bitmap) {
        val s = p.embed(bitmap)
        if (s.embedding == null) {
            failAttempt(s.code, s.message, "Verification failed")
            return
        }
        val dec = p.repo.identify(s.embedding)
        p.repo.maybeAdapt(dec, s.embedding, null)
        endCapture()
        livenessProgress = 0f
        result = grantedResult(dec.granted, dec.userId, "Print not recognised").let {
            if (it.ok) it.copy(sub = it.sub.replaceFirst("Welcome, ${dec.userId}",
                "Welcome, ${dec.userId} (via print)")) else it
        }
    }

    /** Palm enrol - tap the shutter with an open palm shown (same 3-capture rhythm).
     *  Uses the STRICT anchor-quality gate: weak anchors degrade matching forever. */
    private suspend fun handlePalmEnroll(p: PalmEngine, bitmap: Bitmap) {
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        if (!captureRequested.compareAndSet(true, false)) return
        val s = p.embed(bitmap, forEnroll = true)
        if (s.embedding == null) {
            failAttempt(s.code, s.message, "Enrolment failed")
            return
        }
        finishPalmEnroll(p.repo.enroll(enrollName, s.embedding, handedness = s.handedness), s.embedding, s.handedness)
    }

    /** Palm enrol result handling: a different hand offers an "other hand" confirm; a
     *  same-side or third hand is refused; otherwise the shared success/progress path runs. */
    private fun finishPalmEnroll(r: com.faceverify.app.data.EnrollResult, emb: FloatArray, side: String) {
        when (r.code) {
            // Not failures to retry - each needs a decision from the operator, so the
            // attempt ends here and the dialog or verdict takes over.
            "different_hand" -> {
                endCapture()
                pendingHandEmb = emb; pendingHandSide = side; pendingOtherHand = r.message
            }
            "hands_full" -> { endCapture(); result = ScanResult(false, "Both hands enrolled", r.message) }
            "same_hand_side" -> { endCapture(); result = ScanResult(false, "Same hand again", r.message) }
            else -> finishEnroll(r, fromId = false)
        }
    }

    /** Admin confirmed the capture is the person's OTHER palm - bind it as hand two. */
    fun confirmOtherHand() {
        val emb = pendingHandEmb ?: return
        val p = palm ?: return
        val side = pendingHandSide
        pendingHandEmb = null; pendingHandSide = ""; pendingOtherHand = null
        if (!processing.compareAndSet(false, true)) return
        viewModelScope.launch(Dispatchers.Default) {
            try { finishEnroll(p.repo.enroll(enrollName, emb, hand = "other", handedness = side), fromId = false) }
            finally { processing.set(false) }
        }
    }

    fun cancelOtherHand() {
        pendingHandEmb = null; pendingOtherHand = null
        status = "Present the SAME hand you enrolled first."
    }

    private fun finishEnroll(r: com.faceverify.app.data.EnrollResult, fromId: Boolean) {
        if (!r.success) {
            // A refused enrolment (duplicate, wrong hand) is a decision, not a framing
            // problem - failAttempt keeps the retry rule in one place and shows it.
            failAttempt(r.code, r.message, "Enrolment failed")
            return
        }
        endCapture()
        captured = r.samples
        val idNote = if (fromId) " (from ID - add a live capture for best accuracy)" else ""
        val handNote = if (r.hand >= 2) " (other hand)" else ""
        if (captured >= enrollTarget) {
            val more = if (r.hand == 1) " - capture their OTHER hand too for either-hand verify, or you're done" else ""
            result = ScanResult(true, if (r.hand >= 2) "Other hand enrolled" else "Enrolled",
                "${enrollName.trim()} is ready to verify$idNote$more")
            refreshPeople()
        } else {
            status = "Captured $captured of $enrollTarget$handNote$idNote - tap the shutter again"
        }
    }

    /** Enrol from a photo the admin picked from the gallery (PIN-gated in the UI).
     *  Mirrors the web /api/enroll upload: ID cards auto-branch; a normal photo must
     *  be front-facing. No liveness (a still image can't perform a head turn), so this
     *  is enrolment-only - verification still requires the live head-turn challenge. */
    fun enrollFromPhoto(uri: Uri) {
        if (!ready) return
        if (enrollName.isBlank()) { status = "Enter a name first"; return }
        if (!processing.compareAndSet(false, true)) return
        viewModelScope.launch(Dispatchers.Default) {
            var bmp: Bitmap? = null
            try {
                bmp = decodeUri(uri)
                if (bmp == null) { result = ScanResult(false, "Couldn't open photo", "Try another image"); return@launch }
                if (isOnline) {
                    // The server does the same auto-routing (ID card, face, or palm) on
                    // an uploaded frame that it does for the web upload path.
                    val frame = ApiClient.dataUrl(bmp, RemoteBackend.FRAME_WIDTH, RemoteBackend.FRAME_QUALITY)
                    val outcome = remote?.enroll(enrollName, listOf(frame))
                    result = if (outcome?.success == true)
                        ScanResult(true, "Enrolled", "${enrollName.trim()} is ready to verify")
                    else ScanResult(false, "Enrolment failed",
                        outcome?.message?.ifBlank { "The server refused that photo." }
                            ?: "The server refused that photo.")
                    if (outcome?.success == true) refreshPeople()
                    return@launch
                }
                val a = engine.assessIdForEnroll(bmp)
                if (a.faces.isEmpty() || a.primaryFace == null) {
                    // No face - a gallery photo of a palm should enrol as palm
                    // (strict anchor-quality gate, same as live palm enrolment).
                    val p = palm
                    if (p != null) {
                        val s = p.embed(bmp, forEnroll = true)
                        if (s.embedding != null) {
                            finishPalmEnroll(p.repo.enroll(enrollName, s.embedding, handedness = s.handedness),
                                s.embedding, s.handedness); return@launch
                        }
                        result = ScanResult(false, "No face or hand found", s.message); return@launch
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
    // against the template inside the credential - fully offline.
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
            status = "No trust list on this device yet - add one in Settings"
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
            status = "Card OK (${p.name ?: p.subject}, issuer ${p.issuer}) - " +
                if ("face" in p.modalities) "now the person: turn your head slowly"
                else "now the person's open hand"
            return
        }

        // phase 2: live capture, matched INSIDE the credential's domain
        if ("face" in payload.modalities) {
            val face = engine.detect(bitmap)
            if (face == null || face.facepx < Config.MIN_FACE_PX) {
                status = "Show the person's face - turn the head slowly"
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
            ScanResult(true, "VERIFIED - ${p.name ?: p.subject}",
                "${p.subject} · issuer ${p.issuer} · $modality" +
                    if (attrs.isNotEmpty()) "\n$attrs" else "")
        } else {
            ScanResult(false, "NOT the credential holder",
                "The live $modality does not match this card.")
        }
        liveness.reset(); livenessProgress = 0f
    }

    // --- glance: continuous on-device 1:N (trust platform phase 3) -------------
    // Point the camera at people; each detected face is embedded, projected into
    // the index's protection domain, and brute-force matched against EVERY
    // enrolled identity - name chip in well under a second, fully offline.
    // Identification aid only (no liveness) - access decisions stay in Verify.
    var glanceHit by mutableStateOf<String?>(null); private set
    var glanceFace by mutableStateOf<com.faceverify.app.glance.GlanceIndex?>(null); private set
    var glancePalm by mutableStateOf<com.faceverify.app.glance.GlanceIndex?>(null); private set
    var glanceMsg by mutableStateOf(""); private set
    var glanceBusy by mutableStateOf(false); private set

    private fun localHit(dec: com.faceverify.app.face.Decision, floor: Float): Pair<String, Float>? =
        if (dec.granted && dec.userId != null && dec.score >= floor && dec.margin >= Config.GLANCE_MARGIN)
            dec.userId!! to dec.score else null

    private fun setGlance(hit: Pair<String, Float>?) {
        // Withdrawn consent / expired pass suppress identification here too -
        // naming someone IS processing their data (mirrors /api/glance).
        val visible = hit?.takeIf { serviceState?.hideFromGlance(it.first) != true }
        glanceHit = visible?.let { "${it.first}  (${"%.2f".format(it.second)})" }
        status = when {
            visible != null -> "Identified"
            hit != null -> "Seen - record paused (consent withdrawn or pass expired)"
            else -> "Seen - no confident match"
        }
    }

    private suspend fun handleGlanceFrame(bitmap: Bitmap) {
        // Face first (passive crowd scan), then an open PALM (deliberately presented).
        val face = engine.detect(bitmap)
        if (face != null && face.facepx >= Config.MIN_FACE_PX) {
            val emb = engine.embed(bitmap, face) ?: return
            val idx = glanceFace
            setGlance(if (idx != null && idx.count > 0)
                idx.decide(idx.search(idx.probeFor(emb)))?.let { it.userId to it.score }
            else localHit(engine.repo.identify(emb), Config.glanceFloor("face")))
            return
        }
        val p = palm
        if (p != null) {
            val s = p.embed(bitmap)
            if (s.embedding != null) {
                val idx = glancePalm
                setGlance(if (idx != null && idx.count > 0)
                    idx.decide(idx.search(idx.probeFor(s.embedding)))?.let { it.userId to it.score }
                else localHit(p.repo.identify(s.embedding), Config.glanceFloor("palm")))
                return
            }
        }
        glanceHit = null
        status = "Glance: point at a face - or hold up an open hand"
    }

    fun glanceSummary(): String {
        val parts = mutableListOf<String>()
        glanceFace?.let { parts.add("face: ${it.count}") }
        glancePalm?.takeIf { it.count > 0 }?.let { parts.add("print: ${it.count}") }
        if (parts.isEmpty()) return "not loaded (falls back to locally enrolled people)"
        val newest = listOfNotNull(glanceFace?.generated, glancePalm?.generated).maxOrNull() ?: 0L
        return parts.joinToString(" · ") + " identities · updated " +
            java.text.DateFormat.getDateTimeInstance().format(java.util.Date(newest * 1000))
    }

    private fun applyIndex(idx: com.faceverify.app.glance.GlanceIndex) {
        if (idx.modality == "palm") glancePalm = idx else glanceFace = idx
    }

    /** Hybrid: fetch this tenant's glance indexes (face + palm) from the server. */
    fun refreshGlanceIndex() {
        if (!syncPrefs.configured) { glanceMsg = "Set the server URL and API key first."; return }
        if (glanceBusy) return
        glanceBusy = true; glanceMsg = "Fetching…"
        viewModelScope.launch(Dispatchers.IO) {
            try {
                var got = 0
                for (m in listOf("face", "palm")) {
                    val resp = mgr()?.pullIndex(m) ?: continue
                    if (resp.optInt("count", 0) <= 0 && m == "palm") continue  // no palms enrolled
                    applyIndex(com.faceverify.app.glance.GlanceIndexStore.save(getApplication(), resp))
                    got++
                }
                glanceMsg = if (got > 0) "Glance index updated: ${glanceSummary()}." else "Nothing to sync."
            } catch (e: IllegalArgumentException) {
                glanceMsg = e.message ?: "Index rejected."
            } catch (e: Exception) {
                glanceMsg = "Fetch failed: ${e.message ?: "network error"}"
            } finally {
                glanceBusy = false
            }
        }
    }

    /** All builds (incl. air-gapped): import an encrypted glance-index file the admin
     *  exported (`POST /v1/export/glance-index`, face OR palm) and moved out-of-band. */
    fun importGlanceIndex(uri: Uri, passphrase: String) {
        if (passphrase.trim().length < 8) { glanceMsg = "Enter the file's passphrase (8+ chars)."; return }
        if (glanceBusy) return
        glanceBusy = true; glanceMsg = "Importing…"
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val bytes = getApplication<Application>().contentResolver
                    .openInputStream(uri)?.use { it.readBytes() }
                if (bytes == null) { glanceMsg = "Couldn't open that file."; return@launch }
                val payload = com.faceverify.app.data.BundleImporter.decryptToJson(bytes, passphrase)
                applyIndex(com.faceverify.app.glance.GlanceIndexStore.save(getApplication(), payload))
                glanceMsg = "Glance index imported: ${glanceSummary()}."
            } catch (e: com.faceverify.app.data.BundleImporter.BundleException) {
                glanceMsg = e.message ?: "Import failed."
            } catch (e: IllegalArgumentException) {
                glanceMsg = e.message ?: "Not a glance-index file."
            } catch (e: Exception) {
                glanceMsg = "Import failed - check the file and passphrase."
            } finally {
                glanceBusy = false
            }
        }
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
                trustMsg = "Import failed - is this a saved trust-store file?"
            } finally {
                trustBusy = false
            }
        }
    }

    // --- offline provisioning bundle (works on ALL builds, incl. air-gapped) --
    // Bulk-load templates the admin exported on the server, moved here out-of-band
    // (USB / MDM). No network is used - the file is decrypted locally with the same
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
                    " (${parsed.palm.size} print skipped - face-only build)" else ""
                bundleMsg = "Imported $f face + $pl print identities$skipped."
                refreshPeople()
            } catch (e: com.faceverify.app.data.BundleImporter.BundleException) {
                bundleMsg = e.message ?: "Import failed."
            } catch (e: Exception) {
                bundleMsg = "Import failed - check the file and passphrase."
            } finally {
                bundleBusy = false
            }
        }
    }

    // --- online settings (only meaningful when BuildConfig.ONLINE) ------------
    // Which deployment this device verifies against, and the admin password for the
    // endpoints that need one. Verifying never needs the password; enrolling needs it
    // only once the deployment closes open enrolment, and the People tab always does.
    var onlineMsg by mutableStateOf(""); private set
    var onlineBusy by mutableStateOf(false); private set

    fun onlineServerUrl(): String = onlinePrefs.serverUrl
    fun onlineAdminUser(): String = onlinePrefs.adminUser
    fun onlineAdminPasswordSet(): Boolean = onlinePrefs.hasAdminPassword

    /** Save, then - if a password was typed - actually try it against the server and
     *  say what happened. The screen never shows a stored password back (a secret you
     *  can read off a screen is not stored safely), so without this confirmation the
     *  field simply going blank looks exactly like the app discarding it. */
    fun saveOnlineConfig(url: String, username: String, password: String) {
        if (url.isNotBlank()) onlinePrefs.serverUrl = url
        onlinePrefs.adminUser = username
        if (password.isNotBlank()) onlinePrefs.adminPassword = password
        remote?.forgetSession()          // credentials changed - drop any stale session

        if (password.isBlank()) {
            onlineMsg = "Saved. Use Test to check the connection."
            return
        }
        onlineBusy = true
        onlineMsg = "Saved - checking the password…"
        viewModelScope.launch(Dispatchers.IO) {
            onlineMsg = try {
                if (remote?.checkAdminPassword(password, username) == true)
                    "Password accepted and stored on this device."
                else
                    "Stored, but the server refused it. Check the password" +
                        (if (username.isBlank()) " (and the operator name, if your deployment uses named operators)." else " for \"$username\".")
            } catch (e: Exception) {
                "Stored, but could not reach ${onlinePrefs.serverUrl} to check it."
            }
            onlineBusy = false
        }
    }

    fun clearOnlineAdminPassword() {
        onlinePrefs.clearAdminPassword()
        remote?.forgetSession()
        onlineMsg = "Admin password removed from this device."
    }

    /** Check the server is reachable, and report what it is configured for. */
    fun testOnline() {
        if (onlineBusy) return
        onlineBusy = true; onlineMsg = "Checking…"
        viewModelScope.launch(Dispatchers.IO) {
            onlineMsg = try {
                val h = remote?.health()
                if (h == null) "No client - restart the app."
                else if (!h.optBoolean("model_ready", false))
                    "Reached the server, but its recognition model is not loaded yet."
                else {
                    val enrol = when {
                        h.optBoolean("open_enroll", false) -> "enrolment is open"
                        onlinePrefs.hasAdminPassword -> "enrolment needs the admin password (stored)"
                        else -> "enrolment needs the admin password (NOT set)"
                    }
                    val turn = if (h.optBoolean("active_liveness", false))
                        "head-turn required" else "no head-turn"
                    "Connected - $enrol, $turn."
                }
            } catch (e: Exception) {
                "Could not reach ${onlinePrefs.serverUrl} - check the address and the connection."
            }
            onlineBusy = false
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
            if (r?.ok == true) {
                refreshPeople()
                // the service gates ride along with every successful sync, and a
                // paired device announces itself (console last-seen); both best-effort
                try {
                    serviceState = com.faceverify.app.data.ServiceState.save(
                        getApplication(), mgr()!!.pullServiceState())
                } catch (_: Exception) {}
                try { mgr()?.heartbeat("sync") } catch (_: Exception) {}
            }
            syncBusy = false
        }
    }

    // --- device pairing (this kiosk's own identity + key) ---------------------
    var pairBusy by mutableStateOf(false); private set
    var pairMsg by mutableStateOf(""); private set

    fun deviceLabel(): String =
        if (syncPrefs.paired) "'${syncPrefs.deviceName}' (${syncPrefs.deviceId})"
        else "not paired"

    fun pairDevice(code: String) {
        if (syncPrefs.serverUrl.isEmpty()) { pairMsg = "Set the server URL first."; return }
        if (code.isBlank()) { pairMsg = "Enter the pairing code from the console."; return }
        if (pairBusy) return
        pairBusy = true; pairMsg = "Pairing…"
        viewModelScope.launch(Dispatchers.IO) {
            val r = try { mgr()?.pairDevice(code) } catch (e: Exception) { null }
            pairMsg = r?.summary ?: "Pairing unavailable."
            pairBusy = false
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
