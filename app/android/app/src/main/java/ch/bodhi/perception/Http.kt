package ch.bodhi.perception

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLConnection

/** What the site answered: the status, the body and the cookies it set. */
class HttpReply(val status: Int, val body: String, val setCookies: List<String> = emptyList())

/** A blocking HTTP exchange; throws IOException when the site cannot be reached. Call it off the main thread. */
fun interface HttpTransport {
    fun exchange(method: String, url: String, cookie: String?, jsonBody: String?): HttpReply
}

/** The session of the site: its cookie (perception_web_<port>, HttpOnly), kept privately on the phone and
 * shared with the WebView, so the native screens and the site are signed in together. */
interface SessionCookies {
    fun header(base: String): String?
    fun store(base: String, setCookies: List<String>)
    fun clear()
}

/** HttpURLConnection, small bodies, no redirects, no cache; the app sends no Origin header (the site accepts
 * requests without one) and Content-Type application/json with a Content-Length. [open] opens the connection:
 * on the network of the site's route (android.net.Network.openConnection) or the default one. */
class UrlConnectionTransport(
    private val connectTimeoutMs: Int = 4_000,
    private val readTimeoutMs: Int = 15_000,
    private val open: (URL) -> URLConnection = { it.openConnection() }
) : HttpTransport {
    override fun exchange(method: String, url: String, cookie: String?, jsonBody: String?): HttpReply {
        val connection = (open(URL(url)) as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = connectTimeoutMs
            readTimeout = readTimeoutMs
            instanceFollowRedirects = false
            useCaches = false
            setRequestProperty("Accept", "application/json")
            if (!cookie.isNullOrBlank()) setRequestProperty("Cookie", cookie)
        }
        try {
            if (jsonBody != null) {
                val bytes = jsonBody.toByteArray(Charsets.UTF_8)
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.use { input ->
                // The answers of the app API are small; a bigger one is not the site.
                val bytes = input.readNBytesCompat(MAX_REPLY_BYTES + 1)
                if (bytes.size > MAX_REPLY_BYTES) throw IOException("reply too large")
                String(bytes, Charsets.UTF_8)
            } ?: ""
            return HttpReply(status, text, connection.headerFields["Set-Cookie"].orEmpty())
        } finally {
            connection.disconnect()
        }
    }

    private fun java.io.InputStream.readNBytesCompat(limit: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (out.size() < limit) {
            val count = read(buffer, 0, minOf(buffer.size, limit - out.size()))
            if (count < 0) break
            out.write(buffer, 0, count)
        }
        return out.toByteArray()
    }

    companion object {
        const val MAX_REPLY_BYTES = 2 * 1024 * 1024
    }
}

/** The cookies of the WebView (android.webkit.CookieManager): stored in the app's private data, flushed at
 * once so a fresh sign-in survives the app being killed. */
class WebViewCookies : SessionCookies {
    private val manager get() = android.webkit.CookieManager.getInstance()

    override fun header(base: String): String? = manager.getCookie(base)

    override fun store(base: String, setCookies: List<String>) {
        setCookies.forEach { manager.setCookie(base, it) }
        manager.flush()
    }

    override fun clear() {
        manager.removeAllCookies(null)
        manager.flush()
    }
}

/** The session kept back: on a network the owner has not used the site from, nothing goes out with the cookie
 * (a sign-in made there still stores its new session). */
class WithoutSession(private val cookies: SessionCookies) : SessionCookies {
    override fun header(base: String): String? = null
    override fun store(base: String, setCookies: List<String>) = cookies.store(base, setCookies)
    override fun clear() = cookies.clear()
}
