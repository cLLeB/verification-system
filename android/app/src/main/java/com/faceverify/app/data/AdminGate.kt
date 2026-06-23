package com.faceverify.app.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.MessageDigest

/** A local PIN that gates enrolment (verification stays open). Stored as a salted
 *  hash inside EncryptedSharedPreferences. First enrolment sets the PIN. */
class AdminGate(context: Context) {
    private val prefs = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context, "faceverify_admin", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun isSet(): Boolean = prefs.contains("pin")

    fun setPin(pin: String) {
        prefs.edit().putString("pin", hash(pin)).apply()
    }

    fun check(pin: String): Boolean = isSet() && prefs.getString("pin", null) == hash(pin)

    private fun hash(pin: String): String {
        val d = MessageDigest.getInstance("SHA-256").digest(("fv|$pin").toByteArray())
        return d.joinToString("") { "%02x".format(it) }
    }
}
