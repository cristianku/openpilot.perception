package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SiteRoutesTest {
    private fun ip(text: String): ByteArray = SiteRoutes.hostBytes("http://$text")!!
    private fun prefix(text: String): RawPrefix = RawPrefix(ip(text.substringBefore('/')), text.substringAfter('/').toInt())
    private fun route(destination: String, gateway: String? = null) = RawRoute(prefix(destination), gateway?.let(::ip))

    private fun wifi(name: String, own: String, router: String, default: Boolean = true) =
        NetView(name, NetView.Kind.WIFI, default, listOf(prefix(own)),
            listOf(route("${Ipv4.format(Ipv4.parse(own.substringBefore('/'))!! and (-1 shl (32 - own.substringAfter('/').toInt())))}/${own.substringAfter('/')}"),
                route("0.0.0.0/0", router)))

    private val site = ip("10.10.10.213")
    private val home = wifi("home", "10.10.10.57/24", "10.10.10.1")
    private val zerotier = NetView("zt", NetView.Kind.VPN, true, listOf(prefix("10.147.17.5/24")),
        listOf(route("10.147.17.0/24"), route("10.10.10.0/24", "10.147.17.1")))
    private val cellular = NetView("lte", NetView.Kind.CELLULAR, true, listOf(prefix("10.88.1.9/30")), listOf(route("0.0.0.0/0", "10.88.1.10")))

    @Test fun theHomeWifiCarriesTheSiteOnItsSubnet() {
        val chosen = SiteRoutes.choose(site, listOf(home, cellular))!!
        assertEquals("home", chosen.network)
        assertFalse(chosen.bind)
        assertEquals("wifi 10.10.10.0/24 via 10.10.10.1", chosen.id)
        assertEquals("Wi-Fi 10.10.10.0/24, router 10.10.10.1", chosen.label)
        // The phone's own address changes with DHCP; the network's name does not.
        assertEquals(chosen.id, SiteRoutes.choose(site, listOf(wifi("home", "10.10.10.99/24", "10.10.10.1")))!!.id)
    }

    @Test fun aVpnThatRoutesTheSiteWins() {
        val chosen = SiteRoutes.choose(site, listOf(wifi("cafe", "192.168.1.30/24", "192.168.1.1"), zerotier))!!
        assertEquals("zt", chosen.network)
        assertEquals("vpn 10.147.17.5/24", chosen.id)
        assertFalse(chosen.bind)
    }

    @Test fun neverOverMobileData() {
        // A carrier network has 10.x hosts of its own: the site's address there is someone else.
        assertNull(SiteRoutes.choose(site, listOf(cellular)))
        assertNull(SiteRoutes.choose(site, emptyList()))
    }

    @Test fun aCafeWifiIsAnotherNetwork() {
        val cafe = SiteRoutes.choose(site, listOf(wifi("cafe", "10.10.10.40/24", "10.10.10.254")))!!
        assertNotEquals(SiteRoutes.choose(site, listOf(home))!!.id, cafe.id)
        // Behind its router, through the default route.
        val elsewhere = SiteRoutes.choose(site, listOf(wifi("cafe", "192.168.0.12/24", "192.168.0.1")))!!
        assertEquals("wifi 192.168.0.0/24 via 192.168.0.1", elsewhere.id)
    }

    @Test fun aWifiWithoutInternetIsBoundTo() {
        // Mobile data is the default network; the home Wi-Fi has no internet: the connections go on the Wi-Fi.
        val offline = wifi("garage-lan", "10.10.10.57/24", "10.10.10.1", default = false)
        val chosen = SiteRoutes.choose(site, listOf(offline, cellular))!!
        assertEquals("garage-lan", chosen.network)
        assertTrue(chosen.bind)
        // Not on its subnet and not the default: not a way to the site.
        assertNull(SiteRoutes.choose(site, listOf(wifi("other", "192.168.5.3/24", "192.168.5.1", default = false), cellular)))
    }

    @Test fun prefixesMatchBitByBit() {
        assertTrue(SiteRoutes.matches(ip("172.16.5.4"), prefix("172.16.0.0/12")))
        assertFalse(SiteRoutes.matches(ip("172.32.0.1"), prefix("172.16.0.0/12")))
        assertTrue(SiteRoutes.matches(ip("10.10.10.213"), prefix("0.0.0.0/0")))
        assertTrue(SiteRoutes.matches(ip("10.10.10.213"), prefix("10.10.10.212/31")))
        assertFalse(SiteRoutes.matches(ip("10.10.10.213"), prefix("10.10.10.214/31")))
        assertTrue(SiteRoutes.matches(ip("[fd00::12]"), RawPrefix(ip("[fd00::]"), 8)))
        assertFalse(SiteRoutes.matches(ip("[fd00::12]"), prefix("10.0.0.0/8")))
        assertEquals("10.10.10.0", SiteRoutes.format(SiteRoutes.masked(ip("10.10.10.213"), 24)))
        assertNull(SiteRoutes.hostBytes("https://perception.example.com"))
    }
}
