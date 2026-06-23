package com.faceverify.app.sync

import com.faceverify.app.data.FaceRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** Orchestrates pull (server → device mirror) and push (device → server) for the hybrid
 *  build. Pull is incremental by the server's seq watermark and applies deletions; push
 *  surfaces cross-identity duplicate conflicts reported by the server. */
class SyncManager(private val repo: FaceRepository, private val prefs: SyncPrefs) {

    data class Result(val ok: Boolean, val summary: String, val conflicts: List<String> = emptyList())

    private fun client() = SyncClient(prefs.serverUrl, prefs.apiKey)

    /** Confirm the server URL + key work (and report whether sync export is enabled). */
    suspend fun test(): Result = withContext(Dispatchers.IO) {
        try {
            val u = client().get("/v1/usage")            // any authenticated endpoint
            Result(true, "Connected. Tenant: ${u.optString("tenant", "?")}")
        } catch (e: SyncClient.HttpError) {
            Result(false, "Failed (${e.code}): ${e.message}")
        } catch (e: Exception) {
            Result(false, "Failed: ${e.message ?: "network error"}")
        }
    }

    /** Pull the tenant's templates into the local mirror, incrementally. */
    suspend fun pull(): Result = withContext(Dispatchers.IO) {
        try {
            var since = prefs.lastSeq
            var applied = 0
            var deleted = 0
            while (true) {
                val resp = client().get("/v1/sync/pull?since=$since&limit=500")
                val arr = resp.getJSONArray("templates")
                for (i in 0 until arr.length()) {
                    val t = arr.getJSONObject(i)
                    val uid = t.getString("user_id")
                    if (t.optBoolean("deleted", false)) {
                        repo.delete(uid); deleted++
                    } else {
                        val embsJson = t.getJSONArray("embeddings")
                        val embs = (0 until embsJson.length()).map {
                            SyncClient.jsonToFloats(embsJson.getJSONArray(it))
                        }
                        repo.replaceUser(uid, embs, source = "synced"); applied++
                    }
                }
                since = resp.getLong("next_seq")
                prefs.lastSeq = since
                if (resp.optBoolean("done", true)) break
            }
            prefs.lastSyncMs = System.currentTimeMillis()
            val msg = "Pulled $applied, removed $deleted (up to seq $since)."
            prefs.lastMsg = msg
            Result(true, msg)
        } catch (e: SyncClient.HttpError) {
            val hint = if (e.code == 403) " — ask the provider to enable template export for your account." else ""
            Result(false, "Pull failed (${e.code}): ${e.message}$hint")
        } catch (e: Exception) {
            Result(false, "Pull failed: ${e.message ?: "network error"}")
        }
    }

    /** Push device templates up. [selected] = null pushes everyone. [onConflict] = how the
     *  server resolves a face that matches an existing, differently-named person. */
    suspend fun push(selected: Set<String>? = null, onConflict: String = "skip"): Result =
        withContext(Dispatchers.IO) {
            try {
                val templates = JSONArray()
                for ((uid, embs) in repo.exportTemplates(selected)) {
                    val embArr = JSONArray()
                    for (e in embs) embArr.put(SyncClient.floatsToJson(e))
                    templates.put(JSONObject().put("user_id", uid).put("embeddings", embArr))
                }
                if (templates.length() == 0) return@withContext Result(false, "Nothing to push.")
                val payload = JSONObject().put("templates", templates).put("on_conflict", onConflict)
                val resp = client().post("/v1/sync/push", payload)
                val conflicts = mutableListOf<String>()
                val ca = resp.optJSONArray("conflicts") ?: JSONArray()
                for (i in 0 until ca.length()) {
                    val c = ca.getJSONObject(i)
                    conflicts.add("${c.optString("user_id")} ↔ ${c.optString("matched")} " +
                        "(${c.optDouble("score")}) — ${c.optString("action")}")
                }
                prefs.lastSyncMs = System.currentTimeMillis()
                val msg = "Pushed ${resp.optInt("pushed")}, merged ${resp.optInt("merged")}, " +
                    "skipped ${resp.optInt("skipped")}."
                prefs.lastMsg = msg
                Result(true, msg, conflicts)
            } catch (e: SyncClient.HttpError) {
                Result(false, "Push failed (${e.code}): ${e.message}")
            } catch (e: Exception) {
                Result(false, "Push failed: ${e.message ?: "network error"}")
            }
        }
}
