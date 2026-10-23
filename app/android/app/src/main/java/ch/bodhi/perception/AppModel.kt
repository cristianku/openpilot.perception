package ch.bodhi.perception

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File

class PerceptionApp : Application() {
    lateinit var model: AppModel
        private set

    override fun onCreate() {
        super.onCreate()
        CommaSsh.installCrypto()
        model = AppModel(this)
    }
}

/** Where the site stands from this phone. NEW_NETWORK: the site's address answers on a network the owner has not
 * used the site from; the app keeps its session to itself there until he says the network is his. */
enum class SiteLink { UNKNOWN, CHECKING, ONLINE, SIGNED_OUT, OFFLINE, PROBLEM, NEW_NETWORK }

/** The result of "Test the connection". */
sealed class ConnectionCheck {
    data class Connected(val host: String, val fingerprint: String, val openpilotFound: Boolean) : ConnectionCheck()
    data class Choose(val candidates: List<SshHost>) : ConnectionCheck()
    data class Failed(val title: String, val detail: String) : ConnectionCheck()
}

/** The state of the app, one for the process: the site (cached for offline use), the tests on the phone and
 * their sending, the phone key, the comma's address and key, and the radar session. Compose reads its fields;
 * they are written on the main thread. */
class AppModel(private val context: Context) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val store = Store(context)
    val queue = TestQueue(File(context.filesDir, "tests.json"))
    val phoneKey = PhoneKeyStore(File(context.filesDir, "ssh"), KeystoreWrapper())
    private val cookies = WebViewCookies()
    private val defaultTransport = UrlConnectionTransport()
    private val refreshLock = Mutex()

    var siteAddress by mutableStateOf(store.siteAddress)
        private set
    var siteLink by mutableStateOf(SiteLink.UNKNOWN)
        private set
    var siteProblem by mutableStateOf("")
        private set
    var info by mutableStateOf(store.cachedInfo()?.first)
        private set
    var infoAtUnix by mutableStateOf(store.cachedInfo()?.second)
        private set
    var siteTests by mutableStateOf(store.cachedSiteTests().orEmpty())
        private set
    /** Whether [siteTests] is the site's list (read once at least since the sign-in), not just empty. */
    var siteTestsKnown by mutableStateOf(store.cachedSiteTests() != null)
        private set
    /** The networks the owner uses the site from ([SiteRoute.id]). */
    var siteNetworks by mutableStateOf(store.siteNetworks)
        private set
    /** The phone's way to the site now, for people ("Wi-Fi 192.168.1.0/24, router 192.168.1.1"); null: none. */
    var siteNetworkLabel by mutableStateOf<String?>(null)
        private set
    /** +1 after every read of the site that worked: the Site tab leaves its error screen then. */
    var siteOkCount by mutableIntStateOf(0)
        private set
    /** Whether the WebView may send requests to the site now (read on its network thread): only on the
     * owner's networks, never in the moment after the network changed and before it was checked again. */
    @Volatile
    var siteRequestsAllowed = false
        private set
    /** Set by the WebView when it held a request to the site back: the page is loaded again once the site is
     * checked again. */
    val siteBlocked = java.util.concurrent.atomic.AtomicBoolean(false)
    var tests by mutableStateOf(safeTests())
        private set
    var syncing by mutableStateOf(false)
        private set
    var lastSync by mutableStateOf<SyncReport?>(null)
        private set
    var setupDone by mutableStateOf(store.setupDone)
        private set
    var commaAddress by mutableStateOf(store.commaAddress)
        private set
    var acceptedFingerprint by mutableStateOf(store.acceptedFingerprint)
        private set
    var publicKey by mutableStateOf(phoneKey.publicLine())
        private set
    var oneTapRefused by mutableStateOf(store.oneTapRefused)
        private set

    val radar = RadarSession(this)

    private var foreground: Job? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var listener: ByteArray? = null

    /** On the owner's networks (or before the first sign-in, when there is no session to keep), or an https://
     * site, which proves itself. */
    private fun trusted(route: SiteRoute?): Boolean =
        route != null && (route.any || siteNetworks.isEmpty() || route.id in siteNetworks)

    /** The site on [route]'s network; the session goes with the requests only on the owner's networks. */
    private fun api(route: SiteRoute?, session: Boolean = trusted(route)): SiteApi {
        val network = route?.network as? Network
        val transport = if (route?.bind == true && network != null) UrlConnectionTransport(open = { network.openConnection(it) })
        else defaultTransport
        return SiteApi(siteAddress, transport, if (session) cookies else WithoutSession(cookies))
    }

    /** Blocking (the phone's networks are read): off the main thread. */
    fun api(): SiteApi = api(siteRoute())

    private fun siteRoute(): SiteRoute? = try {
        SiteNetworks.route(context, siteAddress)
    } catch (_: Exception) {
        null
    }

    private fun rememberNetwork(route: SiteRoute) {
        if (route.any || route.id in siteNetworks) return
        siteNetworks = siteNetworks + route.id
        store.siteNetworks = siteNetworks
    }

    /** The owner says the network the site answers on now is his (home Wi-Fi, ZeroTier). */
    suspend fun useSiteOnThisNetwork() {
        val route = withContext(Dispatchers.IO) { siteRoute() } ?: return
        rememberNetwork(route)
        refreshSite(wait = true)
    }

    /** The calibration in use: the site's, as last received; null shows raw readings. */
    val calibration: CalibrationValues? get() = info?.calibration?.values

    /** The comma's host key the app trusts: the one pinned (accepted by the owner, or the site's, pinned at the
     * first connection that showed it); the site's list only while none is pinned. What the site says later never
     * replaces a pinned key by itself: the owner decides ([useSiteCommaKey]). */
    fun trustedFingerprints(): Set<String> =
        acceptedFingerprint?.let { setOf(it) } ?: info?.comma?.hostKeyFingerprints?.toSet().orEmpty()

    /** The site tells other keys for the comma than the pinned one: the pinned one stays in use, the owner is told. */
    val fingerprintConflict: Boolean
        get() = acceptedFingerprint != null && info?.comma?.hostKeyFingerprints?.let { it.isNotEmpty() && acceptedFingerprint !in it } == true

    /** The owner confirmed that the site's key is the comma's (the comma was reset): the pinned one goes, the
     * site's is pinned at the next connection. */
    fun useSiteCommaKey() {
        radar.stopNow()
        store.acceptedFingerprint = null
        acceptedFingerprint = null
    }

    /** Waiting on the phone: not sent yet, or sent and not yet in the site's list. */
    val pendingCount: Int get() = tests.count { it.state == SyncState.LOCAL || it.state == SyncState.SENT }

    private fun safeTests(): List<QueuedTest> = try {
        queue.all()
    } catch (_: Exception) {
        emptyList()
    }

    // -- the site ------------------------------------------------------------------------------------

    /** While the app is on screen: the site every 30 s (15 s while it is out of reach), and at once when a
     * network comes or goes. */
    fun onForeground() {
        if (foreground?.isActive == true) return
        foreground = scope.launch {
            while (isActive) {
                refreshSite()
                delay(if (siteLink == SiteLink.ONLINE) 30_000 else 15_000)
            }
        }
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            // Another network: nothing goes to the site from the WebView until the new one is checked.
            override fun onAvailable(network: Network) {
                siteRequestsAllowed = false
                scope.launch { delay(1_500); refreshSite() }
            }

            override fun onLost(network: Network) {
                siteRequestsAllowed = false
                scope.launch { refreshSite() }
            }
        }
        try {
            manager.registerDefaultNetworkCallback(callback)
            networkCallback = callback
        } catch (_: Exception) {
        }
    }

    fun onBackground() {
        foreground?.cancel()
        foreground = null
        networkCallback?.let {
            try {
                context.getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(it)
            } catch (_: Exception) {
            }
        }
        networkCallback = null
    }

    fun refreshSoon() {
        scope.launch { refreshSite() }
    }

    /** The network to the site first (on a network that is not the owner's, only whether the site answers, without
     * the session); then GET /api/app/v1 (kept for offline use), the tests on the phone to the site, and the
     * site's tests, which confirm the ones sent. */
    suspend fun refreshSite(wait: Boolean = false) {
        // The background rounds skip while one runs; what the owner just did (a sign-in) waits for it.
        if (!wait && refreshLock.isLocked) return
        refreshLock.withLock {
            // A state on screen (signed out, a problem, a new network) stays there while it is checked again.
            if (siteLink == SiteLink.UNKNOWN || siteLink == SiteLink.OFFLINE) siteLink = SiteLink.CHECKING
            val route = withContext(Dispatchers.IO) { siteRoute() }
            siteNetworkLabel = route?.label
            if (route == null) {
                siteRequestsAllowed = false
                siteLink = SiteLink.OFFLINE
                return
            }
            if (!trusted(route)) {
                siteRequestsAllowed = false
                siteLink = try {
                    withContext(Dispatchers.IO) { api(route).authState() }
                    SiteLink.NEW_NETWORK
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Exception) {
                    SiteLink.OFFLINE
                }
                return
            }
            siteRequestsAllowed = true
            val api = api(route)
            try {
                val (fresh, raw) = withContext(Dispatchers.IO) { api.info() }
                // The first network the session works on is the owner's (he signed in there).
                rememberNetwork(route)
                store.saveInfo(raw)
                info = fresh
                infoAtUnix = System.currentTimeMillis() / 1000.0
                siteLink = SiteLink.ONLINE
                siteProblem = ""
                if (tests.any { it.state == SyncState.LOCAL }) sync(api)
                val (list, rawTests) = withContext(Dispatchers.IO) { api.tests() }
                store.saveSiteTests(rawTests)
                siteTests = list
                siteTestsKnown = true
                // A test counts as on the site once the site's own list shows it.
                withContext(Dispatchers.IO) { queue.confirm(list.mapNotNull { it.clientId }.toSet()) }
                tests = safeTests()
                siteOkCount++
            } catch (error: CancellationException) {
                throw error
            } catch (problem: SiteProblem) {
                when (problem) {
                    is SiteProblem.Unreachable -> siteLink = SiteLink.OFFLINE
                    is SiteProblem.SignInNeeded -> siteLink = SiteLink.SIGNED_OUT
                    else -> {
                        siteLink = if (problem.temporary) SiteLink.OFFLINE else SiteLink.PROBLEM
                        siteProblem = problem.message ?: ""
                    }
                }
            } catch (_: Exception) {
                siteLink = SiteLink.OFFLINE
            }
        }
    }

    private suspend fun sync(api: SiteApi) {
        syncing = true
        try {
            val report = withContext(Dispatchers.IO) { TestSync(queue, api).run() }
            lastSync = report
            tests = safeTests()
            if (report.sent > 0) {
                // The calibration changed with the new tests: read it again.
                val (fresh, raw) = withContext(Dispatchers.IO) { api.info() }
                store.saveInfo(raw)
                info = fresh
                infoAtUnix = System.currentTimeMillis() / 1000.0
            }
            report.stoppedBy?.let { throw it }
        } finally {
            syncing = false
        }
    }

    suspend fun authState(): AuthState = withContext(Dispatchers.IO) { api(siteRoute(), session = false).authState() }

    /** Throws [SiteProblem] with the site's reason (wrong name or password, too many attempts). The owner signed in
     * on the network of now: it becomes one of his. */
    suspend fun signIn(user: String, password: String) {
        val route = withContext(Dispatchers.IO) { siteRoute() } ?: throw SiteProblem.Unreachable()
        withContext(Dispatchers.IO) { api(route, session = false).signIn(user, password) }
        rememberNetwork(route)
        refreshSite(wait = true)
    }

    suspend fun signOut() {
        withContext(Dispatchers.IO) { api().signOut() }
        android.webkit.WebStorage.getInstance().deleteAllData()
        store.forgetSite()
        info = null
        infoAtUnix = null
        siteTests = emptyList()
        siteTestsKnown = false
        siteLink = SiteLink.SIGNED_OUT
    }

    /** Another site: the session, the owner's networks and what came from the old one are forgotten; the tests on
     * the phone stay. */
    suspend fun changeSite(address: String): Boolean {
        val parsed = SiteAddress.parse(address) ?: return false
        if (parsed == siteAddress) return true
        signOut()
        store.forgetSiteNetworks()
        siteNetworks = emptySet()
        siteRequestsAllowed = false
        store.siteAddress = parsed
        siteAddress = parsed
        siteLink = SiteLink.UNKNOWN
        refreshSite(wait = true)
        return true
    }

    // -- tests on the phone --------------------------------------------------------------------------

    fun saveTest(upload: TestUpload) {
        queue.add(upload)
        tests = safeTests()
        refreshSoon()
    }

    fun deleteTest(clientId: String) {
        queue.remove(clientId)
        tests = safeTests()
    }

    fun retryTest(clientId: String) {
        queue.retry(clientId)
        tests = safeTests()
        refreshSoon()
    }

    /** The tests the means use: kept on the site, and the phone's that the site's list does not have yet (waiting,
     * or sent since the list was read). Never refused ones, and never one the site's list showed and no longer has
     * (deleted on the site); a synced one only while the site's list is not known. */
    fun calibrationPoints(): List<TapePoint> {
        val siteIds = siteTests.mapNotNull { it.clientId }.toSet()
        val fromSite = siteTests.filter { it.kept }.mapNotNull { it.point }
        val fromPhone = tests.filter { test ->
            test.upload.clientId !in siteIds && when (test.state) {
                SyncState.LOCAL, SyncState.SENT -> true
                SyncState.SYNCED -> !siteTestsKnown
                SyncState.REFUSED -> false
            }
        }.mapNotNull { it.upload.point }
        return fromSite + fromPhone
    }

    /** A test the site's list showed and no longer has: deleted on the site (or lost there). */
    fun goneFromSite(test: QueuedTest): Boolean =
        test.state == SyncState.SYNCED && siteTestsKnown && siteTests.none { it.clientId == test.upload.clientId }

    private var testDraft: TestDraft? = null

    /** The radar test in progress (kept across the screen being rebuilt, e.g. the phone turned). */
    fun draft(): TestDraft = testDraft ?: TestDraft().also { testDraft = it }

    fun clearDraft() {
        testDraft = null
    }

    // -- the comma and the phone key -----------------------------------------------------------------

    fun finishSetup() {
        store.setupDone = true
        setupDone = true
    }

    /** The phone key, made the first time (Keystore and key generation off the main thread). */
    suspend fun ensurePhoneKey(): String {
        val line = withContext(Dispatchers.IO) { phoneKey.ensure(SshKeys.comment(Build.MODEL ?: "android")) }
        publicKey = line
        return line
    }

    suspend fun newPhoneKey(): String {
        val line = withContext(Dispatchers.IO) { phoneKey.create(SshKeys.comment(Build.MODEL ?: "android")) }
        publicKey = line
        return line
    }

    fun rememberComma(host: String) {
        if (commaAddress != host) {
            store.commaAddress = host
            commaAddress = host
        }
    }

    fun changeCommaAddress(host: String?) {
        store.commaAddress = host
        commaAddress = store.commaAddress
    }

    /** Trust on first use: the owner saw the fingerprint and said this is the comma. */
    fun acceptComma(host: SshHost) {
        store.acceptedFingerprint = host.fingerprint
        acceptedFingerprint = host.fingerprint
        rememberComma(host.host)
    }

    fun forgetComma() {
        radar.stopNow()
        store.forgetComma()
        commaAddress = null
        acceptedFingerprint = null
    }

    fun markOneTapRefused() {
        store.oneTapRefused = true
        oneTapRefused = true
    }

    /** The comma on the phone's networks. A key the site told, found on the comma, is pinned at once: from then on
     * only the owner changes it. */
    suspend fun findComma(): FindResult {
        val subnets = withContext(Dispatchers.IO) { LocalNetworks.subnets(context) }
        val found = CommaFinder(SshCommaConnector()).find(commaAddress, subnets, trustedFingerprints())
        if (found is FindResult.Found && acceptedFingerprint == null) acceptComma(found.comma)
        return found
    }

    /** app/comma_listener.py, bundled at build time. */
    fun listenerScript(): ByteArray = listener ?: context.assets.open("comma_listener.py").use { it.readBytes() }.also { listener = it }

    /** Finds the comma and logs in with the phone key; runs one read-only check (openpilot and its Python). */
    suspend fun testConnection(): ConnectionCheck {
        if (phoneKey.publicLine() == null) return ConnectionCheck.Failed("No phone key yet", "Make the phone key first.")
        val host = when (val found = findComma()) {
            is FindResult.Found -> found.comma
            is FindResult.Choose -> return ConnectionCheck.Choose(found.candidates)
            is FindResult.NoNetwork -> return ConnectionCheck.Failed("No network",
                "This phone is on no Wi-Fi and its hotspot is off. At home: the Wi-Fi of the comma. In the garage: turn the hotspot on.")
            is FindResult.NotFound -> return notFound(found)
        }
        rememberComma(host.host)
        val comma = info?.comma ?: SiteComma.DEFAULT
        return withContext(Dispatchers.IO) {
            val seed = phoneKey.seed() ?: return@withContext ConnectionCheck.Failed("No phone key yet", "Make the phone key first.")
            try {
                CommaShell.open(host.host, host.subnet, trustedFingerprints(), seed).use { shell ->
                    seed.fill(0)
                    val answer = shell.run("if [ -d ${comma.openpilot} ] && [ -x ${comma.python} ]; then echo ok; else echo missing; fi")
                    ConnectionCheck.Connected(host.host, host.fingerprint, answer.trim() == "ok")
                }
            } catch (_: SshProblem.LoginRefused) {
                ConnectionCheck.Failed("The comma does not accept this phone yet",
                    "Found the comma at ${host.host}, but it refused this phone's key. Add the key to the comma (one tap, or GitHub), then test again.")
            } catch (problem: SshProblem.HostKeyRefused) {
                ConnectionCheck.Failed("Not your comma", problem.message ?: "")
            } catch (problem: Exception) {
                ConnectionCheck.Failed("The connection failed", problem.message ?: "Unknown error.")
            } finally {
                seed.fill(0)
            }
        }
    }

    fun notFound(found: FindResult.NotFound): ConnectionCheck.Failed {
        val changed = found.changed
        if (changed != null) {
            return ConnectionCheck.Failed("The comma's key changed?",
                "${changed.host} answered with the SSH key ${changed.fingerprint}, not the comma's. If the comma was reset " +
                    "or reinstalled, forget it in Settings and connect again; otherwise another device has its address.")
        }
        val where = found.scanned.joinToString { it.label }.ifEmpty { "no network" }
        return ConnectionCheck.Failed("No comma found",
            "Looked on $where. Is the comma on, and on the same Wi-Fi or this phone's hotspot? SSH must be on: on the comma, settings › developer › enable SSH.")
    }
}
