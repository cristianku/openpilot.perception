package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class TestQueueTest {
    @get:Rule val folder = TemporaryFolder()

    private fun upload(id: String, created: Double, tape: Double = 1.32) =
        Fixtures.upload.copy(clientId = id, createdUnix = created, tapeRadarM = tape)

    /** The site's side of POST /api/app/v1/tests: one test per client_id, whatever the number of tries. */
    private class FakeSite : TestUploader {
        val stored = linkedMapOf<String, SiteTest>()
        var calls = 0
        var reachable = true
        var signedIn = true
        var refuse = setOf<String>()
        /** Loses the reply of the next call after storing the test (as a dropped connection would). */
        var loseNextReply = false

        override fun upload(test: TestUpload): Uploaded {
            calls++
            if (!reachable) throw SiteProblem.Unreachable()
            if (!signedIn) throw SiteProblem.SignInNeeded()
            if (test.clientId in refuse) throw SiteProblem.Refused(400, "invalid_param", "The tape and the radar reading are more than 1 m apart.")
            val saved = stored.getOrPut(test.clientId) {
                SiteTest("id${stored.size}", test.clientId, test.target, test.capture.readingM, test.tapeRadarM, test.tapeCommaM,
                    test.point?.correctionM, true, test.createdUnix, test.comma.route)
            }
            if (loseNextReply) {
                loseNextReply = false
                throw SiteProblem.Unreachable()
            }
            return Uploaded(saved, SiteCalibration(1.5, -0.9, 0.6, "tests", stored.size, "close_only", 0.09, listOf(1.32)))
        }
    }

    @Test fun keepsTestsAcrossRestartsAndWritesAtomically() {
        val file = folder.root.resolve("tests.json")
        val queue = TestQueue(file) { 100.0 }
        queue.add(upload("a", 10.0))
        queue.add(upload("b", 20.0, tape = 10.0))
        val again = TestQueue(file)
        assertEquals(listOf("a", "b"), again.all().map { it.upload.clientId })
        assertEquals(Fixtures.upload.copy(clientId = "b", createdUnix = 20.0, tapeRadarM = 10.0), again.all()[1].upload)
        assertTrue(again.all().all { it.state == SyncState.LOCAL })
        assertTrue(folder.root.listFiles()!!.none { it.name.endsWith(".part") })
        try {
            queue.add(upload("a", 30.0))
            throw AssertionError("a second test with the same client_id")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test fun anUnreadableFileIsKeptAside() {
        val file = folder.root.resolve("tests.json")
        file.writeText("{ this is not json")
        val queue = TestQueue(file) { 1234.0 }
        assertEquals(emptyList<QueuedTest>(), queue.all())
        assertTrue(folder.root.resolve("tests.json.unreadable-1234").isFile)
        queue.add(upload("a", 1.0))
        assertEquals(1, TestQueue(file).all().size)
    }

    @Test fun sendsOldestFirstAndMarksEach() {
        val queue = TestQueue(folder.root.resolve("tests.json")) { 50.0 }
        queue.add(upload("new", 20.0))
        queue.add(upload("old", 10.0))
        queue.add(upload("bad", 15.0))
        val site = FakeSite().apply { refuse = setOf("bad") }
        val report = TestSync(queue, site).run()
        assertEquals(2, report.sent)
        assertEquals(1, report.refused)
        assertEquals(0, report.pending)
        assertNull(report.stoppedBy)
        assertEquals(listOf("old", "new"), site.stored.keys.toList())
        val byId = queue.all().associateBy { it.upload.clientId }
        // The reply alone is not enough: sent, waiting for the site's list.
        assertEquals(SyncState.SENT, byId.getValue("old").state)
        assertEquals("id0", byId.getValue("old").siteId)
        assertEquals(SyncState.REFUSED, byId.getValue("bad").state)
        assertEquals("The tape and the radar reading are more than 1 m apart.", byId.getValue("bad").reason)
        assertEquals(2, report.calibration!!.version)
        // Nothing more to send: the next round calls nobody.
        val calls = site.calls
        assertEquals(0, TestSync(queue, site).run().sent)
        assertEquals(calls, site.calls)
    }

    @Test fun offlineOrSignedOutStopsAndKeepsTheTests() {
        val queue = TestQueue(folder.root.resolve("tests.json")) { 60.0 }
        queue.add(upload("a", 1.0))
        queue.add(upload("b", 2.0))
        val site = FakeSite().apply { reachable = false }
        val offline = TestSync(queue, site).run()
        assertTrue(offline.stoppedBy is SiteProblem.Unreachable)
        assertEquals(2, offline.pending)
        assertEquals(1, site.calls)
        assertEquals(1, queue.all().first { it.upload.clientId == "a" }.attempts)
        assertEquals(60.0, queue.all().first { it.upload.clientId == "a" }.lastAttemptUnix!!, 0.0)

        site.reachable = true
        site.signedIn = false
        assertTrue(TestSync(queue, site).run().stoppedBy is SiteProblem.SignInNeeded)
        assertEquals(2, queue.pending().size)

        site.signedIn = true
        assertEquals(2, TestSync(queue, site).run().sent)
    }

    @Test fun aLostReplyIsSafeToSendAgain() {
        val queue = TestQueue(folder.root.resolve("tests.json"))
        queue.add(upload("a", 1.0))
        val site = FakeSite().apply { loseNextReply = true }
        assertTrue(TestSync(queue, site).run().stoppedBy is SiteProblem.Unreachable)
        assertEquals(SyncState.LOCAL, queue.all().single().state)
        assertEquals(1, TestSync(queue, site).run().sent)
        // Two calls, one test on the site: the client_id made the second one the same test.
        assertEquals(2, site.calls)
        assertEquals(1, site.stored.size)
        assertEquals(SyncState.SENT, queue.all().single().state)
        queue.confirm(site.stored.keys)
        assertEquals(SyncState.SYNCED, queue.all().single().state)
    }

    @Test fun onlyTheSiteListMakesATestSynced() {
        val queue = TestQueue(folder.root.resolve("tests.json"))
        queue.add(upload("kept", 1.0))
        queue.add(upload("lost", 2.0))
        // A site that answers 200 and keeps nothing (or a machine posing as it): never "on the site".
        val site = FakeSite()
        assertEquals(2, TestSync(queue, site).run().sent)
        queue.confirm(setOf("kept"))
        val byId = queue.all().associateBy { it.upload.clientId }
        assertEquals(SyncState.SYNCED, byId.getValue("kept").state)
        assertEquals(SyncState.LOCAL, byId.getValue("lost").state)
        // So it goes again, with the same client_id.
        assertEquals(listOf("lost"), queue.pending().map { it.upload.clientId })
        assertEquals(1, TestSync(queue, site).run().sent)
        // A synced test the list no longer has (deleted on the site) stays synced: it is not sent by itself.
        queue.confirm(setOf("lost"))
        assertEquals(SyncState.SYNCED, queue.all().first { it.upload.clientId == "kept" }.state)
        assertEquals(0, queue.pending().size)
        // The queue file keeps the states.
        assertEquals(queue.all(), TestQueue(folder.root.resolve("tests.json")).all())
    }

    @Test fun aRefusedTestCanBeSentAgainOrDeleted() {
        val queue = TestQueue(folder.root.resolve("tests.json"))
        queue.add(upload("a", 1.0))
        queue.add(upload("b", 2.0))
        val site = FakeSite().apply { refuse = setOf("a") }
        TestSync(queue, site).run()
        queue.retry("a")
        assertEquals(SyncState.LOCAL, queue.all().first { it.upload.clientId == "a" }.state)
        site.refuse = emptySet()
        assertEquals(1, TestSync(queue, site).run().sent)
        queue.remove("b")
        assertEquals(listOf("a"), queue.all().map { it.upload.clientId })
    }
}
