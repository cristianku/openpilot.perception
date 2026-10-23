package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SiteAddressTest {
    @Test fun plainHttpOnlyToPrivateAddresses() {
        assertEquals("http://10.10.10.213:8767", SiteAddress.parse("http://10.10.10.213:8767"))
        assertEquals("http://192.168.1.20:8767", SiteAddress.parse(" http://192.168.1.20:8767/#radar "))
        assertEquals("http://172.20.0.5", SiteAddress.parse("http://172.20.0.5/"))
        assertEquals("http://100.101.102.103:8767", SiteAddress.parse("http://100.101.102.103:8767"))
        assertEquals("http://[fd00::12]:8767", SiteAddress.parse("http://[fd00::12]:8767"))
        assertNull(SiteAddress.parse("http://8.8.8.8:8767"))
        assertNull(SiteAddress.parse("http://172.32.0.1"))
        assertNull(SiteAddress.parse("http://perception.example.com"))
        assertEquals("https://perception.example.com", SiteAddress.parse("https://Perception.example.com"))
        assertNull(SiteAddress.parse("ftp://10.0.0.1"))
        assertNull(SiteAddress.parse("http://user@10.0.0.1"))
        assertNull(SiteAddress.parse("10.10.10.213:8767"))
        assertTrue(SiteAddress.problem("http://8.8.8.8").startsWith("Plain http://"))
        assertEquals("", SiteAddress.problem(SiteAddress.DEFAULT))
    }

    @Test fun sameOrigin() {
        assertTrue(SiteAddress.sameOrigin("http://10.10.10.213:8767/api/app/v1", "http://10.10.10.213:8767"))
        assertFalse(SiteAddress.sameOrigin("http://10.10.10.213:8766/", "http://10.10.10.213:8767"))
        assertFalse(SiteAddress.sameOrigin("https://10.10.10.213:8767/", "http://10.10.10.213:8767"))
        assertTrue(SiteAddress.sameOrigin("https://a.example/x", "https://a.example:443"))
    }

    @Test fun ipv4() {
        assertEquals(0x0A0A0AD5, Ipv4.parse("10.10.10.213"))
        assertEquals("192.168.43.1", Ipv4.format(Ipv4.parse("192.168.43.1")!!))
        assertNull(Ipv4.parse("256.1.1.1"))
        assertNull(Ipv4.parse("1.2.3"))
        assertTrue(Ipv4.isPrivate(Ipv4.parse("172.31.255.1")!!))
        assertFalse(Ipv4.isPrivate(Ipv4.parse("100.64.0.1")!!))
        assertTrue(Ipv4.isLocal(Ipv4.parse("100.64.0.1")!!))
    }
}
