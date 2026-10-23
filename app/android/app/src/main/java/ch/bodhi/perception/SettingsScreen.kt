package ch.bodhi.perception

import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

private enum class SettingsPage { PHONE_KEY, GARAGE, LICENCES }

@Composable
fun SettingsScreen(model: AppModel, onSignIn: () -> Unit, onSetup: () -> Unit, onSignedOut: () -> Unit) {
    var page by rememberSaveable { mutableStateOf<SettingsPage?>(null) }
    when (page) {
        SettingsPage.PHONE_KEY -> {
            BackHandler { page = null }
            ConnectPhoneStep(model, onBack = { page = null }, onNext = { page = null }, inSetup = false)
            return
        }
        SettingsPage.GARAGE -> {
            BackHandler { page = null }
            GarageStep(onBack = { page = null }, onDone = { page = null }, inSetup = false)
            return
        }
        SettingsPage.LICENCES -> {
            BackHandler { page = null }
            Licences(onBack = { page = null })
            return
        }
        null -> Unit
    }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var address by rememberSaveable { mutableStateOf(model.siteAddress) }
    var commaAddress by rememberSaveable { mutableStateOf(model.commaAddress ?: "") }
    var confirm by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().background(Site.Background).verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Settings", fontSize = 30.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())

        SectionCard(title = "Site") {
            Text(when (model.siteLink) {
                SiteLink.ONLINE -> "Signed in, reachable"
                SiteLink.SIGNED_OUT -> "Reachable, signed out"
                SiteLink.OFFLINE -> "Not reachable from here"
                SiteLink.PROBLEM -> model.siteProblem
                SiteLink.NEW_NETWORK -> "It answers on a network you have not used it from: ${model.siteNetworkLabel ?: "this one"}. " +
                    "Nothing is sent there until you say it is yours."
                else -> "Checking…"
            }, color = Site.Muted)
            if (model.siteLink == SiteLink.NEW_NETWORK) UseThisNetwork(model)
            if (model.siteNetworks.isNotEmpty()) {
                Note("Used on ${model.siteNetworks.size} network${if (model.siteNetworks.size == 1) "" else "s"} of yours" +
                    (model.siteNetworkLabel?.let { "; now: $it" } ?: "") + ".", center = false)
            }
            OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("Site address") }, singleLine = true,
                shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
                isError = SiteAddress.problem(address).isNotEmpty(),
                supportingText = { SiteAddress.problem(address).takeIf { it.isNotEmpty() }?.let { Text(it) } })
            if (SiteAddress.parse(address) != null && SiteAddress.parse(address) != model.siteAddress) {
                SecondaryButton("Use this address (signs out of the old one)", busy = busy) {
                    busy = true
                    scope.launch {
                        try {
                            model.changeSite(address)
                            onSignedOut()
                        } finally {
                            busy = false
                        }
                    }
                }
            }
            if (model.siteLink == SiteLink.ONLINE) {
                SecondaryButton("Sign out") { confirm = "signout" }
            } else {
                SecondaryButton("Sign in", onClick = onSignIn)
            }
        }

        SectionCard(title = "Comma") {
            val trusted = model.trustedFingerprints()
            Text(when {
                model.acceptedFingerprint != null -> "Its SSH key, the one this phone trusts:"
                trusted.isNotEmpty() -> "Its SSH key as the site tells it (trusted for good at the first connection):"
                else -> "Its SSH key is not known yet: the site tells it at the first sync, or you accept it when the app finds the comma."
            }, color = Site.Muted)
            trusted.forEach { Mono(it) }
            if (model.fingerprintConflict) CommaKeyConflict(model)
            OutlinedTextField(value = commaAddress, onValueChange = { commaAddress = it.trim() }, label = { Text("Comma address (found by itself)") },
                singleLine = true, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                isError = commaAddress.isNotEmpty() && Ipv4.parse(commaAddress) == null,
                supportingText = { Text("The last address that worked is tried first; the app looks on the hotspot and the Wi-Fi otherwise.") })
            if (commaAddress != (model.commaAddress ?: "") && (commaAddress.isEmpty() || Ipv4.parse(commaAddress) != null)) {
                SecondaryButton("Save the address") { model.changeCommaAddress(commaAddress.ifEmpty { null }) }
            }
            if (model.radar.starts > 0) {
                Note("Readings started on the comma since the app opened: ${model.radar.starts}" +
                    (if (model.radar.startsThisDrive > 0) "; ${model.radar.startsThisDrive} during this drive (the app pauses " +
                        "at ${StartBudget.MAX_PER_DRIVE} to leave openpilot its room)" else "") + ".", center = false)
            }
            SecondaryButton("Forget the comma (address and accepted key)") { confirm = "forget" }
            SecondaryButton("The garage: the comma on this phone's hotspot") { page = SettingsPage.GARAGE }
        }

        SectionCard(title = "Phone key") {
            val line = model.publicKey
            if (line == null) {
                Text("No key yet.", color = Site.Muted)
                SecondaryButton("Make the key and add it to the comma") { page = SettingsPage.PHONE_KEY }
            } else {
                Mono(line)
                model.phoneKey.fingerprint()?.let { Note(it, center = false) }
                SecondaryButton("Copy the public key") { copy(context, line) }
                SecondaryButton("Add it to the comma, test the connection") { page = SettingsPage.PHONE_KEY }
                SecondaryButton("Make a new key") { confirm = "newkey" }
            }
        }

        SectionCard(title = "About") {
            Text("Perception ${versionOf(context)}", fontWeight = FontWeight.SemiBold)
            Text("The app of openpilot.perception: the comma's radar, live, and the radar calibration test, with or without " +
                "internet. It reads the comma over SSH and never writes there: the only thing it runs is a read-only listener " +
                "while a radar screen is open.", color = Site.Muted)
            SecondaryButton("Licences of the libraries") { page = SettingsPage.LICENCES }
            SecondaryButton("Run the setup again", onClick = onSetup)
        }
    }
    when (confirm) {
        "signout" -> Confirm("Sign out?", "The session ends on the site too. The tests on this phone stay and wait.", "Sign out", onConfirm = {
            confirm = null
            scope.launch {
                model.signOut()
                onSignedOut()
            }
        }, onDismiss = { confirm = null })
        "forget" -> Confirm("Forget the comma?", "Its address and the key you accepted are forgotten; the app looks for it again " +
            "next time.", "Forget", onConfirm = {
            confirm = null
            model.forgetComma()
            commaAddress = ""
        }, onDismiss = { confirm = null })
        "newkey" -> Confirm("Make a new key?", "The comma accepts only the key it has: add the new one to it (one tap or GitHub). " +
            "Remove the old one from GitHub too.", "Make a new key", onConfirm = {
            confirm = null
            scope.launch {
                model.newPhoneKey()
                page = SettingsPage.PHONE_KEY
            }
        }, onDismiss = { confirm = null })
    }
}

private fun versionOf(context: android.content.Context): String = try {
    val info = if (Build.VERSION.SDK_INT >= 33) context.packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(0))
    else @Suppress("DEPRECATION") context.packageManager.getPackageInfo(context.packageName, 0)
    "${info.versionName} (${if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()})"
} catch (_: Exception) {
    ""
}

/** The libraries in the app and their licences, with the full texts the licences ask to ship
 * (assets/third_party_licences.txt). */
@Composable
private fun Licences(onBack: () -> Unit) {
    val context = LocalContext.current
    val texts = remember {
        try {
            context.assets.open("third_party_licences.txt").use { it.readBytes().toString(Charsets.UTF_8) }
        } catch (_: Exception) {
            ""
        }
    }
    StepScreen("Licences", "Perception is MIT-licensed, like openpilot.perception. It includes:", onBack = onBack) {
        listOf(
            "sshj 0.41.2, asn-one 0.6.0 (com.hierynomus) — Apache License 2.0" to "SSH to the comma",
            "Bouncy Castle 1.84: bcprov, bcpkix, bcutil (jdk18on) — Bouncy Castle Licence (MIT)" to "Ed25519, X25519, ChaCha20-Poly1305",
            "SLF4J API 2.0.17 — MIT License" to "logging interface of sshj (no logger is bundled: nothing is logged)",
            "AndroidX, Jetpack Compose, Material 3, Guava ListenableFuture 1.0 — Apache License 2.0" to "the user interface",
            "Kotlin standard library, kotlinx.coroutines, JetBrains annotations 23.0.0, JSpecify 1.0.0 — Apache License 2.0" to "the language",
        ).forEach { (name, use) ->
            SectionCard {
                Text(name, fontWeight = FontWeight.SemiBold)
                Text(use, color = Site.Muted)
            }
        }
        if (texts.isNotEmpty()) {
            SectionCard(title = "The licence texts") {
                Text(texts, style = androidx.compose.material3.MaterialTheme.typography.bodySmall, color = Site.Muted)
            }
        }
    }
}
