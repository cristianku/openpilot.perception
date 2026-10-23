package ch.bodhi.perception

import org.json.JSONArray
import org.json.JSONObject

/** The JSON fixtures of src/test/resources/fixtures: answers of the site exactly as the app contract has them. */
object Fixtures {
    fun text(name: String): String =
        requireNotNull(javaClass.classLoader!!.getResource("fixtures/$name")) { "no fixture $name" }.readText()

    fun json(name: String) = JSONObject(text(name))

    fun keys(json: JSONObject): Set<String> = json.keys().asSequence().toSet()

    /** Deep equality of JSON values: same keys, same order of arrays, numbers equal as numbers. */
    fun same(a: Any?, b: Any?): Boolean = when {
        a is JSONObject && b is JSONObject -> keys(a) == keys(b) && keys(a).all { same(a.opt(it), b.opt(it)) }
        a is JSONArray && b is JSONArray -> a.length() == b.length() && (0 until a.length()).all { same(a.opt(it), b.opt(it)) }
        a is Number && b is Number -> a.toDouble() == b.toDouble()
        a == null || a == JSONObject.NULL -> b == null || b == JSONObject.NULL
        else -> a == b
    }

    /** The upload the fixture test_upload_body.json is the body of. */
    val upload = TestUpload(
        clientId = "6f1c1f9e-6c7e-4c64-9f43-1d1a9b0e2a11",
        createdUnix = 1792771200.5,
        target = Target.WALL,
        tapeRadarM = 1.32,
        tapeCommaM = 2.82,
        capture = CaptureSummary(startedUnix = 1792771170.0, durationS = 20.0, samples = 200, withReading = 180,
            readingM = 2.25, steadyShare = 0.98, histogram = sortedMapOf("2.25" to 176, "2.50" to 4), speedMaxMps = 0.0,
            onroad = true, steadyS = 17.6),
        comma = CommaRef("00000134--a5c8197883", "1b6a472a"),
        note = "")
}
