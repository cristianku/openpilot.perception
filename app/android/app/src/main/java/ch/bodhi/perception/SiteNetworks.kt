package ch.bodhi.perception

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.RouteInfo
import android.os.Build
import java.net.InetAddress
import java.net.URI

// The site is plain HTTP at a private address (http://10.10.10.213:8767), and that address can belong to another
// machine on whatever network the phone is on (a café, work or carrier network). Plain HTTP cannot prove which
// machine answers, so the app talks to the site with its session, and believes what it says (the calibration,
// the comma's paths, which tests it has), only on the networks the owner uses it from: the one he signed in on,
// and the ones he says are his (the Site tab asks when the site answers on a new one). Never over mobile data.
// An https:// site proves itself with its certificate and needs none of this.

/** The phone's way to the site now: the network that carries the traffic, a name that is the same on every visit
 * to that network (for the list of the owner's networks), and a label for people. [network] is an
 * android.net.Network (Any here so the choice runs in the JVM tests); [bind]: open the connections on that network
 * (a Wi-Fi without internet is not the default network while mobile data is on). [any]: an https:// site or the
 * phone itself, where the network does not matter. */
data class SiteRoute(val network: Any?, val bind: Boolean, val id: String, val label: String, val any: Boolean = false)

/** An IP prefix as raw bytes (4 for IPv4, 16 for IPv6). */
class RawPrefix(val address: ByteArray, val length: Int)

/** A route of a network: its destination and its gateway (null when on-link). */
class RawRoute(val destination: RawPrefix, val gateway: ByteArray?)

/** A network of the phone as the choice sees it. [isDefault]: what the app's unbound connections use (the
 * underlying Wi-Fi when a VPN is the default). */
class NetView(val network: Any?, val kind: Kind, val isDefault: Boolean, val addresses: List<RawPrefix>, val routes: List<RawRoute>) {
    enum class Kind { VPN, WIFI, ETHERNET, CELLULAR, OTHER }
}

object SiteRoutes {
    /** The network the traffic to [target] goes through: a VPN that routes it (ZeroTier), else a Wi-Fi or
     * Ethernet the address is on (the home network), else the default Wi-Fi or Ethernet (the site behind its
     * router); null when only mobile data (or nothing) would carry it. */
    fun choose(target: ByteArray, networks: List<NetView>): SiteRoute? {
        fun longest(net: NetView, specificOnly: Boolean): Int? = net.routes
            .filter { (!specificOnly || it.destination.length > 0) && matches(target, it.destination) }
            .maxOfOrNull { it.destination.length }

        val vpns = networks.filter { it.kind == NetView.Kind.VPN }
        val lans = networks.filter { it.kind == NetView.Kind.WIFI || it.kind == NetView.Kind.ETHERNET }
        val chosen = vpns.mapNotNull { net -> longest(net, false)?.let { net to it } }.maxByOrNull { it.second }?.first
            ?: lans.mapNotNull { net -> longest(net, true)?.let { net to it } }.maxByOrNull { it.second }?.first
            ?: lans.firstOrNull { it.isDefault && longest(it, false) != null }
            ?: return null
        // Under a VPN, binding to another network may be refused: the system routes the traffic itself.
        val bind = vpns.isEmpty() && !chosen.isDefault
        return SiteRoute(chosen.network, bind, identity(chosen), label(chosen))
    }

    /** The same on every visit: a VPN by the phone's own address on it (ZeroTier keeps it), a Wi-Fi or Ethernet
     * by its subnet and router (the phone's own address there changes with DHCP). */
    fun identity(net: NetView): String {
        val own = net.addresses.sortedBy { if (it.address.size == 4) 0 else 1 }
        return when (net.kind) {
            NetView.Kind.VPN -> "vpn " + own.joinToString(",") { "${format(it.address)}/${it.length}" }
            else -> {
                val subnets = own.joinToString(",") { "${format(masked(it.address, it.length))}/${it.length}" }
                val router = net.routes.firstOrNull { it.destination.length == 0 && it.gateway != null }?.gateway
                "${net.kind.name.lowercase()} $subnets via ${router?.let(::format) ?: "-"}"
            }
        }
    }

    fun label(net: NetView): String {
        val own = net.addresses.firstOrNull { it.address.size == 4 } ?: net.addresses.firstOrNull()
        val router = net.routes.firstOrNull { it.destination.length == 0 && it.gateway != null }?.gateway
        return when (net.kind) {
            NetView.Kind.VPN -> "VPN" + (own?.let { ", this phone ${format(it.address)}" } ?: "")
            NetView.Kind.WIFI, NetView.Kind.ETHERNET -> (if (net.kind == NetView.Kind.WIFI) "Wi-Fi" else "Ethernet") +
                (own?.let { " ${format(masked(it.address, it.length))}/${it.length}" } ?: "") +
                (router?.let { ", router ${format(it)}" } ?: "")
            else -> "another network"
        }
    }

    fun matches(address: ByteArray, prefix: RawPrefix): Boolean {
        if (address.size != prefix.address.size || prefix.length < 0 || prefix.length > address.size * 8) return false
        var bits = prefix.length
        var index = 0
        while (bits > 0) {
            val mask = if (bits >= 8) 0xFF else (0xFF shl (8 - bits)) and 0xFF
            if ((address[index].toInt() and mask) != (prefix.address[index].toInt() and mask)) return false
            bits -= 8
            index++
        }
        return true
    }

    fun masked(address: ByteArray, length: Int): ByteArray = ByteArray(address.size) { index ->
        val bits = (length - index * 8).coerceIn(0, 8)
        (address[index].toInt() and ((0xFF shl (8 - bits)) and 0xFF)).toByte()
    }

    fun format(address: ByteArray): String =
        if (address.size == 4) address.joinToString(".") { (it.toInt() and 0xFF).toString() }
        else InetAddress.getByAddress(address).hostAddress ?: "?"

    /** The raw address of the site's host when it is an IP literal (the only kind plain http:// may use). */
    fun hostBytes(base: String): ByteArray? = try {
        val host = URI(base).host?.removePrefix("[")?.removeSuffix("]") ?: return null
        val ipv4 = Ipv4.parse(host)
        when {
            ipv4 != null -> byteArrayOf((ipv4 ushr 24).toByte(), (ipv4 ushr 16).toByte(), (ipv4 ushr 8).toByte(), ipv4.toByte())
            host.contains(':') && host.all { it.isLetterOrDigit() || it == ':' || it == '.' } -> InetAddress.getByName(host).address
            else -> null
        }
    } catch (_: Exception) {
        null
    }
}

/** The phone's networks, read from the system, and the route to the site. */
object SiteNetworks {
    fun route(context: Context, base: String): SiteRoute? {
        if (!base.startsWith("http://")) return SiteRoute(null, false, "https", "HTTPS", any = true)
        val target = SiteRoutes.hostBytes(base) ?: return null
        if (InetAddress.getByAddress(target).isLoopbackAddress) return SiteRoute(null, false, "loopback", "this phone", any = true)
        return SiteRoutes.choose(target, views(context))
    }

    private fun views(context: Context): List<NetView> {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return emptyList()
        return try {
            val active = manager.activeNetwork
            val activeIsVpn = active?.let { manager.getNetworkCapabilities(it) }?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
            @Suppress("DEPRECATION")
            manager.allNetworks.mapNotNull { network -> view(manager, network, active, activeIsVpn) }
        } catch (_: SecurityException) {
            emptyList()
        }
    }

    private fun view(manager: ConnectivityManager, network: Network, active: Network?, activeIsVpn: Boolean): NetView? {
        val capabilities = manager.getNetworkCapabilities(network) ?: return null
        val links = manager.getLinkProperties(network) ?: return null
        val kind = when {
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> NetView.Kind.VPN
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> NetView.Kind.WIFI
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> NetView.Kind.ETHERNET
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> NetView.Kind.CELLULAR
            else -> NetView.Kind.OTHER
        }
        val validated = capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        val isDefault = network == active ||
            (activeIsVpn && validated && (kind == NetView.Kind.WIFI || kind == NetView.Kind.ETHERNET))
        val routes = links.routes
            // Throw and unreachable routes (a split VPN) carry nothing; their type is public from Android 13.
            .filter { Build.VERSION.SDK_INT < 33 || it.type == RouteInfo.RTN_UNICAST }
            .map { route ->
                val gateway = route.gateway?.takeIf { !it.isAnyLocalAddress }?.address
                RawRoute(RawPrefix(route.destination.rawAddress, route.destination.prefixLength), gateway)
            }
        val addresses = links.linkAddresses
            .filter { !it.address.isLinkLocalAddress && !it.address.isLoopbackAddress }
            .map { RawPrefix(it.address.address, it.prefixLength) }
        return NetView(network, kind, isDefault, addresses, routes)
    }
}
