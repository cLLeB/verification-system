package com.faceverify.app.online

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.faceverify.app.BuildConfig

/** Encrypted store for the ONLINE build's server settings: which deployment to talk to
 *  and (optionally) the admin password used for the endpoints that need one.
 *
 *  Separate from [com.faceverify.app.sync.SyncPrefs] on purpose. Sync is a hybrid
 *  concept - an API key that mirrors a tenant's templates for ON-DEVICE matching. This
 *  is the opposite arrangement: no templates ever land here, the server does the
 *  matching, and the credential involved is an admin password rather than a tenant key.
 *  Sharing one store would blur two different trust relationships. */
class OnlinePrefs(context: Context) {
    private val prefs = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context, "faceverify_online", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    /** The deployment this device verifies against. Prefilled with the build's default
     *  so the app works the moment it is installed, with no setup step. */
    var serverUrl: String
        get() = prefs.getString("server_url", null) ?: BuildConfig.DEFAULT_SERVER_URL
        set(v) { prefs.edit().putString("server_url", v.trim().trimEnd('/')).apply() }

    /** Admin password, stored only if the operator chooses to. Verifying never needs
     *  it; enrolling needs it once the deployment turns FACE_OPEN_ENROLL off, and the
     *  People tab always needs it. Blank means "not provided". */
    var adminPassword: String
        get() = prefs.getString("admin_password", "") ?: ""
        set(v) { prefs.edit().putString("admin_password", v).apply() }

    /** Which operator account the password belongs to. Deployments that never created
     *  named operators use the built-in "admin", which is why that is the default. */
    var adminUser: String
        get() = prefs.getString("admin_user", "") ?: ""
        set(v) { prefs.edit().putString("admin_user", v.trim()).apply() }

    val hasAdminPassword: Boolean get() = adminPassword.isNotEmpty()

    fun clearAdminPassword() { prefs.edit().remove("admin_password").apply() }
}
