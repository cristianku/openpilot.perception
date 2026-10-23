package ch.bodhi.perception

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address
import java.net.NetworkInterface

/** The private IPv4 networks of the phone, where the comma may be:
 *  - HOTSPOT: an interface that carries a private address but belongs to no network the phone joined; that is
 *    the phone's own hotspot (or USB tethering), the garage case. Its routes apply to every app.
 *  - WIFI: the Wi-Fi the phone joined, with its own socket factory: a Wi-Fi without internet is not the default
 *    network while mobile data is on, and its sockets must be bound to it.
 * Mobile data, VPNs (ZeroTier) and loopback are never scanned. */
object LocalNetworks {
    private val SKIP = Regex("^(lo|tun|ppp|rmnet|ccmni|dummy|ip6tnl|sit|v4-|clat|ifb|p2p|aware|nan|radio).*")

    fun subnets(context: Context): List<LocalSubnet> {
        val result = mutableListOf<LocalSubnet>()
        val joined = mutableSetOf<String>()
        val manager = context.getSystemService(ConnectivityManager::class.java)
        try {
            @Suppress("DEPRECATION")
            for (network in manager?.allNetworks.orEmpty()) {
                val capabilities = manager?.getNetworkCapabilities(network) ?: continue
                val links = manager.getLinkProperties(network) ?: continue
                val name = links.interfaceName ?: continue
                joined += name
                if (!capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
                    !capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) continue
                for (link in links.linkAddresses) {
                    val address = link.address as? Inet4Address ?: continue
                    val value = Ipv4.parse(address.hostAddress ?: continue) ?: continue
                    if (!Ipv4.isPrivate(value)) continue
                    result += LocalSubnet(name, LocalSubnet.Kind.WIFI, value, link.prefixLength, network.socketFactory)
                }
            }
        } catch (_: SecurityException) {
        }
        try {
            for (face in NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()) {
                val name = face.name ?: continue
                if (name in joined || SKIP.matches(name) || !face.isUp || face.isLoopback) continue
                for (link in face.interfaceAddresses) {
                    val address = link.address as? Inet4Address ?: continue
                    val value = Ipv4.parse(address.hostAddress ?: continue) ?: continue
                    if (!Ipv4.isPrivate(value)) continue
                    result += LocalSubnet(name, LocalSubnet.Kind.HOTSPOT, value, link.networkPrefixLength.toInt(), null)
                }
            }
        } catch (_: Exception) {
        }
        return result.distinctBy { it.address to it.prefix }
    }
}
