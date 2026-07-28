package com.faceverify.app.data

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.util.Calendar
import java.util.TimeZone

/** Offline mirror of the server's service state (hybrid build): access policies,
 *  guest expiries, consent standing, guardianship links. Pulled from
 *  `/v1/service-state` alongside sync and re-evaluated LOCALLY after every
 *  on-device match - same gate order as the server (guests -> consent ->
 *  policies), same typed codes, and gates only ever narrow a granted match.
 *
 *  Absent state (never synced, or a face-only airgapped build) means every gate
 *  passes - identical to the server's "mode off / no record" defaults, so a
 *  device without the mirror behaves exactly as before. */
class ServiceState private constructor(private val doc: JSONObject) {

    data class Gate(val code: String, val message: String)

    private val guests: JSONObject = doc.optJSONObject("guests") ?: JSONObject()
    private val withdrawn: Set<String> = stringSet("withdrawn")
    private val consented: Set<String> = stringSet("consented")
    private val enforceWithdrawal = doc.optBoolean("enforce_withdrawal", true)
    private val requireConsent = doc.optBoolean("require_consent", false)
    private val policies: JSONObject = doc.optJSONObject("policies") ?: JSONObject()
    private val guardians: JSONObject = doc.optJSONObject("guardians") ?: JSONObject()

    val generated: Long = doc.optLong("generated", 0L)

    private fun stringSet(key: String): Set<String> {
        val arr = doc.optJSONArray(key) ?: return emptySet()
        return (0 until arr.length()).map { arr.getString(it) }.toSet()
    }

    /** Post-match gates for a locally-granted verify of [userId]. Returns null
     *  when the person passes (the overwhelmingly common case). Order and codes
     *  mirror face_service (guests -> consent -> policies). */
    fun gate(userId: String, nowSeconds: Long = System.currentTimeMillis() / 1000): Gate? {
        val exp = guests.optLong(userId, 0L)
        if (exp > 0L && nowSeconds > exp) {
            return Gate("identity_expired", "Recognised, but this guest pass has expired.")
        }
        if (userId in withdrawn && enforceWithdrawal) {
            return Gate("consent_withdrawn", "Recognised, but consent was withdrawn - verification is paused.")
        }
        if (requireConsent && userId !in consented) {
            return Gate("consent_missing", "Recognised, but no consent is on record for this person.")
        }
        return evalPolicy(userId, nowSeconds)
    }

    /** True when identifying this person at a GLANCE should be suppressed
     *  (withdrawn consent / expired pass). Policies stay out of glance - it is
     *  an identification aid, not an access decision (mirrors /api/glance). */
    fun hideFromGlance(userId: String, nowSeconds: Long = System.currentTimeMillis() / 1000): Boolean {
        val exp = guests.optLong(userId, 0L)
        if (exp > 0L && nowSeconds > exp) return true
        return userId in withdrawn && enforceWithdrawal
    }

    /** Beneficiaries this verified person may act for ("may collect for: ...")
     *  - shown on a granted result, mirroring the server's `wards` field. */
    fun wardsOf(userId: String): List<String> {
        val out = mutableListOf<String>()
        for (b in guardians.keys()) {
            val links = guardians.optJSONArray(b) ?: continue
            for (i in 0 until links.length()) {
                if (links.getJSONObject(i).optString("guardian") == userId) out.add(b)
            }
        }
        return out.sorted()
    }

    // --- policy evaluation (mirrors face_service/policies.py exactly) ---------
    private fun evalPolicy(userId: String, nowSeconds: Long): Gate? {
        val mode = policies.optString("mode", "off")
        if (mode == "off") return null
        val groups = policies.optJSONObject("groups") ?: JSONObject()
        val rules = policies.optJSONArray("rules")
        var allowHit = false
        if (rules != null) {
            for (i in 0 until rules.length()) {
                val r = rules.getJSONObject(i)
                if (!r.optBoolean("enabled", true)) continue
                if (!subjectMatch(r, userId, groups)) continue
                if (!timeMatch(r, nowSeconds)) continue
                if (r.optString("effect") == "deny") {
                    return denied(mode, "denied by rule '${r.optString("name")}'")
                }
                allowHit = true
            }
        }
        if (allowHit) return null
        if (policies.optString("default", "allow") == "deny") {
            return denied(mode, "no rule matched - default deny")
        }
        return null
    }

    private fun denied(mode: String, reason: String): Gate? =
        // advise mode reports server-side; on a kiosk result there is nothing to
        // annotate without blocking, so only enforce flips the outcome (the
        // server remains the system of record for advise analytics).
        if (mode == "enforce") Gate("access_denied", "Recognised, but access is not permitted right now: $reason")
        else null

    private fun subjectMatch(rule: JSONObject, userId: String, groups: JSONObject): Boolean {
        val subs = rule.optJSONArray("subjects") ?: return false
        for (i in 0 until subs.length()) {
            val s = subs.getString(i)
            if (s == "*") return true
            if (s == "user:$userId") return true
            if (s.startsWith("group:")) {
                val members = groups.optJSONArray(s.substring(6)) ?: continue
                for (j in 0 until members.length()) {
                    if (members.getString(j) == userId) return true
                }
            }
        }
        return false
    }

    private fun timeMatch(rule: JSONObject, nowSeconds: Long): Boolean {
        val vf = rule.optLong("valid_from", 0L)
        if (vf > 0L && nowSeconds < vf) return false
        val vu = rule.optLong("valid_until", 0L)
        if (vu > 0L && nowSeconds > vu) return false

        val tzOffsetMin = policies.optInt("tz_offset_minutes", 0)
        val cal = Calendar.getInstance(TimeZone.getTimeZone("UTC"))
        cal.timeInMillis = (nowSeconds + tzOffsetMin * 60L) * 1000L
        val days = rule.optJSONArray("days")
        if (days != null && days.length() > 0) {
            // Calendar: SUNDAY=1..SATURDAY=7 -> mon..sun names (server's DAYS order)
            val names = arrayOf("sun", "mon", "tue", "wed", "thu", "fri", "sat")
            val today = names[cal.get(Calendar.DAY_OF_WEEK) - 1]
            var hit = false
            for (i in 0 until days.length()) if (days.getString(i) == today) { hit = true; break }
            if (!hit) return false
        }
        val start = rule.optString("start", "")
        val end = rule.optString("end", "")
        if (start.isNotEmpty() && end.isNotEmpty()) {
            val minutes = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)
            val s = start.substring(0, 2).toInt() * 60 + start.substring(3).toInt()
            val e = end.substring(0, 2).toInt() * 60 + end.substring(3).toInt()
            return if (s <= e) minutes in s..e         // same-day window
            else minutes >= s || minutes <= e          // wraps midnight (night shift)
        }
        return true
    }

    companion object {
        private const val FILE = "service_state.json"

        /** Parse + persist a fresh `/v1/service-state` payload. */
        fun save(context: Context, payload: JSONObject): ServiceState {
            File(context.filesDir, FILE).writeText(payload.toString())
            return ServiceState(payload)
        }

        /** The stored mirror, or null when never synced (all gates pass). */
        fun load(context: Context): ServiceState? = try {
            val f = File(context.filesDir, FILE)
            if (f.exists()) ServiceState(JSONObject(f.readText())) else null
        } catch (_: Exception) { null }

        fun clear(context: Context) { File(context.filesDir, FILE).delete() }
    }
}
