package ch.bodhi.perception

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections

class CommaFinderTest {
    private val comma = "SHA256:VmJ38dgdv0FQ7zoRV/9NCsqcmY6OpUPux0CXtxd9YxM"
    private val other = "SHA256:bbXpuKG6zhzdmnxq256TlqzFBzRl2f6OOg722cYNbU8"

    /** A network where [ssh] hosts run sshd with those keys and [open] hosts only have port 22 open. */
    private class FakeNetwork(val ssh: Map<String, String>, val open: Set<String> = emptySet()) : CommaConnector {
        val probed: MutableList<String> = Collections.synchronizedList(mutableListOf())
        val keyAsked: MutableList<String> = Collections.synchronizedList(mutableListOf())
        override suspend fun portOpen(host: String, port: Int, timeoutMs: Int, subnet: LocalSubnet?): Boolean {
            assertEquals(22, port)
            probed += host
            return host in ssh || host in open
        }

        override suspend fun hostKey(host: String, port: Int, subnet: LocalSubnet?): String? {
            keyAsked += host
            return ssh[host]
        }
    }

    private fun subnet(address: String, prefix: Int = 24, kind: LocalSubnet.Kind = LocalSubnet.Kind.HOTSPOT, name: String = "ap0") =
        LocalSubnet(name, kind, Ipv4.parse(address)!!, prefix)

    @Test fun theHostsOfASubnet() {
        val hosts = subnet("192.168.43.1").hosts().map(Ipv4::format)
        assertEquals(253, hosts.size)
        assertEquals("192.168.43.2", hosts.first())
        assertEquals("192.168.43.254", hosts.last())
        assertFalse("192.168.43.1" in hosts)
        // A /16 is cut to the /24 around the phone; a /28 stays a /28.
        val wide = subnet("10.20.30.40", 16).hosts().map(Ipv4::format)
        assertEquals("10.20.30.1", wide.first())
        assertEquals(253, wide.size)
        assertEquals(13, subnet("192.168.1.18", 28).hosts().size)
        // Never a public network.
        assertTrue(subnet("8.8.8.8").hosts().isEmpty())
    }

    @Test fun theLastAddressFirst() = runBlocking {
        val network = FakeNetwork(mapOf("192.168.43.77" to comma))
        val result = CommaFinder(network).find("192.168.43.77", listOf(subnet("192.168.43.1")), setOf(comma))
        assertEquals(FindResult.Found(SshHost("192.168.43.77", comma, subnet("192.168.43.1"))), result)
        assertEquals(listOf("192.168.43.77"), network.probed)
    }

    @Test fun scansAndKeepsOnlyTheTrustedKey() = runBlocking {
        val network = FakeNetwork(mapOf("192.168.43.20" to other, "192.168.43.140" to comma), open = setOf("192.168.43.9"))
        val result = CommaFinder(network, parallel = 8).find(null, listOf(subnet("192.168.43.1")), setOf(comma))
        assertTrue(result is FindResult.Found)
        assertEquals("192.168.43.140", (result as FindResult.Found).comma.host)
        // The open port without an SSH server and the other SSH server were asked, and refused.
        assertTrue("192.168.43.9" in network.keyAsked)
        assertTrue("192.168.43.20" in network.keyAsked)
    }

    @Test fun theHotspotBeforeTheWifi() = runBlocking {
        val network = FakeNetwork(mapOf("192.168.1.50" to comma))
        val wifi = subnet("192.168.1.10", kind = LocalSubnet.Kind.WIFI, name = "wlan0")
        val hotspot = subnet("10.1.2.1", kind = LocalSubnet.Kind.HOTSPOT, name = "ap0")
        val result = CommaFinder(network, parallel = 4).find(null, listOf(wifi, hotspot), setOf(comma))
        assertEquals("192.168.1.50", (result as FindResult.Found).comma.host)
        assertTrue(network.probed.first().startsWith("10.1.2."))
        assertEquals(253, network.probed.count { it.startsWith("10.1.2.") })
    }

    @Test fun aChangedKeyIsNeverAccepted() = runBlocking {
        // The old address now has another SSH server; the comma moved to another address.
        val moved = FakeNetwork(mapOf("192.168.43.77" to other, "192.168.43.78" to comma))
        val found = CommaFinder(moved).find("192.168.43.77", listOf(subnet("192.168.43.1")), setOf(comma))
        assertEquals("192.168.43.78", (found as FindResult.Found).comma.host)
        // Nothing has the trusted key: the change is reported.
        val changed = FakeNetwork(mapOf("192.168.43.77" to other))
        val result = CommaFinder(changed).find("192.168.43.77", listOf(subnet("192.168.43.1")), setOf(comma))
        assertTrue(result is FindResult.NotFound)
        assertEquals(SshHost("192.168.43.77", other, subnet("192.168.43.1")), (result as FindResult.NotFound).changed)
    }

    @Test fun beforeTheFirstSyncTheOwnerChooses() = runBlocking {
        val network = FakeNetwork(mapOf("192.168.43.140" to comma, "192.168.43.20" to other))
        val result = CommaFinder(network).find(null, listOf(subnet("192.168.43.1")), emptySet())
        assertEquals(listOf("192.168.43.20", "192.168.43.140"), (result as FindResult.Choose).candidates.map { it.host })
        assertEquals(comma, result.candidates[1].fingerprint)
    }

    @Test fun nothingThere() = runBlocking {
        assertEquals(FindResult.NoNetwork, CommaFinder(FakeNetwork(emptyMap())).find(null, emptyList(), setOf(comma)))
        assertEquals(FindResult.NoNetwork, CommaFinder(FakeNetwork(emptyMap())).find(null, listOf(subnet("100.70.1.2")), setOf(comma)))
        val empty = CommaFinder(FakeNetwork(emptyMap())).find("192.168.43.77", listOf(subnet("192.168.43.1")), setOf(comma))
        assertTrue(empty is FindResult.NotFound && empty.changed == null && empty.scanned.size == 1)
    }
}
