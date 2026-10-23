package ch.bodhi.perception

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.SocketFactory

/** A network of the phone the comma may be on. [socketFactory] binds sockets to that network (a Wi-Fi without
 * internet is not the default network while mobile data is on); null for the default routing (the hotspot). */
data class LocalSubnet(val interfaceName: String, val kind: Kind, val address: Int, val prefix: Int,
                       val socketFactory: SocketFactory? = null) {
    enum class Kind { HOTSPOT, WIFI, OTHER }

    /** The hosts to try: the subnet itself when it is /24 or smaller, otherwise the /24 around the phone's address;
     * never the phone, the network or the broadcast address. Private (RFC 1918) networks only. */
    fun hosts(): List<Int> {
        if (!Ipv4.isPrivate(address)) return emptyList()
        val bits = prefix.coerceIn(24, 30)
        val mask = -1 shl (32 - bits)
        val network = address and mask
        val broadcast = network or mask.inv()
        return ((network + 1) until broadcast).filter { it != address }
    }

    val label: String get() = "${Ipv4.format(address)}/$prefix ($interfaceName)"
}

/** An SSH server found on the phone's networks, with the SHA-256 fingerprint of its host key. */
data class SshHost(val host: String, val fingerprint: String, val subnet: LocalSubnet?)

/** What [CommaFinder.find] found. */
sealed class FindResult {
    /** The comma: its host key is one the app trusts. */
    data class Found(val comma: SshHost) : FindResult()

    /** No key is trusted yet (before the first sync with the site): the SSH servers found, for the owner to pick
     * one after seeing its fingerprint (trust on first use). */
    data class Choose(val candidates: List<SshHost>) : FindResult()

    /** Nothing with a trusted key. [changed]: the address that worked before now shows another key. */
    data class NotFound(val scanned: List<LocalSubnet>, val changed: SshHost?, val others: List<SshHost>) : FindResult()

    /** The phone is on no private network (no hotspot, no Wi-Fi). */
    data object NoNetwork : FindResult()
}

/** How the finder touches the network: [SshCommaConnector] in the app, a fake in the tests. */
interface CommaConnector {
    /** Whether something accepts TCP connections on [port] within [timeoutMs]. */
    suspend fun portOpen(host: String, port: Int, timeoutMs: Int, subnet: LocalSubnet?): Boolean

    /** The SHA-256 fingerprint of the SSH host key there (SHA256:…), null when it is not an SSH server. Only the
     * key exchange: no login. */
    suspend fun hostKey(host: String, port: Int, subnet: LocalSubnet?): String?
}

/** Finds the comma: the address that worked last first, then every host of the phone's networks (the hotspot
 * first, then the Wi-Fi) on port 22 with short timeouts, keeping only the SSH server whose host key the app
 * trusts. A changed key is reported, never accepted. */
class CommaFinder(
    private val connector: CommaConnector,
    private val port: Int = 22,
    private val probeTimeoutMs: Int = 350,
    private val parallel: Int = 48
) {
    suspend fun find(lastGood: String?, subnets: List<LocalSubnet>, trusted: Set<String>): FindResult {
        val ordered = subnets.sortedBy { it.kind.ordinal }
        var changed: SshHost? = null
        if (lastGood != null) {
            val subnet = ordered.firstOrNull { subnet -> Ipv4.parse(lastGood)?.let { it in subnet.hosts() } == true }
            if (connector.portOpen(lastGood, port, 1_500, subnet)) {
                val fingerprint = connector.hostKey(lastGood, port, subnet)
                if (fingerprint != null) {
                    val host = SshHost(lastGood, fingerprint, subnet)
                    if (fingerprint in trusted) return FindResult.Found(host)
                    if (trusted.isNotEmpty()) changed = host
                }
            }
        }
        val scannable = ordered.filter { it.hosts().isNotEmpty() }
        if (scannable.isEmpty()) {
            return if (changed != null) FindResult.NotFound(emptyList(), changed, emptyList()) else FindResult.NoNetwork
        }
        val seen = mutableListOf<SshHost>()
        for (subnet in scannable) {
            val found = scan(subnet, trusted, skip = setOfNotNull(lastGood))
            seen += found
            found.firstOrNull { it.fingerprint in trusted }?.let { return FindResult.Found(it) }
        }
        val others = seen.filter { it.host != changed?.host }
        if (trusted.isEmpty()) {
            val candidates = (listOfNotNull(changed) + seen).distinctBy { it.host }
            return if (candidates.isEmpty()) FindResult.NotFound(scannable, null, emptyList()) else FindResult.Choose(candidates)
        }
        return FindResult.NotFound(scannable, changed, others)
    }

    /** Every SSH server of one subnet; stops early once a trusted one answers. */
    private suspend fun scan(subnet: LocalSubnet, trusted: Set<String>, skip: Set<String>): List<SshHost> = coroutineScope {
        val gate = Semaphore(parallel)
        val found = java.util.Collections.synchronizedList(mutableListOf<SshHost>())
        val stop = AtomicBoolean(false)
        subnet.hosts().map(Ipv4::format).filter { it !in skip }.map { host ->
            launch(Dispatchers.IO) {
                gate.withPermit {
                    if (stop.get()) return@withPermit
                    if (connector.portOpen(host, port, probeTimeoutMs, subnet)) {
                        val fingerprint = connector.hostKey(host, port, subnet) ?: return@withPermit
                        found += SshHost(host, fingerprint, subnet)
                        if (fingerprint in trusted) stop.set(true)
                    }
                }
            }
        }.joinAll()
        synchronized(found) { found.sortedBy { Ipv4.parse(it.host) ?: 0 } }
    }
}
