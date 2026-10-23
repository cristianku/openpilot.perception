package ch.bodhi.perception

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class SiteApiTest {
    private class Request(val method: String, val url: String, val cookie: String?, val body: String?)

    /** Answers by "METHOD /path"; records what the app sent. */
    private class FakeTransport(val replies: Map<String, HttpReply>) : HttpTransport {
        val requests = mutableListOf<Request>()
        var down = false
        override fun exchange(method: String, url: String, cookie: String?, jsonBody: String?): HttpReply {
            requests += Request(method, url, cookie, jsonBody)
            if (down) throw IOException("no route to host")
            return replies["$method ${url.substringAfter("8767")}"] ?: HttpReply(404, """{"ok": false, "error": "not_found", "message": "Not found."}""")
        }
    }

    private class MemoryCookies : SessionCookies {
        var value: String? = null
        override fun header(base: String) = value
        override fun store(base: String, setCookies: List<String>) {
            value = setCookies.first().substringBefore(';')
        }
        override fun clear() {
            value = null
        }
    }

    private val base = "http://10.10.10.213:8767"
    private fun ok(name: String) = HttpReply(200, Fixtures.text(name))

    @Test fun readsTheAppInfo() {
        val (info, raw) = SiteApi(base, FakeTransport(mapOf("GET /api/app/v1" to ok("app_v1.json"))), MemoryCookies()).info()
        assertEquals("1ccb3fb", info.code)
        val calibration = info.calibration!!
        assertEquals(1.5, calibration.radarToCameraM!!, 0.0)
        assertEquals(-0.89, calibration.radarCorrectionM!!, 0.0)
        assertEquals(0.61, calibration.radarToReferenceM!!, 0.0)
        assertEquals("tests", calibration.source)
        assertEquals(3, calibration.version)
        assertEquals("close_only", calibration.status)
        assertEquals(0.09, calibration.uncertaintyM!!, 0.0)
        assertEquals(listOf(0.9, 1.32), calibration.testedM)
        assertEquals(CalibrationValues(1.5, -0.89), calibration.values)
        assertEquals(listOf("SHA256:VmJ38dgdv0FQ7zoRV/9NCsqcmY6OpUPux0CXtxd9YxM"), info.comma.hostKeyFingerprints)
        assertEquals("/usr/local/venv/bin/python", info.comma.python)
        assertEquals("/data/openpilot", info.comma.openpilot)
        assertEquals(5.0..30.0, info.labelRangeM)
        // What is cached is what the site sent.
        assertTrue(Fixtures.same(Fixtures.json("app_v1.json"), JSONObject(raw)))
    }

    @Test fun aSiteWithoutCalibrationShowsRawReadings() {
        val info = SiteApi.parseInfo(Fixtures.json("app_v1_not_set.json"))
        assertEquals("not_set", info.calibration!!.status)
        assertNull(info.calibration!!.values)
        assertTrue(info.comma.hostKeyFingerprints.isEmpty())
    }

    @Test fun refusesWhatIsNotThisApi() {
        val newer = Fixtures.json("app_v1.json").put("version", 2)
        assertTrue(runCatching { SiteApi.parseInfo(newer) }.exceptionOrNull() is SiteProblem.TooNew)
        val other = Fixtures.json("app_v1.json").put("api", "distill")
        assertTrue(runCatching { SiteApi.parseInfo(other) }.exceptionOrNull() is SiteProblem.NotTheSite)
        // Paths from the site go into a shell command on the comma: only plain absolute paths.
        val odd = Fixtures.json("app_v1.json")
        odd.getJSONObject("comma").put("python", "/usr/bin/python; reboot").put("host_key_fingerprints", listOf("MD5:aa", 3))
        val info = SiteApi.parseInfo(odd)
        assertEquals(SiteComma.DEFAULT.python, info.comma.python)
        // A plain path, but to another program than Python: not run on the comma.
        odd.getJSONObject("comma").put("python", "/sbin/reboot")
        assertEquals(SiteComma.DEFAULT.python, SiteApi.parseInfo(odd).comma.python)
        odd.getJSONObject("comma").put("python", "/usr/bin/python3.12")
        assertEquals("/usr/bin/python3.12", SiteApi.parseInfo(odd).comma.python)
        assertTrue(info.comma.hostKeyFingerprints.isEmpty())
        val api = SiteApi(base, FakeTransport(emptyMap()), MemoryCookies())
        assertTrue(runCatching { api.info() }.exceptionOrNull() is SiteProblem.NoAppApi)
    }

    @Test fun theTestBodyIsExactlyTheContract() {
        val body = SiteApi.testBody(Fixtures.upload)
        val expected = Fixtures.json("test_upload_body.json")
        assertTrue("$body", Fixtures.same(expected, body))
        assertEquals(Fixtures.keys(expected), Fixtures.keys(body))
        assertEquals(Fixtures.keys(expected.getJSONObject("capture")), Fixtures.keys(body.getJSONObject("capture")))
        // And it reads back the same (the queue on the phone keeps this form).
        assertEquals(Fixtures.upload, SiteApi.parseUpload(body, steadyS = 17.6))
    }

    @Test fun uploadsWithTheSessionAndReadsTheReply() {
        val transport = FakeTransport(mapOf("POST /api/app/v1/tests" to ok("test_post_reply.json")))
        val cookies = MemoryCookies().apply { value = "perception_web_8767=abc" }
        val uploaded = SiteApi(base, transport, cookies).upload(Fixtures.upload)
        val request = transport.requests.single()
        assertEquals("POST", request.method)
        assertEquals("$base/api/app/v1/tests", request.url)
        assertEquals("perception_web_8767=abc", request.cookie)
        assertTrue(Fixtures.same(Fixtures.json("test_upload_body.json"), JSONObject(request.body!!)))
        assertEquals("a1b2c3d4e5f6", uploaded.test.id)
        assertEquals(Fixtures.upload.clientId, uploaded.test.clientId)
        assertEquals(-0.93, uploaded.test.correctionM!!, 0.0)
        assertEquals(4, uploaded.calibration!!.version)
        assertEquals(-0.9, uploaded.calibration!!.radarCorrectionM!!, 0.0)
    }

    @Test fun listsTheSiteTests() {
        val (tests, _) = SiteApi(base, FakeTransport(mapOf("GET /api/app/v1/tests" to ok("tests_list.json"))), MemoryCookies()).tests()
        assertEquals(3, tests.size)
        assertNull(tests[0].clientId)
        assertEquals(Fixtures.upload.clientId, tests[1].clientId)
        assertEquals(Target.CAR, tests[2].target)
        assertFalse(tests[2].kept)
        assertEquals(TapePoint(1.75, 0.9, 2.4), tests[0].point)
    }

    @Test fun errorsBecomeProblems() {
        fun problem(status: Int, fixture: String, path: String = "/api/app/v1/tests") =
            SiteApi.problemOf(HttpReply(status, Fixtures.text(fixture)), path)
        val invalid = problem(400, "error_invalid_param.json") as SiteProblem.Refused
        assertEquals("invalid_param", invalid.code)
        assertEquals("The comma sits behind the radar: its distance must be the larger.", invalid.message)
        assertFalse(invalid.temporary)
        assertTrue(problem(401, "error_login_required.json") is SiteProblem.SignInNeeded)
        val unreachable = problem(503, "comma_key_unreachable.json", "/api/app/v1/comma-key") as SiteProblem.Unavailable
        assertEquals("comma_unreachable", unreachable.code)
        assertTrue(unreachable.temporary)
        val notAllowed = problem(403, "comma_key_not_allowed.json", "/api/app/v1/comma-key") as SiteProblem.Refused
        assertEquals("not_allowed", notAllowed.code)
        assertTrue(SiteApi.problemOf(HttpReply(409, """{"ok": false, "error": "conflict", "message": "Already there."}"""), "/x") is SiteProblem.Refused)
        assertTrue(SiteApi.problemOf(HttpReply(502, "<html>bad gateway</html>"), "/x") is SiteProblem.Unavailable)
        assertTrue(SiteApi.problemOf(HttpReply(200, "<html>a router page</html>"), "/x") is SiteProblem.NotTheSite)
        val down = FakeTransport(emptyMap()).apply { this.down = true }
        assertTrue(runCatching { SiteApi(base, down, MemoryCookies()).info() }.exceptionOrNull() is SiteProblem.Unreachable)
    }

    @Test fun signInKeepsTheCookieAndTheKeyGoesToTheComma() {
        val transport = FakeTransport(mapOf(
            "GET /api/auth/state" to ok("auth_state.json"),
            "POST /api/auth/login" to HttpReply(200, """{"ok": true}""",
                listOf("perception_web_8767=s3cr3t; Path=/; HttpOnly; SameSite=Strict")),
            "POST /api/app/v1/comma-key" to ok("comma_key_added.json")))
        val cookies = MemoryCookies().apply { value = "perception_web_8767=old" }
        val api = SiteApi(base, transport, cookies)
        assertEquals(AuthState(configured = true, signedIn = false), api.authState())
        api.signIn("owner", "pass word")
        assertEquals("perception_web_8767=s3cr3t", cookies.value)
        // Neither needs the session, and neither carries it.
        assertNull(transport.requests[0].cookie)
        assertNull(transport.requests[1].cookie)
        val login = transport.requests[1]
        assertEquals(JSONObject().put("user", "owner").put("password", "pass word").toString(), login.body)
        val key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea perception-app@Pixel-8"
        assertEquals(CommaKeyResult(added = true, already = false), api.addCommaKey(key))
        assertEquals(key, JSONObject(transport.requests.last().body!!).getString("public_key"))
        assertEquals("perception_web_8767=s3cr3t", transport.requests.last().cookie)
    }

    @Test fun withoutSessionKeepsTheCookieBack() {
        val transport = FakeTransport(mapOf("GET /api/app/v1" to ok("app_v1.json"),
            "POST /api/auth/login" to HttpReply(200, """{"ok": true}""", listOf("perception_web_8767=new; HttpOnly"))))
        val cookies = MemoryCookies().apply { value = "perception_web_8767=abc" }
        val api = SiteApi(base, transport, WithoutSession(cookies))
        api.info()
        assertNull(transport.requests.single().cookie)
        // A sign-in there still keeps its new session.
        api.signIn("owner", "pw")
        assertEquals("perception_web_8767=new", cookies.value)
    }

    @Test fun onlyAPrivateAddressOverPlainHttp() {
        try {
            SiteApi("http://example.com:8767", FakeTransport(emptyMap()), MemoryCookies())
            throw AssertionError("plain HTTP to a public host")
        } catch (_: IllegalArgumentException) {
        }
    }
}
