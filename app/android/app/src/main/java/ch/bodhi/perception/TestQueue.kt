package ch.bodhi.perception

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/** Where a test made on this phone is. */
enum class SyncState(val wire: String) {
    /** On the phone only: it goes to the site when the phone reaches it. */
    LOCAL("local"),
    /** The site answered that it took it; it counts as on the site once the site's own list shows it (a reply
     * alone proves nothing on plain HTTP). Not in the next list: sent again (the site takes a client_id once). */
    SENT("sent"),
    /** The site's list showed it. */
    SYNCED("synced"),
    /** The site refused it and said why: it stays on the phone until the owner sends it again or deletes it. */
    REFUSED("refused");

    companion object {
        fun of(wire: String?): SyncState = entries.firstOrNull { it.wire == wire } ?: LOCAL
    }
}

data class QueuedTest(
    val upload: TestUpload,
    val state: SyncState = SyncState.LOCAL,
    val reason: String = "",
    val siteId: String? = null,
    val attempts: Int = 0,
    val lastAttemptUnix: Double? = null
)

/** The tests made on this phone, in one small JSON file of the app's private storage, written atomically
 * (a new file next to it, synced to disk, then renamed over the old one): a test saved in the garage survives
 * the app being killed or the battery running out. A JSON file rather than SQLite: a few dozen tests, read
 * whole, one writer, and the same tests run on the JVM without Android. A file that cannot be read is kept
 * aside (tests.json.unreadable-<time>), never overwritten. */
class TestQueue(private val file: File, private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 }) {
    private val lock = Any()
    private var cache: List<QueuedTest>? = null

    fun all(): List<QueuedTest> = synchronized(lock) { load() }

    fun pending(): List<QueuedTest> = all().filter { it.state == SyncState.LOCAL }.sortedBy { it.upload.createdUnix }

    fun add(upload: TestUpload) = update { tests ->
        require(tests.none { it.upload.clientId == upload.clientId }) { "this test is already saved" }
        tests + QueuedTest(upload)
    }

    fun remove(clientId: String) = update { tests -> tests.filterNot { it.upload.clientId == clientId } }

    /** A test sent again: refused before (after the site was fixed, or the tapes were right after all), or no
     * longer on the site. */
    fun retry(clientId: String) = change(clientId) { it.copy(state = SyncState.LOCAL, reason = "") }

    fun markSent(clientId: String, siteId: String?) =
        change(clientId) { it.copy(state = SyncState.SENT, reason = "", siteId = siteId, lastAttemptUnix = clock()) }

    /** The site's list (GET /api/app/v1/tests) read after the sending: a sent test it shows is on the site, one it
     * does not show goes again. A synced test the list no longer has stays as it is (deleted on the site: the
     * owner sends it again if he wants it back). */
    fun confirm(siteClientIds: Set<String>) {
        if (all().none { it.state == SyncState.SENT }) return
        update { tests ->
            tests.map {
                when {
                    it.state != SyncState.SENT -> it
                    it.upload.clientId in siteClientIds -> it.copy(state = SyncState.SYNCED)
                    else -> it.copy(state = SyncState.LOCAL)
                }
            }
        }
    }

    fun markRefused(clientId: String, reason: String) =
        change(clientId) { it.copy(state = SyncState.REFUSED, reason = reason, attempts = it.attempts + 1, lastAttemptUnix = clock()) }

    fun markTried(clientId: String) =
        change(clientId) { it.copy(attempts = it.attempts + 1, lastAttemptUnix = clock()) }

    private fun change(clientId: String, how: (QueuedTest) -> QueuedTest) = update { tests ->
        tests.map { if (it.upload.clientId == clientId) how(it) else it }
    }

    private fun update(how: (List<QueuedTest>) -> List<QueuedTest>) {
        synchronized(lock) {
            val next = how(load())
            write(next)
            cache = next
        }
    }

    private fun load(): List<QueuedTest> {
        cache?.let { return it }
        if (!file.exists()) return emptyList<QueuedTest>().also { cache = it }
        val loaded = try {
            decode(file.readText(Charsets.UTF_8))
        } catch (error: Exception) {
            // Never lose tests to a bad file: keep it aside for the owner, start a new one.
            val aside = File(file.parentFile, "${file.name}.unreadable-${clock().toLong()}")
            if (!file.renameTo(aside)) throw IOException("the tests file cannot be read or moved aside", error)
            emptyList()
        }
        cache = loaded
        return loaded
    }

    private fun write(tests: List<QueuedTest>) {
        file.parentFile?.mkdirs()
        val part = File(file.parentFile, "${file.name}.part")
        FileOutputStream(part).use { out ->
            out.write(encode(tests).toByteArray(Charsets.UTF_8))
            out.fd.sync()
        }
        if (!part.renameTo(file)) {
            part.delete()
            throw IOException("the tests file could not be written")
        }
    }

    companion object {
        const val FORMAT = 1

        fun encode(tests: List<QueuedTest>): String {
            val list = JSONArray()
            tests.forEach { test ->
                list.put(JSONObject()
                    .put("upload", SiteApi.testBody(test.upload))
                    .put("steady_s", test.upload.capture.steadyS)
                    .put("state", test.state.wire)
                    .put("reason", test.reason)
                    .put("site_id", test.siteId ?: JSONObject.NULL)
                    .put("attempts", test.attempts)
                    .put("last_attempt_unix", test.lastAttemptUnix ?: JSONObject.NULL))
            }
            return JSONObject().put("format", FORMAT).put("tests", list).toString(1)
        }

        fun decode(text: String): List<QueuedTest> {
            val json = JSONObject(text)
            if (json.optInt("format", 0) != FORMAT) throw JSONException("unknown format")
            val list = json.getJSONArray("tests")
            return (0 until list.length()).map { index ->
                val entry = list.getJSONObject(index)
                QueuedTest(
                    upload = SiteApi.parseUpload(entry.getJSONObject("upload"), entry.optDouble("steady_s", 0.0)),
                    state = SyncState.of(entry.optString("state")),
                    reason = entry.optString("reason", ""),
                    siteId = if (entry.isNull("site_id")) null else entry.optString("site_id"),
                    attempts = entry.optInt("attempts", 0),
                    lastAttemptUnix = if (entry.isNull("last_attempt_unix")) null else entry.optDouble("last_attempt_unix"))
            }
        }
    }
}

/** What one round of sending gave. */
data class SyncReport(
    val sent: Int,
    val refused: Int,
    val pending: Int,
    /** Why the round stopped before the end (signed out, site gone), null when every test was tried. */
    val stoppedBy: SiteProblem?,
    /** The site's calibration after the last test it took. */
    val calibration: SiteCalibration?
)

/** Sends the tests on the phone to the site, oldest first, each with its client_id: the site takes a test once
 * whatever the number of tries, so a reply lost on the way is safe to try again. A refusal is kept with its
 * reason; signed out or the site out of reach stops the round, and the tests wait for the next. A test the site
 * took is SENT until its list shows it ([TestQueue.confirm]). */
class TestSync(private val queue: TestQueue, private val uploader: TestUploader) {
    fun run(): SyncReport {
        var sent = 0
        var refused = 0
        var calibration: SiteCalibration? = null
        var stoppedBy: SiteProblem? = null
        for (test in queue.pending()) {
            val id = test.upload.clientId
            try {
                val uploaded = uploader.upload(test.upload)
                queue.markSent(id, uploaded.test.id)
                uploaded.calibration?.let { calibration = it }
                sent++
            } catch (problem: SiteProblem) {
                when {
                    problem is SiteProblem.Refused -> {
                        queue.markRefused(id, problem.message ?: "refused")
                        refused++
                    }
                    else -> {
                        // Signed out, site gone, busy, not the site: nothing is lost, the next round tries again.
                        queue.markTried(id)
                        stoppedBy = problem
                        break
                    }
                }
            }
        }
        return SyncReport(sent, refused, queue.pending().size, stoppedBy, calibration)
    }
}
