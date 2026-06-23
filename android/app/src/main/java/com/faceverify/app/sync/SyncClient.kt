package com.faceverify.app.sync

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/** Tiny JSON-over-HTTPS client for the /v1 sync endpoints (HttpURLConnection + org.json,
 *  no extra dependencies). Used only by the hybrid build. */
class SyncClient(private val baseUrl: String, private val apiKey: String) {

    class HttpError(val code: Int, message: String) : Exception(message)

    private fun open(method: String, path: String): HttpURLConnection {
        val conn = URL(baseUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15000
        conn.readTimeout = 60000
        conn.setRequestProperty("X-API-Key", apiKey)
        conn.setRequestProperty("Accept", "application/json")
        return conn
    }

    private fun readBody(conn: HttpURLConnection): String {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        return stream?.bufferedReader()?.use(BufferedReader::readText) ?: ""
    }

    fun get(path: String): JSONObject {
        val conn = open("GET", path)
        try {
            val body = readBody(conn)
            if (conn.responseCode !in 200..299) throw error(conn.responseCode, body)
            return JSONObject(body)
        } finally { conn.disconnect() }
    }

    fun post(path: String, payload: JSONObject): JSONObject {
        val conn = open("POST", path)
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            val body = readBody(conn)
            if (conn.responseCode !in 200..299) throw error(conn.responseCode, body)
            return JSONObject(body)
        } finally { conn.disconnect() }
    }

    private fun error(code: Int, body: String): HttpError {
        val msg = try { JSONObject(body).optString("message", "") } catch (_: Exception) { "" }
        return HttpError(code, msg.ifEmpty { "HTTP $code" })
    }

    companion object {
        fun floatsToJson(v: FloatArray): JSONArray {
            val a = JSONArray()
            for (f in v) a.put(f.toDouble())
            return a
        }
        fun jsonToFloats(a: JSONArray): FloatArray =
            FloatArray(a.length()) { a.getDouble(it).toFloat() }
    }
}
