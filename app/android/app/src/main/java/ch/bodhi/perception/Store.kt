package ch.bodhi.perception

import android.content.Context
import androidx.core.content.edit
import org.json.JSONObject

/** The app's settings and what it keeps of the site for offline use, in private preferences (never backed up).
 * The session cookie is not here: it lives in the WebView's cookie store. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("perception", Context.MODE_PRIVATE)

    var siteAddress: String
        get() = prefs.getString("site", null)?.let(SiteAddress::parse) ?: SiteAddress.DEFAULT
        set(value) {
            SiteAddress.parse(value)?.let { prefs.edit { putString("site", it) } }
        }

    var setupDone: Boolean
        get() = prefs.getBoolean("setup_done", false)
        set(value) = prefs.edit { putBoolean("setup_done", value) }

    /** The site said the owner does not allow the one-tap key (403 not_allowed): only the GitHub way. */
    var oneTapRefused: Boolean
        get() = prefs.getBoolean("one_tap_refused", false)
        set(value) = prefs.edit { putBoolean("one_tap_refused", value) }

    /** The address the comma answered at last, with a trusted key. */
    var commaAddress: String?
        get() = prefs.getString("comma_address", null)?.takeIf { Ipv4.parse(it) != null }
        set(value) = prefs.edit { if (value == null) remove("comma_address") else putString("comma_address", value) }

    /** The comma's host key accepted by the owner before the site told it (trust on first use). */
    var acceptedFingerprint: String?
        get() = prefs.getString("comma_fingerprint", null)
        set(value) = prefs.edit { if (value == null) remove("comma_fingerprint") else putString("comma_fingerprint", value) }

    /** The networks the owner uses the site from ([SiteRoute.id]): the one he signed in on and the ones he said
     * are his. On any other the app keeps its session and its trust to itself (SiteNetworks.kt). */
    var siteNetworks: Set<String>
        get() = prefs.getStringSet("site_networks", null)?.toSet().orEmpty()
        set(value) = prefs.edit { putStringSet("site_networks", value.toSet()) }

    /** GET /api/app/v1 as last received, and when (phone clock). */
    fun cachedInfo(): Pair<SiteInfo, Double>? {
        val text = prefs.getString("site_info", null) ?: return null
        return try {
            SiteApi.parseInfo(JSONObject(text)) to prefs.getLong("site_info_at_ms", 0L) / 1000.0
        } catch (_: Exception) {
            null
        }
    }

    fun saveInfo(raw: String) {
        prefs.edit { putString("site_info", raw).putLong("site_info_at_ms", System.currentTimeMillis()) }
    }

    /** GET /api/app/v1/tests as last received. */
    fun cachedSiteTests(): List<SiteTest>? {
        val text = prefs.getString("site_tests", null) ?: return null
        return try {
            SiteApi.parseTests(JSONObject(text))
        } catch (_: Exception) {
            null
        }
    }

    fun saveSiteTests(raw: String) {
        prefs.edit { putString("site_tests", raw) }
    }

    /** Signed out or another site: what came from the old one is forgotten (the tests on the phone stay). The
     * owner's networks stay for a sign-out; another address forgets them too ([forgetSiteNetworks]). */
    fun forgetSite() {
        prefs.edit { remove("site_info").remove("site_info_at_ms").remove("site_tests").remove("one_tap_refused") }
    }

    fun forgetSiteNetworks() {
        prefs.edit { remove("site_networks") }
    }

    fun forgetComma() {
        commaAddress = null
        acceptedFingerprint = null
    }
}
