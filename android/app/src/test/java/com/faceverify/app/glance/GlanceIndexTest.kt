package com.faceverify.app.glance

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** Golden glance-index test: the Kotlin search must reproduce the server's
 *  reference search (face_service/glance.py) on a real payload — same top hit,
 *  same score, margin-gated decisions, clamped threshold. If this fails, the
 *  on-device 1:N disagrees with the server — do not ship. */
class GlanceIndexTest {

    private val fx: JSONObject by lazy {
        JSONObject(javaClass.classLoader!!.getResourceAsStream("glance_fixture.json")!!
            .readBytes().toString(Charsets.UTF_8))
    }

    private fun floats(key: String): FloatArray {
        val a = fx.getJSONArray(key)
        return FloatArray(a.length()) { a.getDouble(it).toFloat() }
    }

    private fun index(): GlanceIndex = GlanceIndex.parse(fx.getJSONObject("payload"))

    @Test
    fun matchesServerReferenceSearch() {
        val idx = index()
        assertEquals(20, idx.count)
        // raw probe in, device projects with the payload's domain seed
        val hits = idx.search(idx.probeFor(floats("holder_probe_raw")))
        assertEquals(fx.getString("expected_top"), hits[0].userId)
        assertTrue(
            "score ${hits[0].score} vs server ${fx.getDouble("expected_top_score")}",
            abs(hits[0].score - fx.getDouble("expected_top_score").toFloat()) < 1e-3f,
        )
        val dec = idx.decide(hits)
        assertNotNull(dec)
        assertEquals(fx.getString("expected_top"), dec!!.userId)
    }

    @Test
    fun strangerIsRejected() {
        val idx = index()
        val hits = idx.search(idx.probeFor(floats("stranger_probe_raw")))
        assertTrue(abs(hits[0].score - fx.getDouble("stranger_top_score").toFloat()) < 1e-3f)
        assertNull(idx.decide(hits))
    }

    @Test
    fun thresholdIsClampedToTheFloorBand() {
        val loose = JSONObject(fx.getJSONObject("payload").toString())
        loose.put("threshold", 0.05)                       // hostile/bad calibration
        assertEquals(com.faceverify.app.Config.GLANCE_MIN_THRESHOLD,
            GlanceIndex.parse(loose).threshold)
        loose.put("threshold", 0.99)
        assertEquals(com.faceverify.app.Config.GLANCE_MIN_THRESHOLD +
            com.faceverify.app.Config.GLANCE_CLAMP_BAND,
            GlanceIndex.parse(loose).threshold)
    }

    @Test
    fun rejectsMalformedPayloads() {
        val bad = JSONObject(fx.getJSONObject("payload").toString())
        bad.put("format", "something-else")
        try {
            GlanceIndex.parse(bad)
            throw AssertionError("wrong format must not parse")
        } catch (e: IllegalArgumentException) { /* expected */ }
        val short = JSONObject(fx.getJSONObject("payload").toString())
        short.put("count", 999)
        try {
            GlanceIndex.parse(short)
            throw AssertionError("size mismatch must not parse")
        } catch (e: IllegalArgumentException) { /* expected */ }
    }
}
