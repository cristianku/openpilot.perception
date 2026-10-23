package ch.bodhi.perception

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException

// The contract between this app and perception-web, all in this file so it is easy to adjust:
//   POST /api/auth/login {"user", "password"}        -> 200, Set-Cookie perception_web_<port> (the session)
//   POST /api/auth/logout                            -> 200
//   GET  /api/auth/state                             -> {"configured", "signed_in"} (no session needed)
//   GET  /api/app/v1                                 -> {"ok", "api": "perception-app", "version": 1, "code",
//                                                        "calibration": {...}, "comma": {...}, "label_range_m"}
//   POST /api/app/v1/tests {client_id, created_unix, target, tape_radar_m, tape_comma_m, capture, comma, note}
//                                                    -> {"ok", "test", "calibration"}; idempotent on client_id
//   GET  /api/app/v1/tests                           -> {"ok", "tests": [...]}
//   POST /api/app/v1/comma-key {"public_key"}        -> {"ok", "added", "already"}; 503 comma_unreachable,
//                                                        403 not_allowed
// Errors: {"ok": false, "error": <code>, "message": <sentence>}; 401 means signed out.

/** The car's calibration as the site keeps it (web/radar.py calibration_view). */
data class SiteCalibration(
    val radarToCameraM: Double?,
    val radarCorrectionM: Double?,
    val radarToReferenceM: Double?,
    val source: String?,
    val version: Int,
    val status: String,
    val uncertaintyM: Double?,
    val testedM: List<Double>
) {
    /** The values to turn a reading into a distance from the comma; null while the site has none. */
    val values: CalibrationValues?
        get() = radarToCameraM?.let { CalibrationValues(it, radarCorrectionM ?: 0.0) }
}

/** How to reach the comma: its SSH host keys as the site knows them, and where openpilot and its Python are. */
data class SiteComma(val hostKeyFingerprints: List<String>, val python: String, val openpilot: String) {
    companion object {
        val DEFAULT = SiteComma(emptyList(), "/usr/local/venv/bin/python", "/data/openpilot")
    }
}

data class SiteInfo(val code: String, val calibration: SiteCalibration?, val comma: SiteComma,
                    val labelRangeM: ClosedFloatingPointRange<Double>)

/** A test as the site lists it. Fields the site may not send are null. */
data class SiteTest(val id: String?, val clientId: String?, val target: Target, val readingM: Double?,
                    val tapeRadarM: Double?, val tapeCommaM: Double?, val correctionM: Double?, val kept: Boolean,
                    val createdUnix: Double?, val route: String?) {
    val point: TapePoint?
        get() = if (readingM != null && tapeRadarM != null && tapeCommaM != null) TapePoint(readingM, tapeRadarM, tapeCommaM) else null
}

/** The recording and the openpilot commit of the comma during the capture (from the listener). */
data class CommaRef(val route: String?, val gitCommit: String?)

/** A test made on this phone, exactly as POST /api/app/v1/tests takes it. */
data class TestUpload(
    val clientId: String,
    val createdUnix: Double,
    val target: Target,
    val tapeRadarM: Double,
    val tapeCommaM: Double,
    val capture: CaptureSummary,
    val comma: CommaRef,
    val note: String
) {
    val point: TapePoint? get() = capture.readingM?.let { TapePoint(it, tapeRadarM, tapeCommaM) }
}

data class Uploaded(val test: SiteTest, val calibration: SiteCalibration?)
data class CommaKeyResult(val added: Boolean, val already: Boolean)
data class AuthState(val configured: Boolean, val signedIn: Boolean)

/** Why a call to the site did not work. [temporary] problems are tried again later; the others need someone. */
sealed class SiteProblem(message: String, val temporary: Boolean) : Exception(message) {
    class Unreachable(cause: Throwable? = null) :
        SiteProblem("The site cannot be reached from here.", true) {
        init {
            if (cause != null) initCause(cause)
        }
    }
    class SignInNeeded(message: String = "Sign in to the site first.") : SiteProblem(message, false)
    class NoAppApi : SiteProblem("This site has no app API yet: update the site.", false)
    class NotTheSite(message: String = "No perception site answers at this address.") : SiteProblem(message, false)
    class TooNew(version: Int) : SiteProblem("The site speaks a newer app API ($version): update the app.", false)
    class Refused(val status: Int, val code: String, message: String) : SiteProblem(message, false)
    class Unavailable(val status: Int, val code: String, message: String) : SiteProblem(message, true)
}

/** Sends one test; [SiteApi] in the app, a fake in the tests. */
fun interface TestUploader {
    fun upload(test: TestUpload): Uploaded
}

/** The site's API for the app. Blocking: call it off the main thread. Every request goes to [base] only. */
class SiteApi(val base: String, private val transport: HttpTransport, private val cookies: SessionCookies) : TestUploader {
    init {
        require(SiteAddress.parse(base) == base) { "not a site address the app may use" }
    }

    /** Needs no session, and is sent without it: it also tells whether the site answers on a network the owner
     * has not used it from yet. */
    fun authState(): AuthState {
        val json = call("GET", "/api/auth/state", null, session = false)
        if (!json.has("configured") || !json.has("signed_in")) throw SiteProblem.NotTheSite()
        return AuthState(json.optBoolean("configured"), json.optBoolean("signed_in"))
    }

    /** The session cookie goes to the WebView's store, so the site is signed in too. */
    fun signIn(user: String, password: String) {
        val reply = send("POST", "/api/auth/login", JSONObject().put("user", user).put("password", password).toString(), session = false)
        if (reply.status != 200) throw problemOf(reply, "/api/auth/login")
        if (reply.setCookies.isEmpty()) throw SiteProblem.NotTheSite("The site did not open a session.")
        cookies.store(base, reply.setCookies)
    }

    /** Ends the session on the site too (best effort), then forgets it here. */
    fun signOut() {
        try {
            send("POST", "/api/auth/logout", "{}")
        } catch (_: IOException) {
        } catch (_: SiteProblem) {
        }
        cookies.clear()
    }

    /** GET /api/app/v1, and its raw text to keep for offline use. */
    fun info(): Pair<SiteInfo, String> {
        val reply = send("GET", APP_PATH, null)
        if (reply.status == 404) throw SiteProblem.NoAppApi()
        if (reply.status != 200) throw problemOf(reply, APP_PATH)
        val json = parseObject(reply.body)
        return parseInfo(json) to reply.body
    }

    override fun upload(test: TestUpload): Uploaded {
        val json = call("POST", "$APP_PATH/tests", testBody(test).toString())
        val siteTest = json.optJSONObject("test")?.let(::parseTest) ?: throw SiteProblem.NotTheSite("The site did not return the test.")
        return Uploaded(siteTest, json.optJSONObject("calibration")?.let(::parseCalibration))
    }

    /** GET /api/app/v1/tests, and its raw text to keep. */
    fun tests(): Pair<List<SiteTest>, String> {
        val reply = send("GET", "$APP_PATH/tests", null)
        if (reply.status != 200) throw problemOf(reply, "$APP_PATH/tests")
        return parseTests(parseObject(reply.body)) to reply.body
    }

    /** The one-tap way: the site adds this phone's public key to the comma's allowed SSH keys. */
    fun addCommaKey(publicKey: String): CommaKeyResult {
        val json = call("POST", "$APP_PATH/comma-key", JSONObject().put("public_key", publicKey).toString())
        return CommaKeyResult(json.optBoolean("added"), json.optBoolean("already"))
    }

    private fun call(method: String, path: String, body: String?, session: Boolean = true): JSONObject {
        val reply = send(method, path, body, session)
        if (reply.status != 200) throw problemOf(reply, path)
        return parseObject(reply.body)
    }

    private fun send(method: String, path: String, body: String?, session: Boolean = true): HttpReply {
        val url = base + path
        check(SiteAddress.sameOrigin(url, base))
        return try {
            transport.exchange(method, url, if (session) cookies.header(base) else null, body)
        } catch (error: IOException) {
            throw SiteProblem.Unreachable(error)
        }
    }

    companion object {
        const val API_NAME = "perception-app"
        const val API_VERSION = 1
        const val APP_PATH = "/api/app/v1"
        private val FINGERPRINT = Regex("^SHA256:[A-Za-z0-9+/]{43}$")
        private val PYTHON_NAME = Regex("^python(3(\\.[0-9]{1,2})?)?$")

        fun parseObject(text: String): JSONObject = try {
            JSONObject(text)
        } catch (_: JSONException) {
            throw SiteProblem.NotTheSite()
        }

        /** An error answer: {"ok": false, "error", "message"}. */
        fun problemOf(reply: HttpReply, path: String): SiteProblem {
            val json = try {
                JSONObject(reply.body)
            } catch (_: JSONException) {
                null
            }
            val code = json?.optString("error").orEmpty()
            val message = json?.optString("message").orEmpty()
            return when {
                reply.status == 401 -> if (message.isBlank()) SiteProblem.SignInNeeded() else SiteProblem.SignInNeeded(message)
                reply.status == 404 && path == APP_PATH -> SiteProblem.NoAppApi()
                json == null && reply.status in 200..499 -> SiteProblem.NotTheSite()
                reply.status >= 500 || reply.status == 408 || reply.status == 429 ->
                    SiteProblem.Unavailable(reply.status, code, message.ifBlank { "The site is busy (HTTP ${reply.status}): later." })
                else -> SiteProblem.Refused(reply.status, code, message.ifBlank { "The site refused it (HTTP ${reply.status})." })
            }
        }

        fun parseInfo(json: JSONObject): SiteInfo {
            if (json.optString("api") != API_NAME) throw SiteProblem.NotTheSite("This is not the perception site's app API.")
            val version = json.optInt("version", 0)
            if (version > API_VERSION) throw SiteProblem.TooNew(version)
            if (version < 1) throw SiteProblem.NotTheSite("This is not the perception site's app API.")
            val comma = json.optJSONObject("comma")
            val fingerprints = (comma?.optJSONArray("host_key_fingerprints") ?: JSONArray()).strings()
                .filter { FINGERPRINT.matches(it) }
            // The program the listener runs on the comma: a plain path to a Python interpreter, nothing else.
            val python = comma?.text("python")
                ?.takeIf { ListenerProtocol.SAFE_PATH.matches(it) && PYTHON_NAME.matches(it.substringAfterLast('/')) }
                ?: SiteComma.DEFAULT.python
            val openpilot = comma?.text("openpilot")?.takeIf { ListenerProtocol.SAFE_PATH.matches(it) } ?: SiteComma.DEFAULT.openpilot
            val range = json.optJSONArray("label_range_m")
            val low = range?.optDouble(0, Double.NaN) ?: Double.NaN
            val high = range?.optDouble(1, Double.NaN) ?: Double.NaN
            return SiteInfo(
                code = json.optString("code", ""),
                calibration = json.optJSONObject("calibration")?.let(::parseCalibration),
                comma = SiteComma(fingerprints, python, openpilot),
                labelRangeM = if (low.isFinite() && high.isFinite() && low < high) low..high else 5.0..30.0)
        }

        fun parseCalibration(json: JSONObject): SiteCalibration = SiteCalibration(
            radarToCameraM = json.number("radar_to_camera_m"),
            radarCorrectionM = json.number("radar_correction_m"),
            radarToReferenceM = json.number("radar_to_reference_m"),
            source = json.text("source"),
            version = json.optInt("version", 0),
            status = json.text("status") ?: "not_set",
            uncertaintyM = json.number("uncertainty_m"),
            testedM = (json.optJSONArray("tested_m") ?: JSONArray()).numbers())

        fun parseTest(json: JSONObject): SiteTest = SiteTest(
            id = json.text("id"),
            clientId = json.text("client_id"),
            target = Target.of(json.text("target")),
            readingM = json.number("reading_m"),
            tapeRadarM = json.number("tape_radar_m"),
            tapeCommaM = json.number("tape_comma_m"),
            correctionM = json.number("correction_m"),
            kept = json.opt("kept") != false,
            createdUnix = json.number("created") ?: json.number("created_unix"),
            route = json.text("route"))

        fun parseTests(json: JSONObject): List<SiteTest> =
            (json.optJSONArray("tests") ?: JSONArray()).objects().map(::parseTest)

        /** The body of POST /api/app/v1/tests: exactly the keys of the contract. */
        fun testBody(test: TestUpload): JSONObject {
            val capture = test.capture
            val histogram = JSONObject()
            capture.histogram.forEach { (key, count) -> histogram.put(key, count) }
            return JSONObject()
                .put("client_id", test.clientId)
                .put("created_unix", test.createdUnix)
                .put("target", test.target.wire)
                .put("tape_radar_m", test.tapeRadarM)
                .put("tape_comma_m", test.tapeCommaM)
                .put("capture", JSONObject()
                    .put("started_unix", capture.startedUnix)
                    .put("duration_s", capture.durationS)
                    .put("samples", capture.samples)
                    .put("with_reading", capture.withReading)
                    .put("reading_m", capture.readingM ?: JSONObject.NULL)
                    .put("steady_share", capture.steadyShare ?: JSONObject.NULL)
                    .put("histogram", histogram)
                    .put("speed_max_mps", capture.speedMaxMps ?: JSONObject.NULL)
                    .put("onroad", capture.onroad))
                .put("comma", JSONObject()
                    .put("route", test.comma.route ?: JSONObject.NULL)
                    .put("git_commit", test.comma.gitCommit ?: JSONObject.NULL))
                .put("note", test.note)
        }

        /** [testBody] read back (the phone's queue keeps tests in this form). steady_s is the phone's own. */
        fun parseUpload(json: JSONObject, steadyS: Double = 0.0): TestUpload {
            val capture = json.getJSONObject("capture")
            val histogram = mutableMapOf<String, Int>()
            capture.optJSONObject("histogram")?.let { map -> map.keys().forEach { key -> histogram[key] = map.optInt(key) } }
            val comma = json.optJSONObject("comma")
            return TestUpload(
                clientId = json.getString("client_id"),
                createdUnix = json.getDouble("created_unix"),
                target = Target.of(json.optString("target")),
                tapeRadarM = json.getDouble("tape_radar_m"),
                tapeCommaM = json.getDouble("tape_comma_m"),
                capture = CaptureSummary(
                    startedUnix = capture.getDouble("started_unix"),
                    durationS = capture.getDouble("duration_s"),
                    samples = capture.getInt("samples"),
                    withReading = capture.getInt("with_reading"),
                    readingM = capture.number("reading_m"),
                    steadyShare = capture.number("steady_share"),
                    histogram = histogram.toSortedMap(),
                    speedMaxMps = capture.number("speed_max_mps"),
                    onroad = capture.optBoolean("onroad"),
                    steadyS = steadyS),
                comma = CommaRef(comma?.text("route"), comma?.text("git_commit")),
                note = json.optString("note", ""))
        }

        private fun JSONObject.text(key: String): String? = if (!has(key) || isNull(key)) null else (opt(key) as? String)

        private fun JSONObject.number(key: String): Double? {
            if (!has(key) || isNull(key)) return null
            val value = opt(key)
            return if (value is Number) value.toDouble().takeIf { it.isFinite() } else null
        }

        private fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
        private fun JSONArray.strings(): List<String> = (0 until length()).mapNotNull { opt(it) as? String }
        private fun JSONArray.numbers(): List<Double> =
            (0 until length()).mapNotNull { (opt(it) as? Number)?.toDouble()?.takeIf { v -> v.isFinite() } }
    }
}
