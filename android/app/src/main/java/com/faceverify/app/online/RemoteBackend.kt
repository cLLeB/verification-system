package com.faceverify.app.online

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** The online build's enrol/verify path: the server decides, this device only captures.
 *
 *  It talks to the same `/api/` endpoints as the web verifier, sends the same frame
 *  bursts, and gets back the same decision objects - so a person enrolled from a phone
 *  and a person enrolled from a browser are the same record, matched by the same model
 *  at the same threshold. Nothing is stored on the device and no template ever reaches
 *  it, which is the whole point of this flavor.
 *
 *  Framing guidance does NOT come through here - the bundled detectors do that locally
 *  (see [com.faceverify.app.capture.CaptureCoach]), so the chips stay instant and the
 *  network is used only for decisions. */
class RemoteBackend(private val prefs: OnlinePrefs) {

    /** A decision from the server, flattened to what the UI needs. */
    data class Outcome(
        val success: Boolean,
        val code: String,
        val message: String,
        val userId: String?,
        val score: Float,
        val modality: String,
    )

    private var client = ApiClient(prefs.serverUrl)
    private var clientUrl = prefs.serverUrl

    /** Rebuild the client if the operator changed the server in Settings. */
    private fun api(): ApiClient {
        if (clientUrl != prefs.serverUrl) {
            client = ApiClient(prefs.serverUrl)
            clientUrl = prefs.serverUrl
        }
        return client
    }

    /** Log in if a password is stored. Called before the endpoints that need one; a
     *  no-op when the deployment still has open enrolment. */
    private fun ensureAdmin(): Boolean {
        val pw = prefs.adminPassword
        return if (pw.isEmpty()) false else api().login(pw)
    }

    private fun outcome(o: JSONObject): Outcome = Outcome(
        success = o.optBoolean("success", false),
        code = o.optString("code", ""),
        message = o.optString("message", ""),
        userId = o.optString("user_id", "").ifEmpty { null },
        score = o.optDouble("score", 0.0).toFloat(),
        modality = o.optString("modality", ""),
    )

    /** Is the deployment reachable, and what is it configured for? */
    suspend fun health(): JSONObject = withContext(Dispatchers.IO) { api().get("/api/health", 15_000) }

    /** A head-turn challenge token, or null when the deployment has active liveness
     *  off (in which case verify takes a single frame and no token). */
    suspend fun challenge(): String? = withContext(Dispatchers.IO) {
        val r = try { api().get("/api/challenge", 15_000) } catch (_: Exception) { return@withContext null }
        if (!r.optBoolean("active", false)) null else r.optString("token", "").ifEmpty { null }
    }

    /** Verify a captured burst. [token] is the head-turn challenge for a face; a palm
     *  burst needs none (the server routes it and picks the sharpest frame). A blank
     *  [userId] asks for open 1:N identify, which the deployment may refuse. */
    suspend fun verify(userId: String, frames: List<String>, token: String?): Outcome =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("frames", JSONArray(frames))
            if (userId.isNotBlank()) body.put("user_id", userId.trim())
            if (token != null) body.put("token", token)
            outcome(api().post("/api/verify", body))
        }

    /** Enrol a captured burst under [userId]. [hand] is "auto", or "other" to bind a
     *  person's SECOND palm under a name that already has one. */
    suspend fun enroll(userId: String, frames: List<String>, hand: String = "auto"): Outcome =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("user_id", userId.trim())
                .put("frames", JSONArray(frames))
                .put("hand", hand)
            try {
                outcome(api().post("/api/enroll", body))
            } catch (e: ApiClient.AuthRequired) {
                // The deployment has closed open enrolment - log in and try once more.
                if (!ensureAdmin()) throw e
                outcome(api().post("/api/enroll", body))
            }
        }

    /** Everyone enrolled on the server (face + palm union). Needs the admin password. */
    suspend fun listUsers(): List<String> = withContext(Dispatchers.IO) {
        val fetch = { api().getArray("/api/users") }
        val arr: JSONArray = try {
            fetch()
        } catch (e: ApiClient.AuthRequired) {
            if (!ensureAdmin()) throw e
            fetch()
        }
        (0 until arr.length()).mapNotNull { i ->
            when (val v = arr.opt(i)) {
                is String -> v
                is JSONObject -> v.optString("user_id", "").ifEmpty { null }
                else -> null
            }
        }.sorted()
    }

    /** Remove someone from the server. Needs the admin password. */
    suspend fun delete(userId: String): Outcome = withContext(Dispatchers.IO) {
        val body = JSONObject().put("user_id", userId)
        try {
            outcome(api().post("/api/users/delete", body))
        } catch (e: ApiClient.AuthRequired) {
            if (!ensureAdmin()) throw e
            outcome(api().post("/api/users/delete", body))
        }
    }

    /** Verify the stored admin password against the server, for the Settings screen. */
    suspend fun checkAdminPassword(password: String): Boolean = withContext(Dispatchers.IO) {
        api().login(password)
    }

    fun forgetSession() = api().forgetSession()

    companion object {
        /** Frames posted for a decision are downscaled before they leave the device.
         *  640px keeps a face well above the embedder's minimum while keeping a burst
         *  of 11 frames small enough to send over a phone connection. */
        const val FRAME_WIDTH = 640
        const val FRAME_QUALITY = 82

        /** A palm burst: short and steady, no head-turn (that is a face challenge).
         *  Matches the web client's palmVerify. */
        const val PALM_BURST_FRAMES = 3
        const val PALM_BURST_GAP_MS = 220L
        const val PALM_SETTLE_MS = 450L
    }
}
