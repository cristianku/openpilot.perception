package ch.bodhi.perception

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Radar
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Straighten
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext

class MainActivity : ComponentActivity() {
    private val model: AppModel get() = (application as PerceptionApp).model

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Edge to edge, as Android 15 and later draw every app; light icons on the navy of the site.
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        setContent {
            PerceptionTheme {
                Root(model)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        model.onForeground()
    }

    override fun onStop() {
        model.onBackground()
        super.onStop()
    }
}

private enum class Tab(val label: String) { RADAR("Radar"), TESTS("Tests"), SITE("Site"), SETTINGS("Settings") }

/** Full screens over the tabs: the radar test, the setup again, the sign-in. */
private enum class Overlay { TEST, SETUP, SIGN_IN }

@Composable
private fun Root(model: AppModel) {
    val context = LocalContext.current
    val webView = remember { siteWebView(context) }
    var tab by rememberSaveable { mutableStateOf(Tab.RADAR) }
    var overlay by rememberSaveable { mutableStateOf<Overlay?>(null) }
    val full = Modifier.fillMaxSize().background(Site.Background).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()

    if (!model.setupDone) {
        Box(full) { SetupFlow(model, onDone = { model.finishSetup() }) }
        return
    }
    when (overlay) {
        Overlay.TEST -> {
            Box(full) {
                TestWizard(model, onClose = { overlay = null }, onSaved = {
                    overlay = null
                    tab = Tab.TESTS
                })
            }
            return
        }
        Overlay.SETUP -> {
            BackHandler { overlay = null }
            Box(full) { SetupFlow(model, onDone = { overlay = null }) }
            return
        }
        Overlay.SIGN_IN -> {
            BackHandler { overlay = null }
            Box(full) {
                SignInStep(model, step = 0, count = 0, onBack = { overlay = null }, onDone = {
                    overlay = null
                    webView.reload()
                })
            }
            return
        }
        null -> Unit
    }
    Scaffold(
        containerColor = Site.Background,
        bottomBar = {
            NavigationBar(containerColor = Site.Surface) {
                Tab.entries.forEach { item ->
                    NavigationBarItem(
                        selected = tab == item,
                        onClick = { tab = item },
                        icon = {
                            val icon = when (item) {
                                Tab.RADAR -> Icons.Outlined.Radar
                                Tab.TESTS -> Icons.Outlined.Straighten
                                Tab.SITE -> Icons.Outlined.Language
                                Tab.SETTINGS -> Icons.Outlined.Settings
                            }
                            if (item == Tab.TESTS && model.pendingCount > 0) {
                                BadgedBox(badge = { Badge { Text("${model.pendingCount}") } }) {
                                    Icon(icon, contentDescription = item.label)
                                }
                            } else {
                                Icon(icon, contentDescription = item.label)
                            }
                        },
                        label = { Text(item.label, maxLines = 1) }
                    )
                }
            }
        }
    ) { padding ->
        val screen = Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding).imePadding()
        Box(screen) {
            when (tab) {
                Tab.RADAR -> RadarScreen(model, onTest = { overlay = Overlay.TEST })
                Tab.TESTS -> TestsScreen(model, onTest = { overlay = Overlay.TEST }, onSignIn = { overlay = Overlay.SIGN_IN })
                Tab.SITE -> SiteScreen(model, webView)
                Tab.SETTINGS -> SettingsScreen(model, onSignIn = { overlay = Overlay.SIGN_IN },
                    onSetup = { overlay = Overlay.SETUP }, onSignedOut = { webView.loadUrl("about:blank"); webView.clearHistory(); webView.clearCache(true) })
            }
        }
    }
}
