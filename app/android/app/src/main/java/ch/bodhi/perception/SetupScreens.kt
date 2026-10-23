package ch.bodhi.perception

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.core.net.toUri
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val SETUP_STEPS = 3

/** The first run, guided, every step skippable: the site (address and sign-in), this phone's key on the comma,
 * the hotspot for the garage. Settings › Run the setup again opens it later. */
@Composable
fun SetupFlow(model: AppModel, onDone: () -> Unit) {
    var step by rememberSaveable { mutableIntStateOf(1) }
    BackHandler(enabled = step > 1) { step -= 1 }
    when (step) {
        1 -> SignInStep(model, 1, SETUP_STEPS, onBack = null, onDone = { step = 2 }, onSkip = { step = 2 })
        2 -> ConnectPhoneStep(model, onBack = { step = 1 }, onNext = { step = 3 })
        else -> GarageStep(onBack = { step = 2 }, onDone = onDone)
    }
}

/** The site's address and its administrator's name and password (POST /api/auth/login). Needs the site
 * reachable: at home or through ZeroTier. */
@Composable
fun SignInStep(model: AppModel, step: Int, count: Int, onBack: (() -> Unit)?, onDone: () -> Unit, onSkip: (() -> Unit)? = null) {
    var address by rememberSaveable { mutableStateOf(model.siteAddress) }
    var user by rememberSaveable { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    var notConfigured by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val addressProblem = SiteAddress.problem(address)
    val signedIn = model.siteLink == SiteLink.ONLINE && SiteAddress.parse(address) == model.siteAddress

    fun submit() {
        if (busy || addressProblem.isNotEmpty() || user.isBlank() || password.isEmpty()) return
        busy = true
        problem = ""
        scope.launch {
            try {
                model.changeSite(address)
                val state = model.authState()
                if (!state.configured) {
                    notConfigured = true
                } else {
                    model.signIn(user.trim(), password)
                    password = ""
                    onDone()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: SiteProblem.Unreachable) {
                problem = "The site does not answer at ${SiteAddress.parse(address) ?: address}. At home or with ZeroTier on? " +
                    (if (onSkip != null) "You can also do this later." else "")
            } catch (error: Exception) {
                problem = error.message ?: "The sign-in did not work."
            } finally {
                busy = false
            }
        }
    }

    StepScreen("Your perception site", "Where the app sends the tests and reads the calibration. The site is reachable at home " +
        "or through ZeroTier; in the garage the app works without it.", step, count, onBack, bottom = {
        if (signedIn) PrimaryButton("Continue", onClick = onDone)
        else PrimaryButton("Sign in", when {
            addressProblem.isNotEmpty() -> addressProblem
            user.isBlank() || password.isEmpty() -> "Type the name and the password of the site's administrator."
            else -> ""
        }, busy = busy, onClick = ::submit)
        onSkip?.let { LinkButton("Skip: do it later in Settings", Site.Muted, it) }
    }) {
        OutlinedTextField(value = address, onValueChange = { address = it; problem = "" }, label = { Text("Site address") },
            singleLine = true, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false, imeAction = ImeAction.Next))
        if (signedIn) {
            Callout("Signed in to ${model.siteAddress.substringAfter("://")} ✓", Tone.OK)
            return@StepScreen
        }
        OutlinedTextField(value = user, onValueChange = { user = it; problem = "" }, label = { Text("Name") },
            singleLine = true, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Next))
        OutlinedTextField(value = password, onValueChange = { password = it; problem = "" }, label = { Text("Password") },
            singleLine = true, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { submit() }))
        if (model.siteLink == SiteLink.NEW_NETWORK && SiteAddress.parse(address) == model.siteAddress) {
            Callout("The site answers on a network you have not used it from (${model.siteNetworkLabel ?: "this one"}). Sign in " +
                "only on your home Wi-Fi or with ZeroTier on: the password goes to whatever answers at this address, and the " +
                "app then uses the site on this network.", Tone.TODO)
        }
        if (notConfigured) {
            Callout("This site has no administrator yet: open it in a browser and create it with the setup code, then sign in here.", Tone.TODO)
        }
        if (problem.isNotEmpty()) Callout(problem, Tone.BAD)
        Note("The app keeps the site's session (as the browser does), never the password.")
    }
}

/** This phone's own SSH key on the comma: one tap through the site (at home), or through GitHub; then a test. */
@Composable
fun ConnectPhoneStep(model: AppModel, onBack: (() -> Unit)?, onNext: () -> Unit, inSetup: Boolean = true) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var oneTap by remember { mutableStateOf<Pair<Tone, String>?>(null) }
    var oneTapBusy by remember { mutableStateOf(false) }
    var testing by remember { mutableStateOf(false) }
    var check by remember { mutableStateOf<ConnectionCheck?>(null) }
    LaunchedEffect(Unit) {
        if (model.publicKey == null) model.ensurePhoneKey()
    }
    val line = model.publicKey

    fun test() {
        testing = true
        check = null
        scope.launch {
            try {
                check = model.testConnection()
            } finally {
                testing = false
            }
        }
    }

    StepScreen("Connect this phone to your comma", "The app made its own SSH key; the private part never leaves this phone. " +
        "The comma needs its public part once.", if (inSetup) 2 else 0, if (inSetup) SETUP_STEPS else 0, onBack, bottom = {
        PrimaryButton("Test the connection", if (line == null) "Making the key…" else "", busy = testing, onClick = ::test)
        if (inSetup) LinkButton(if (check is ConnectionCheck.Connected) "Continue" else "Skip: do it later", Site.Muted, onNext)
    }) {
        check?.let { result ->
            when (result) {
                is ConnectionCheck.Connected -> Callout("Connected to the comma at ${result.host} ✓" +
                    if (result.openpilotFound) " The radar reading will work." else
                        " But openpilot or its Python is not where the site says: the radar may not start.",
                    if (result.openpilotFound) Tone.OK else Tone.TODO)
                is ConnectionCheck.Choose -> ChooseComma(result.candidates) { host ->
                    model.acceptComma(host)
                    test()
                }
                is ConnectionCheck.Failed -> Callout("${result.title}. ${result.detail}", Tone.BAD)
            }
        }
        if (model.fingerprintConflict) CommaKeyConflict(model)
        SectionCard(title = "This phone's key") {
            if (line == null) Text("Making the key…", color = Site.Muted)
            else {
                Mono(line)
                model.phoneKey.fingerprint()?.let { Note(it, center = false) }
                SecondaryButton("Copy the key") { copy(context, line) }
            }
        }
        if (!model.oneTapRefused) {
            SectionCard(title = "One tap, at home (the best way)") {
                Text("The site adds this key to the comma for you. It needs the site (home Wi-Fi or ZeroTier) and the comma on at home. " +
                    "Nothing goes through GitHub.", color = Site.Muted)
                val ready = model.siteLink == SiteLink.ONLINE
                SecondaryButton(if (ready) "Add this phone to the comma" else "Needs the site: sign in first",
                    enabled = ready && line != null, busy = oneTapBusy) {
                    val key = line ?: return@SecondaryButton
                    oneTapBusy = true
                    oneTap = null
                    scope.launch {
                        oneTap = try {
                            val result = withContext(Dispatchers.IO) { model.api().addCommaKey(key) }
                            Tone.OK to (if (result.already) "The comma already had this key ✓" else "Added to the comma ✓") + " Now test the connection."
                        } catch (problem: SiteProblem.Refused) {
                            if (problem.status == 403 || problem.code == "not_allowed") {
                                model.markOneTapRefused()
                                Tone.TODO to "The site does not allow adding keys to the comma: use the GitHub way below."
                            } else Tone.BAD to (problem.message ?: "Refused.")
                        } catch (problem: SiteProblem.Unavailable) {
                            Tone.TODO to if (problem.code == "comma_unreachable") "The site cannot reach the comma now: is it on, at home? Try again then, or use GitHub."
                            else problem.message ?: "The site is busy: try again."
                        } catch (problem: SiteProblem) {
                            Tone.BAD to (problem.message ?: "It did not work.")
                        } finally {
                            oneTapBusy = false
                        }
                    }
                }
                oneTap?.let { (tone, text) -> Callout(text, tone) }
            }
        }
        SectionCard(title = if (model.oneTapRefused) "With GitHub" else "Or with GitHub") {
            Text("While this key is on your GitHub account it can also push to your repositories (the comma's openpilot " +
                "fork too): it stays there only until the comma has it.", color = Site.Muted)
            CheckLine("Copy the key (above).", Tone.INFO)
            CheckLine("On GitHub: Settings › SSH and GPG keys › New SSH key. Title: Perception phone; key type: Authentication " +
                "Key; paste the key; Add SSH key.", Tone.INFO)
            SecondaryButton("Open GitHub") { open(context, "https://github.com/settings/ssh/new") }
            CheckLine("On the comma, with internet (at home): settings › developer › SSH keys. Tap it, keep your GitHub " +
                "username, confirm. The comma downloads your GitHub keys again, now with this phone's.", Tone.INFO)
            CheckLine("On the comma, settings › developer › enable SSH stays on.", Tone.INFO)
            CheckLine("Test the connection, below.", Tone.INFO)
            CheckLine("Once it works: on GitHub, delete \"Perception phone\" (Settings › SSH and GPG keys › Delete). The comma " +
                "keeps its own copy.", Tone.TODO)
            Note("Every download of the GitHub keys on the comma replaces the keys it has with the ones on GitHub then: a " +
                "phone added with one tap, or one whose key left GitHub, is dropped and must be added again. And if the " +
                "download fails (no internet), the comma forgets all its keys.", center = false)
        }
    }
}

/** How the comma joins the phone's hotspot, once: in the garage there is no other network. */
@Composable
fun GarageStep(onBack: (() -> Unit)?, onDone: () -> Unit, inSetup: Boolean = true) {
    StepScreen("For the garage", "No internet there: the comma joins this phone's hotspot, and the app talks to it directly.",
        if (inSetup) 3 else 0, if (inSetup) SETUP_STEPS else 0, onBack,
        bottom = { PrimaryButton(if (inSetup) "Done" else "Close", onClick = onDone) }) {
        SectionCard(title = "Once") {
            CheckLine("On the phone: turn the hotspot on (Settings › Network › Hotspot & tethering). Note its name and password. " +
                "Mobile data can stay off.", Tone.INFO)
            CheckLine("On the comma: settings › network › wi-fi › your phone's hotspot, and its password. The comma remembers it.", Tone.INFO)
        }
        SectionCard(title = "Every time in the garage") {
            CheckLine("Turn the phone's hotspot on, then the car: the comma joins it by itself within a minute.")
            CheckLine("Open Radar: the app finds the comma on the hotspot and shows the reading.")
            CheckLine("Tests made there wait on the phone; they go to the site the next time you open the app at home (or with ZeroTier on).")
        }
        Note("Some phones give the hotspot new addresses every time: the app looks for the comma again by itself.")
    }
}

/** The site tells another key for the comma than the one pinned: the pinned one stays in use until the owner says
 * the comma was reset. */
@Composable
fun CommaKeyConflict(model: AppModel) {
    var asking by remember { mutableStateOf(false) }
    val site = model.info?.comma?.hostKeyFingerprints.orEmpty()
    Callout("The site tells another SSH key for the comma (${site.joinToString()}) than the one this phone trusts " +
        "(${model.acceptedFingerprint}). The app keeps using the one it trusts. Change it only if the comma was reset or " +
        "reinstalled.", Tone.TODO) {
        SecondaryButton("The comma was reset: use the site's key") { asking = true }
    }
    if (asking) {
        Confirm("Use the site's key for the comma?", "The key this phone trusts is forgotten; the next comma that shows " +
            "the site's key is trusted.", "Use the site's key", onConfirm = {
                asking = false
                model.useSiteCommaKey()
            }, onDismiss = { asking = false })
    }
}

fun copy(context: Context, text: String) {
    context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("SSH public key", text))
    Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
}

fun open(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, url.toUri()))
    } catch (_: Exception) {
        Toast.makeText(context, "No browser to open $url", Toast.LENGTH_LONG).show()
    }
}
