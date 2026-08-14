package com.faceverify.app.online

import android.graphics.Bitmap
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

/** JSON-over-HTTPS client for the first-party `/api/` endpoints the web verifier uses
 *  (HttpURLConnection + org.json, no extra dependencies - same approach as SyncClient).
 *
 *  Holds the admin session cookie in memory so the endpoints that require a login
 *  (People, and enrolment once the deployment closes open-enrol) keep working across
 *  calls without re-sending the password every time. The cookie is never persisted:
 *  a restart re-logs in from the stored password, and a device that was never given
 *  one simply cannot reach those endpoints. */
class ApiClient(private val baseUrl: String) {

    class HttpError(val code: Int, message: String) : Exception(message)

    /** Set when a call needs a login the client does not have. The UI turns this into
     *  "add the admin password in Settings" rather than a raw 401. */
    class AuthRequired(message: String) : Exception(message)

    /** Every cookie the server has set, by name.
     *
     *  It used to keep only a cookie literally called `session=`, which is Flask's
     *  DEFAULT signed-session name - but this server does not use it. `/admin/login`
     *  issues `face_admin` (face_service/admin.py), so the one cookie that mattered was
     *  the one being thrown away: login answered `{"success": true}`, the retry went out
     *  with no credential, came back 401, and the People tab said "add the admin
     *  password in Settings" no matter how many times you added it. Keeping the jar by
     *  name means a rename or an extra cookie on the server cannot break it again. */
    private val cookies = java.util.concurrent.ConcurrentHashMap<String, String>()

    private fun open(method: String, path: String, timeoutMs: Int): HttpURLConnection {
        val conn = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = timeoutMs
        conn.readTimeout = timeoutMs
        conn.setRequestProperty("Accept", "application/json")
        if (cookies.isNotEmpty()) {
            conn.setRequestProperty("Cookie", cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
        }
        return conn
    }

    private fun readBody(conn: HttpURLConnection): String {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        return stream?.bufferedReader()?.use(BufferedReader::readText) ?: ""
    }

    /** Keep whatever cookies the server hands back, whatever they are called.
     *  A `Set-Cookie` with an empty value (or an expiry in the past) is the server
     *  clearing one - drop it rather than sending an empty credential back. */
    private fun captureCookie(conn: HttpURLConnection) {
        val headers = conn.headerFields ?: return
        val set = headers.entries.firstOrNull { it.key?.equals("Set-Cookie", true) == true }?.value
            ?: return
        for (line in set) {
            val pair = line.substringBefore(';')
            val name = pair.substringBefore('=').trim()
            val value = pair.substringAfter('=', "").trim()
            if (name.isEmpty()) continue
            if (value.isEmpty()) cookies.remove(name) else cookies[name] = value
        }
    }

    fun get(path: String, timeoutMs: Int = 20_000): JSONObject {
        val conn = open("GET", path, timeoutMs)
        try {
            val body = readBody(conn)
            captureCookie(conn)
            if (conn.responseCode == 401 || conn.responseCode == 403) {
                throw AuthRequired("This needs the admin password - add it in Settings.")
            }
            if (conn.responseCode !in 200..299) throw HttpError(conn.responseCode, body.take(300))
            return JSONObject(body)
        } finally { conn.disconnect() }
    }

    /** GET an endpoint that returns a bare JSON array (e.g. /api/users). */
    fun getArray(path: String, timeoutMs: Int = 20_000): JSONArray {
        val conn = open("GET", path, timeoutMs)
        try {
            val body = readBody(conn)
            captureCookie(conn)
            if (conn.responseCode == 401 || conn.responseCode == 403) {
                throw AuthRequired("This needs the admin password - add it in Settings.")
            }
            if (conn.responseCode !in 200..299) throw HttpError(conn.responseCode, body.take(300))
            return JSONArray(body)
        } finally { conn.disconnect() }
    }

    fun post(path: String, payload: JSONObject, timeoutMs: Int = 45_000): JSONObject {
        val conn = open("POST", path, timeoutMs)
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val body = readBody(conn)
            captureCookie(conn)
            if (conn.responseCode == 401 || conn.responseCode == 403) {
                throw AuthRequired("This needs the admin password - add it in Settings.")
            }
            // A 4xx with a JSON body is a real answer (a refused enrolment, say), not a
            // transport failure - hand it back so the caller can show its message.
            if (conn.responseCode !in 200..299 && body.isBlank()) {
                throw HttpError(conn.responseCode, "Server returned ${conn.responseCode}")
            }
            return JSONObject(body)
        } finally { conn.disconnect() }
    }

    /** Exchange the admin credentials for a session cookie. Safe to call repeatedly.
     *
     *  [username] is sent explicitly: the server falls back to "admin" for a blank one,
     *  but a deployment that has created named operator accounts stops accepting the
     *  bootstrap admin altogether, and then the name is the only way in. */
    fun login(password: String, username: String = "admin"): Boolean {
        if (password.isEmpty()) return false
        return try {
            val body = JSONObject().put("username", username.ifBlank { "admin" }).put("password", password)
            val r = post("/admin/login", body, 20_000)
            val ok = r.optBoolean("success", false)
            if (!ok) cookies.clear()
            ok
        } catch (_: Exception) {
            cookies.clear()
            false
        }
    }

    fun forgetSession() = cookies.clear()

    companion object {
        /** Encode a frame the way the web client does: a JPEG data URL. The server's
         *  decode_image accepts the prefix or bare base64; sending the same shape as the
         *  browser keeps one format on the wire across both clients. */
        fun dataUrl(bitmap: Bitmap, maxWidth: Int, quality: Int): String {
            val scaled = if (bitmap.width > maxWidth) {
                val h = (bitmap.height.toLong() * maxWidth / bitmap.width).toInt().coerceAtLeast(1)
                Bitmap.createScaledBitmap(bitmap, maxWidth, h, true)
            } else bitmap
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
            if (scaled !== bitmap && !scaled.isRecycled) scaled.recycle()
            val b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
            return "data:image/jpeg;base64,$b64"
        }
    }
}
