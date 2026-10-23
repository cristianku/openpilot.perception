package ch.bodhi.perception

import java.net.URI

/** The address of perception-web, as the owner types it. The site is plain HTTP on the LAN or ZeroTier, so
 * http:// is accepted only for a private IP literal (res/xml/network_security_config.xml explains why the
 * app, not the platform, enforces this); any other host needs https://. */
object SiteAddress {
    const val DEFAULT = "http://10.10.10.213:8767"

    /** scheme://host[:port] of a plain site address; null for anything else (path, query, user info, http to
     * a public or named host). A pasted page address (…/#radar) is reduced to the site first. */
    fun parse(input: String): String? = try {
        val uri = URI(reduce(input))
        val scheme = uri.scheme?.lowercase()
        val host = uri.host
        when {
            scheme != "https" && scheme != "http" -> null
            host.isNullOrBlank() || uri.rawUserInfo != null -> null
            uri.port > 65535 || uri.port == 0 || uri.port < -1 -> null
            scheme == "http" && !isPrivateLiteral(host) -> null
            else -> {
                val shown = if (host.contains(":") && !host.startsWith("[")) "[$host]" else host
                "$scheme://${shown.lowercase()}${if (uri.port > 0) ":${uri.port}" else ""}"
            }
        }
    } catch (_: Exception) {
        null
    }

    /** Why [parse] refused the address, for the form. */
    fun problem(input: String): String {
        val text = input.trim()
        if (text.isEmpty()) return "Type the address you open the site at."
        if (parse(text) != null) return ""
        val scheme = text.substringBefore("://", "").lowercase()
        return when {
            scheme.isEmpty() -> "Start with http:// or https://, for example $DEFAULT."
            scheme == "http" -> "Plain http:// only for a private address (10.x, 172.16–31.x, 192.168.x, " +
                "100.64–127.x): use https:// for any other."
            else -> "Not a site address. Example: $DEFAULT."
        }
    }

    /** The page address people paste (http://10.10.10.213:8767/#radar) reduced to the site. */
    fun reduce(input: String): String = try {
        val text = input.trim()
        val uri = URI(text)
        if (uri.scheme == null || uri.rawAuthority == null) text else "${uri.scheme}://${uri.rawAuthority}"
    } catch (_: Exception) {
        input.trim()
    }

    /** Whether two absolute URLs have the same scheme, host and port. */
    fun sameOrigin(url: String, base: String): Boolean = try {
        val a = URI(url)
        val b = URI(base)
        a.scheme.equals(b.scheme, ignoreCase = true) && a.host.equals(b.host, ignoreCase = true) &&
            effectivePort(a) == effectivePort(b)
    } catch (_: Exception) {
        false
    }

    private fun effectivePort(uri: URI): Int =
        if (uri.port > 0) uri.port else if (uri.scheme.equals("https", ignoreCase = true)) 443 else 80

    /** An IPv4 or IPv6 literal of a private, shared (100.64/10, as Tailscale), link-local or loopback range. */
    fun isPrivateLiteral(host: String): Boolean {
        val bare = host.removePrefix("[").removeSuffix("]")
        ipv4(bare)?.let { return Ipv4.isLocal(it) }
        if (!bare.contains(":") || !bare.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' || it == ':' }) return false
        val lower = bare.lowercase()
        return lower == "::1" || lower.startsWith("fc") || lower.startsWith("fd") ||
            lower.startsWith("fe8") || lower.startsWith("fe9") || lower.startsWith("fea") || lower.startsWith("feb")
    }

    private fun ipv4(text: String): Int? = Ipv4.parse(text)
}

/** IPv4 addresses as Int (network order: 10.0.0.1 is 0x0A000001). */
object Ipv4 {
    fun parse(text: String): Int? {
        val parts = text.split('.')
        if (parts.size != 4) return null
        var value = 0
        for (part in parts) {
            if (part.isEmpty() || part.length > 3 || !part.all { it.isDigit() }) return null
            val number = part.toInt()
            if (number > 255) return null
            value = (value shl 8) or number
        }
        return value
    }

    fun format(address: Int): String =
        "${(address ushr 24) and 255}.${(address ushr 16) and 255}.${(address ushr 8) and 255}.${address and 255}"

    private fun inRange(address: Int, network: Int, prefix: Int): Boolean {
        val mask = if (prefix == 0) 0 else (-1 shl (32 - prefix))
        return (address and mask) == (network and mask)
    }

    /** RFC 1918: 10/8, 172.16/12, 192.168/16. The comma is looked for in these only. */
    fun isPrivate(address: Int): Boolean =
        inRange(address, 0x0A000000, 8) || inRange(address, 0xAC100000.toInt(), 12) ||
            inRange(address, 0xC0A80000.toInt(), 16)

    /** Private, shared address space (100.64/10), link-local or loopback: where a plain-HTTP site may be. */
    fun isLocal(address: Int): Boolean =
        isPrivate(address) || inRange(address, 0x64400000, 10) || inRange(address, 0xA9FE0000.toInt(), 16) ||
            inRange(address, 0x7F000000, 8)
}
