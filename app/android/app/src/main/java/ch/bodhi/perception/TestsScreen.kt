package ch.bodhi.perception

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Every test made on this phone with where it is (on the phone, on the site, refused and why), the site's own
 * tests, and the calibration as the site last said it. */
@Composable
fun TestsScreen(model: AppModel, onTest: () -> Unit, onSignIn: () -> Unit) {
    var deleting by remember { mutableStateOf<QueuedTest?>(null) }
    val local = model.tests.sortedByDescending { it.upload.createdUnix }
    val mine = local.map { it.upload.clientId }.toSet()
    val fromSite = model.siteTests.filter { it.clientId == null || it.clientId !in mine }
    LazyColumn(Modifier.fillMaxSize().background(Site.Background), contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Text("Tests", fontSize = 30.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        }
        item { CalibrationCard(model) }
        item { SyncCard(model, onSignIn) }
        item { PrimaryButton("New radar test", onClick = onTest) }
        if (local.isNotEmpty()) {
            item { Text("ON THIS PHONE", style = MaterialTheme.typography.labelLarge, color = Site.Muted) }
            items(local, key = { it.upload.clientId }) { test -> LocalTestRow(model, test, onDelete = { deleting = test }) }
        }
        if (fromSite.isNotEmpty()) {
            item { Text("ON THE SITE", style = MaterialTheme.typography.labelLarge, color = Site.Muted) }
            items(fromSite) { test -> SiteTestRow(test) }
        }
        if (local.isEmpty() && fromSite.isEmpty()) {
            item { Note("No test yet. One takes about 10 minutes with a tape measure, in front of a wall or a parked car.") }
        }
    }
    deleting?.let { test ->
        Confirm("Delete this test?", when {
            test.state == SyncState.SYNCED && !model.goneFromSite(test) -> "It goes from this phone only: the site keeps its copy."
            test.state == SyncState.SENT -> "It goes from this phone; the site may have it already (see the site's tests)."
            else -> "It is not on the site: it is gone for good."
        }, "Delete", onConfirm = {
            model.deleteTest(test.upload.clientId)
            deleting = null
        }, onDismiss = { deleting = null })
    }
}

@Composable
fun CalibrationCard(model: AppModel) {
    val calibration = model.info?.calibration
    SectionCard(title = "Calibration") {
        if (calibration == null) {
            Text("Not known yet: the site tells it at the first sync.", color = Site.Muted)
            return@SectionCard
        }
        val (tone, label) = when (calibration.status) {
            "ready" -> Tone.OK to "Ready"
            "close_only" -> Tone.TODO to "Checked close up only"
            "typed" -> Tone.TODO to "Typed by hand"
            else -> Tone.BAD to "Not set"
        }
        Pill(label, tone)
        calibration.values?.let { values ->
            Text("Radar to comma ${RadarMath.metres(values.radarToCameraM)} · correction ${RadarMath.signedMetres(values.correctionM)}" +
                (calibration.uncertaintyM?.let { " ± ${RadarMath.format2(it)} m" } ?: ""), fontWeight = FontWeight.SemiBold)
        }
        if (calibration.testedM.isNotEmpty()) {
            Text("Tested at " + calibration.testedM.joinToString { RadarMath.metres(it) }, color = Site.Muted)
        }
        if (calibration.status == "close_only" || calibration.status == "typed") {
            Text("A test at 5 m or more (the parked car) checks it where the labels will be.", color = Site.Muted)
        }
        model.infoAtUnix?.let { Note("From the site, ${shortDate(it)}.", center = false) }
    }
}

@Composable
private fun SyncCard(model: AppModel, onSignIn: () -> Unit) {
    val pending = model.pendingCount
    val refused = model.tests.count { it.state == SyncState.REFUSED }
    val gone = model.tests.count { model.goneFromSite(it) }
    val waiting = if (pending > 0) "$pending test${if (pending == 1) "" else "s"} waiting" else ""
    // What stops the sending comes first: a problem that needs someone is never hidden behind "waiting".
    val (tone, text) = when {
        model.syncing -> Tone.INFO to "Sending the tests to the site…"
        model.siteLink == SiteLink.SIGNED_OUT -> Tone.TODO to (if (pending > 0) "$waiting: sign in to the site to send them."
            else "Signed out of the site.")
        model.siteLink == SiteLink.PROBLEM -> Tone.BAD to (if (pending > 0) "$waiting. ${model.siteProblem}" else model.siteProblem)
        model.siteLink == SiteLink.NEW_NETWORK -> Tone.TODO to ((if (pending > 0) "$waiting. " else "") +
            "The site answers on a network you have not used it from (${model.siteNetworkLabel ?: "this one"}): nothing is " +
            "sent there until you say it is yours.")
        pending > 0 && model.siteLink == SiteLink.ONLINE -> Tone.INFO to "$waiting to be sent."
        pending > 0 -> Tone.TODO to "$waiting on the phone: ${if (pending == 1) "it goes" else "they go"} to the site the next " +
            "time the app is open where the site answers (home Wi-Fi or ZeroTier)."
        model.siteLink == SiteLink.ONLINE -> Tone.OK to "Everything is on the site."
        else -> Tone.MUTED to "The site is not reachable from here."
    }
    Callout(text + (if (refused > 0) " $refused refused: see below." else "") +
        (if (gone > 0) " $gone no longer on the site: see below." else ""), tone) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (model.siteLink == SiteLink.NEW_NETWORK) UseThisNetwork(model)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (model.siteLink == SiteLink.SIGNED_OUT) TextButton(onClick = onSignIn) { Text("Sign in") }
                TextButton(onClick = { model.refreshSoon() }, enabled = !model.syncing) { Text("Sync now") }
            }
        }
    }
}

@Composable
private fun LocalTestRow(model: AppModel, test: QueuedTest, onDelete: () -> Unit) {
    val upload = test.upload
    val reading = upload.capture.readingM
    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${upload.target.title} · tape ${RadarMath.metres(upload.tapeRadarM)}", fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            val gone = model.goneFromSite(test)
            when {
                gone -> Pill("No longer on the site", Tone.TODO)
                test.state == SyncState.LOCAL -> Pill("On the phone", Tone.TODO)
                test.state == SyncState.SENT -> Pill("Sent", Tone.INFO)
                test.state == SyncState.SYNCED -> Pill("On the site", Tone.OK)
                else -> Pill("Refused", Tone.BAD)
            }
        }
        Text(buildString {
            reading?.let { append("Radar ${RadarMath.metres(it)} · ${RadarMath.verdict(RadarMath.round2(upload.tapeRadarM - it)).replaceFirstChar { c -> c.lowercase() }}") }
            append(" · comma at ${RadarMath.metres(upload.tapeCommaM)}")
        }, color = Site.Muted)
        Text(shortDate(upload.createdUnix) + (upload.comma.route?.let { " · recording ${it.substringBefore("--")}" } ?: ""),
            style = MaterialTheme.typography.bodySmall, color = Site.Muted)
        if (upload.note.isNotBlank()) Text(upload.note, style = MaterialTheme.typography.bodySmall)
        val gone = model.goneFromSite(test)
        if (test.state == SyncState.REFUSED) Callout("The site said: ${test.reason}", Tone.BAD)
        if (gone) Note("The site's list no longer has it: deleted there, or lost. The means leave it out; send it again " +
            "to put it back on the site.", center = false)
        if (test.state == SyncState.SENT) Note("The site took it; it counts once the site's list shows it.", center = false)
        if (test.state == SyncState.LOCAL && test.attempts > 0) {
            Note("Tried ${test.attempts} time${if (test.attempts == 1) "" else "s"}, last ${shortDate(test.lastAttemptUnix)}: it " +
                "is tried again while the app is open where the site answers.", center = false)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (test.state == SyncState.REFUSED || gone) TextButton(onClick = { model.retryTest(upload.clientId) }) { Text("Send again") }
            TextButton(onClick = onDelete) { Text("Delete", color = Site.Bad) }
        }
    }
}

@Composable
private fun SiteTestRow(test: SiteTest) {
    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${test.target.title} · tape ${test.tapeRadarM?.let(RadarMath::metres) ?: "—"}", fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Pill(if (test.kept) "Kept" else "Left out", if (test.kept) Tone.OK else Tone.MUTED)
        }
        Text(buildString {
            test.readingM?.let { append("Radar ${RadarMath.metres(it)}") }
            test.correctionM?.let { append(" · ${RadarMath.verdict(it).replaceFirstChar { c -> c.lowercase() }}") }
        }, color = Site.Muted)
        Text(shortDate(test.createdUnix) + (test.route?.let { " · recording ${it.substringBefore("--")}" } ?: ""),
            style = MaterialTheme.typography.bodySmall, color = Site.Muted)
    }
}
