package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class ListenerProtocolTest {
    private val lines = Fixtures.text("listener_lines.txt").lines().filter { it.isNotBlank() }

    @Test fun readsTheHelloTheCommaSent() {
        val hello = ListenerProtocol.parse(lines[0]) as ListenerLine.Hello
        assertEquals(1, hello.version)
        assertEquals("1b6a472ae61d2d61866902cb48a5b3ab8bd11d07", hello.gitCommit)
        assertEquals("psa-torque-sunny-testing", hello.gitBranch)
        assertEquals("00000134--a5c8197883", hello.route)
        assertEquals("00000134--a5c8197883--0", hello.segment)
        assertEquals(29729, hello.pid)
        assertTrue(hello.radarFields)
        assertEquals(1200, hello.maxSeconds)
    }

    @Test fun readsStatusRadarByeAndError() {
        val status = ListenerProtocol.parse(lines[1]) as ListenerLine.Status
        assertFalse(status.onroad)
        assertEquals("00000134--a5c8197883", status.route)

        val radar = ListenerProtocol.parse(lines[2]) as ListenerLine.Radar
        assertEquals(123456789012345L, radar.monoNs)
        assertEquals(0.0, radar.vEgo!!, 0.0)
        assertTrue(radar.valid)
        assertEquals(2.25, radar.reading!!, 0.0)
        assertNull(radar.relSpeedMps)
        assertEquals(987654321L, radar.measuredNs)

        val none = ListenerProtocol.parse(lines[3]) as ListenerLine.Radar
        assertFalse(none.valid)
        assertNull(none.reading)
        assertNull(none.measuredNs)

        assertEquals(ListenerLine.Bye("time_limit"), ListenerProtocol.parse(lines[4]))
        assertTrue((ListenerProtocol.parse(lines[5]) as ListenerLine.Error).message.startsWith("openpilot messaging"))
    }

    @Test fun validWithoutADistanceIsNoReading() {
        val radar = ListenerProtocol.parse("""{"type":"radar","mono_ns":1,"onroad":true,"v_ego":0.0,"valid":true,"distance_m":null}""")
            as ListenerLine.Radar
        assertFalse(radar.valid)
        assertNull(radar.reading)
    }

    @Test fun skipsNoiseAndKeepsUnknownTypes() {
        assertNull(ListenerProtocol.parse(""))
        assertNull(ListenerProtocol.parse("Traceback (most recent call last):"))
        assertNull(ListenerProtocol.parse("{not json"))
        assertNull(ListenerProtocol.parse("""{"no_type":1}"""))
        assertEquals(ListenerLine.Unknown("future"), ListenerProtocol.parse("""{"type":"future","x":1}"""))
    }

    @Test fun refusesBadValues() {
        val hello = ListenerProtocol.parse("""{"type":"hello","version":1,"git_commit":"; rm -rf /","route":"../etc","segment":7}""")
            as ListenerLine.Hello
        assertNull(hello.gitCommit)
        assertNull(hello.route)
        assertNull(hello.segment)
        assertFalse(hello.radarFields)
        val radar = ListenerProtocol.parse("""{"type":"radar","v_ego":"fast","valid":true,"distance_m":"2.25"}""") as ListenerLine.Radar
        assertNull(radar.vEgo)
        assertFalse(radar.valid)
    }

    @Test fun commandIsTheOneOfTheListener() {
        assertEquals("cd /data/openpilot && exec nice -n 19 /usr/local/venv/bin/python -B -u -",
            ListenerProtocol.command("/data/openpilot", "/usr/local/venv/bin/python"))
        for (bad in listOf("/data/openpilot; reboot", "data/openpilot", "/data/open pilot", "/x\$(id)")) {
            try {
                ListenerProtocol.command(bad, "/usr/local/venv/bin/python")
                throw AssertionError("accepted $bad")
            } catch (_: IllegalArgumentException) {
            }
        }
    }

    /** The app bundles app/comma_listener.py at build time: the protocol version it speaks is the app's. */
    @Test fun theBundledListenerSpeaksThisVersion() {
        val script = File(System.getProperty("perception.listener") ?: "../../comma_listener.py").readText()
        assertTrue(Regex("(?m)^VERSION = ${ListenerProtocol.VERSION}$").containsMatchIn(script))
        for (type in listOf("hello", "radar", "status", "bye", "error")) assertTrue(type, script.contains("'type': '$type'"))
        assertTrue(script.contains("sys.dont_write_bytecode = True"))
    }
}
