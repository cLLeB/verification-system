package com.faceverify.app.sync

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Encrypted local store for hybrid sync config: server URL + API key (the tenant is
 *  implicit in the key), plus the incremental watermark and last-sync status. Only the
 *  hybrid build writes here; the API key is sensitive so it's kept in EncryptedSharedPrefs. */
class SyncPrefs(context: Context) {
    private val prefs = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context, "faceverify_sync", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) { prefs.edit().putString("server_url", v.trim().trimEnd('/')).apply() }

    var apiKey: String
        get() = prefs.getString("api_key", "") ?: ""
        set(v) { prefs.edit().putString("api_key", v.trim()).apply() }

    /** Incremental pull watermark (server seq). Reset to 0 to re-pull everything. */
    var lastSeq: Long
        get() = prefs.getLong("last_seq", 0L)
        set(v) { prefs.edit().putLong("last_seq", v).apply() }

    var lastSyncMs: Long
        get() = prefs.getLong("last_sync_ms", 0L)
        set(v) { prefs.edit().putLong("last_sync_ms", v).apply() }

    var lastMsg: String
        get() = prefs.getString("last_msg", "") ?: ""
        set(v) { prefs.edit().putString("last_msg", v).apply() }

    val configured: Boolean get() = serverUrl.isNotEmpty() && apiKey.isNotEmpty()

    /** Forget the watermark so the next pull re-downloads the whole dataset. */
    fun resetWatermark() { lastSeq = 0L }
}
