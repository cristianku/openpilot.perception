package ch.bodhi.perception

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.os.Environment
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.GppMaybe
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.net.toUri
import kotlinx.coroutines.launch
import java.io.ByteArrayInputStream

/** One WebView for the whole app, so switching tabs keeps the page, its scroll and its sign-in. */
@SuppressLint("SetJavaScriptEnabled")
fun siteWebView(context: Context): WebView = WebView(context).apply {
    setBackgroundColor(0xFF0B0F16.toInt())
    settings.javaScriptEnabled = true
    settings.domStorageEnabled = true
    settings.allowFileAccess = false
    settings.allowContentAccess = false
    settings.setSupportMultipleWindows(false)
    settings.javaScriptCanOpenWindowsAutomatically = false
    settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
    settings.setGeolocationEnabled(false)
    // Without a WebChromeClient every confirm() of the site answers "no" at once (and alert() shows nothing):
    // the site asks before an update, a stop, a delete, a sign-out.
    webChromeClient = SiteDialogs()
    CookieManager.getInstance().setAcceptCookie(true)
    CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
}

/** alert(), confirm() and prompt() of the site as Android dialogs. */
private class SiteDialogs : WebChromeClient() {
    private fun builder(view: WebView, message: String?): AlertDialog.Builder =
        AlertDialog.Builder(view.context, android.R.style.Theme_DeviceDefault_Dialog_Alert).setMessage(message.orEmpty())

    private fun show(result: JsResult, build: () -> AlertDialog.Builder): Boolean {
        try {
            build().setOnCancelListener { result.cancel() }.show()
        } catch (_: Exception) {
            // The screen is going away: the page gets "no".
            result.cancel()
        }
        return true
    }

    override fun onJsAlert(view: WebView, url: String?, message: String?, result: JsResult): Boolean = show(result) {
        builder(view, message).setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
    }

    override fun onJsConfirm(view: WebView, url: String?, message: String?, result: JsResult): Boolean = show(result) {
        builder(view, message)
            .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
            .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
    }

    override fun onJsPrompt(view: WebView, url: String?, message: String?, defaultValue: String?, result: JsPromptResult): Boolean {
        val field = EditText(view.context).apply { setText(defaultValue.orEmpty()) }
        return show(result) {
            builder(view, message).setView(field)
                .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm(field.text.toString()) }
                .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
        }
    }
}

/** The Site tab: the whole site, signed in with the app's session, when the phone reaches it on one of the
 * owner's networks; a question on a network he has not used the site from; a clear offline screen when the
 * site does not answer (the radar and the tests work without it). */
@Composable
fun SiteScreen(model: AppModel, webView: WebView) {
    var failed by remember { mutableStateOf(false) }
    // The app held a request of the page back (the network changed): the page is loaded again once the site is
    // checked again.
    LaunchedEffect(model.siteOkCount) {
        if (model.siteLink != SiteLink.ONLINE) return@LaunchedEffect
        val wasBlocked = model.siteBlocked.getAndSet(false)
        if (failed || wasBlocked) {
            failed = false
            webView.reload()
        }
    }
    if (model.siteLink == SiteLink.NEW_NETWORK) {
        NewNetwork(model)
        return
    }
    // Signed out and a problem show the site (its own sign-in page, or what it says); a refresh in the background
    // never takes the page off the screen (the keyboard would close while the owner types).
    val reachable = model.siteLink == SiteLink.ONLINE || model.siteLink == SiteLink.SIGNED_OUT || model.siteLink == SiteLink.PROBLEM
    if (!reachable || failed) {
        Offline(model, checking = model.siteLink == SiteLink.CHECKING || model.siteLink == SiteLink.UNKNOWN) {
            failed = false
            model.refreshSoon()
            webView.reload()
        }
        return
    }
    Column(Modifier.fillMaxSize()) {
        if (model.siteLink == SiteLink.PROBLEM && model.siteProblem.isNotEmpty()) {
            Box(Modifier.fillMaxWidth().background(Site.TodoSoft).padding(10.dp)) {
                Text(model.siteProblem, color = Site.Todo, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
            }
        }
        Portal(model, webView, onFailed = { failed = true }, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun Offline(model: AppModel, checking: Boolean, onRetry: () -> Unit) {
    Column(Modifier.fillMaxSize().background(Site.Background).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally) {
        if (checking) CircularProgressIndicator(Modifier.size(40.dp))
        else Icon(Icons.Outlined.CloudOff, contentDescription = null, tint = Site.Muted, modifier = Modifier.size(56.dp))
        Text(if (checking) "Looking for the site…" else "The site is not reachable from here", fontSize = 24.sp,
            fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
        Text("${model.siteAddress.substringAfter("://")} answers on your home Wi-Fi or through ZeroTier. In the garage " +
            "the Radar and Tests tabs work without it: tests wait on the phone and go to the site the next time the app " +
            "is open where the site answers.",
            color = Site.Muted, textAlign = TextAlign.Center)
        model.infoAtUnix?.let { Note("Calibration from the site as of ${shortDate(it)}.") }
        SecondaryButton("Try again", busy = checking, onClick = onRetry)
    }
}

/** The site answers on a network the owner has not used it from: the app keeps its session and its trust to
 * itself until he says the network is his. */
@Composable
private fun NewNetwork(model: AppModel) {
    Column(Modifier.fillMaxSize().background(Site.Background).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Outlined.GppMaybe, contentDescription = null, tint = Site.Todo, modifier = Modifier.size(56.dp))
        Text("Is this your network?", fontSize = 24.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
        Text("The site's address answers here (${model.siteNetworkLabel ?: "this network"}), but you have not used the site " +
            "from this network yet. Until you say it is yours, the app sends it neither your session nor your tests.",
            color = Site.Muted, textAlign = TextAlign.Center)
        Text("Yes only on your home Wi-Fi or with ZeroTier on. On a café, work or phone network another machine can " +
            "answer at the same address and pose as the site.", color = Site.Muted, textAlign = TextAlign.Center)
        UseThisNetwork(model)
        Note("In the garage the Radar and Tests tabs work without the site.")
    }
}

/** "This is my network", with a confirmation: the Site tab, the Tests tab and Settings offer it. */
@Composable
fun UseThisNetwork(model: AppModel) {
    val scope = rememberCoroutineScope()
    var asking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    SecondaryButton("This is my network: use the site here", busy = busy) { asking = true }
    if (asking) {
        Confirm("Use the site on this network?", "${model.siteNetworkLabel ?: "This network"}. From now on the app sends its " +
            "session and your tests to ${model.siteAddress.substringAfter("://")} on it. Only for your home Wi-Fi or ZeroTier.",
            "Use it here", danger = false, onConfirm = {
                asking = false
                busy = true
                scope.launch {
                    try {
                        model.useSiteOnThisNetwork()
                    } finally {
                        busy = false
                    }
                }
            }, onDismiss = { asking = false })
    }
}

@Composable
private fun Portal(model: AppModel, webView: WebView, onFailed: () -> Unit, modifier: Modifier) {
    val base = model.siteAddress
    var canGoBack by remember { mutableStateOf(webView.canGoBack()) }
    BackHandler(enabled = canGoBack) { webView.goBack() }
    AndroidView(
        modifier = modifier,
        factory = { context ->
            (webView.parent as? ViewGroup)?.removeView(webView)
            webView.webViewClient = object : WebViewClient() {
                // Only the configured site inside the app; other links open in the browser.
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (!request.isForMainFrame) return false
                    val url = request.url.toString()
                    if (SiteAddress.sameOrigin(url, base)) return false
                    if (request.url.scheme == "https" || request.url.scheme == "http") {
                        try {
                            context.startActivity(Intent(Intent.ACTION_VIEW, request.url))
                        } catch (_: Exception) {
                        }
                    }
                    return true
                }

                // Nothing but the configured site is loaded inside the app (plain HTTP to it only), and the site
                // only on the owner's networks: the cookie never goes to another machine at its address.
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                    val scheme = request.url.scheme
                    if (scheme != "http" && scheme != "https") return null
                    if (!SiteAddress.sameOrigin(request.url.toString(), base) || !model.siteRequestsAllowed) {
                        if (SiteAddress.sameOrigin(request.url.toString(), base)) model.siteBlocked.set(true)
                        return WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                    }
                    return null
                }

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    canGoBack = view.canGoBack()
                }

                override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                    canGoBack = view.canGoBack()
                }

                override fun onPageFinished(view: WebView, url: String?) {
                    // A sign-in on the site is the app's session too: keep it, and read the site again.
                    CookieManager.getInstance().flush()
                    if (model.siteLink != SiteLink.ONLINE) model.refreshSoon()
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) onFailed()
                }
            }
            // The APK the site offers (GET /download/android) and any other file: to the phone's downloads, with
            // the session, from the site only.
            webView.setDownloadListener { url, userAgent, disposition, mimeType, _ ->
                if (SiteAddress.sameOrigin(url, base) && model.siteRequestsAllowed) download(context, url, userAgent, disposition, mimeType)
            }
            val current = webView.url
            if (current == null || !SiteAddress.sameOrigin(current, base)) webView.loadUrl("$base/")
            webView
        },
        update = {}
    )
}

/** A file of the site through the system's DownloadManager, with the session cookie; the notification opens it
 * (an APK: the installer). */
private fun download(context: Context, url: String, userAgent: String?, disposition: String?, mimeType: String?) {
    val name = URLUtil.guessFileName(url, disposition, mimeType)
    try {
        val request = DownloadManager.Request(url.toUri())
            .setTitle(name)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, name)
        if (!mimeType.isNullOrBlank()) request.setMimeType(mimeType)
        if (!userAgent.isNullOrBlank()) request.addRequestHeader("User-Agent", userAgent)
        CookieManager.getInstance().getCookie(url)?.let { request.addRequestHeader("Cookie", it) }
        context.getSystemService(DownloadManager::class.java)!!.enqueue(request)
        Toast.makeText(context, "Downloading $name: open it from the notification when it is done.", Toast.LENGTH_LONG).show()
    } catch (error: Exception) {
        Toast.makeText(context, "The download did not start: ${error.message ?: "unknown error"}", Toast.LENGTH_LONG).show()
    }
}
