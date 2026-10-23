package ch.bodhi.perception

import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import kotlinx.coroutines.delay
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor

/** Keeps the screen on while a radar screen is shown (the phone sits in the car during a test). */
@Composable
fun KeepScreenOn() {
    val view = LocalView.current
    DisposableEffect(view) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
}

/** The phone's clock, four times a second, for "steady 12 s" and stale readings. */
@Composable
fun rememberTicker(): Long {
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = SystemClock.elapsedRealtime()
            delay(250)
        }
    }
    return now
}

/** The listener runs while the calling screen is on screen: started with it, stopped when it goes away or the app
 * goes to the background (the channel closed a moment later, unless another radar screen takes it over). */
@Composable
fun UseRadar(model: AppModel, owner: String) {
    LifecycleStartEffect(owner) {
        model.radar.acquire(owner)
        onStopOrDispose { model.radar.release(owner) }
    }
    KeepScreenOn()
}

/** The radar, live: a listener on the comma while this screen is open, stopped when it goes away. */
@Composable
fun RadarScreen(model: AppModel, onTest: () -> Unit) {
    val radar = model.radar
    UseRadar(model, "radar-tab")
    val now = rememberTicker()
    Column(Modifier.fillMaxSize().background(Site.Background).verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Radar", fontSize = 30.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        ConnectionLine(radar, now)
        when (val phase = radar.phase) {
            is RadarPhase.Choose -> ChooseComma(phase.candidates, onPick = radar::choose)
            is RadarPhase.Stopped -> {
                Callout("${phase.title}. ${phase.detail}", Tone.BAD)
                SecondaryButton("Try again", onClick = radar::retry)
            }
            else -> LiveReading(model, now)
        }
        SectionCard(title = "Last 30 seconds") {
            RadarChart(radar.history, now)
            Note("Raw radar reading in metres; a gap is no target.", center = false)
        }
        CalibrationNote(model)
        PrimaryButton("Test the radar with a tape", onClick = onTest)
    }
}

@Composable
private fun ConnectionLine(radar: RadarSession, now: Long) {
    val (tone, text) = when (val phase = radar.phase) {
        RadarPhase.Idle -> Tone.MUTED to "Not connected"
        RadarPhase.Searching -> Tone.INFO to "Looking for the comma…"
        is RadarPhase.Choose -> Tone.TODO to "Which one is your comma?"
        is RadarPhase.Connecting -> Tone.INFO to "Connecting to ${phase.host}…"
        is RadarPhase.Live -> Tone.OK to buildString {
            append("Connected to ${phase.host}")
            radar.hello?.gitCommit?.let { append(" · openpilot ${it.take(7)}") }
            radar.route?.let { append(" · recording ${it.substringBefore("--")}") }
        }
        is RadarPhase.Waiting -> Tone.TODO to "${phase.reason} Again in ${((phase.retryAtMs - now) / 1000).coerceAtLeast(0)} s."
        is RadarPhase.Stopped -> Tone.BAD to phase.title
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
            .background(Site.Surface).padding(horizontal = 14.dp, vertical = 10.dp)) {
        Box(Modifier.size(9.dp).clip(CircleShape).background(tone.color))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = Site.Text, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun LiveReading(model: AppModel, now: Long) {
    val radar = model.radar
    val phase = radar.phase
    val latest = radar.latest
    val stale = latest == null || now - radar.latestAtMs > 1_500
    SectionCard {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            when {
                phase !is RadarPhase.Live -> {
                    CircularProgressIndicator(Modifier.size(36.dp))
                    Text(when (phase) {
                        is RadarPhase.Waiting -> "Waiting to try again"
                        is RadarPhase.Connecting -> "Starting the reading on the comma…"
                        else -> "Looking for the comma on this phone's networks…"
                    }, color = Site.Muted, textAlign = TextAlign.Center)
                }
                radar.hello?.radarFields == false -> {
                    BigLine("No radar fields", Site.Todo)
                    Text("This openpilot's carState has no stockRadar* fields: there is no radar reading to show.",
                        color = Site.Muted, textAlign = TextAlign.Center)
                }
                radar.onroad == false -> {
                    BigLine("Switch the car on", Site.Todo)
                    Text("The comma is connected. Its radar reading comes only with the car on (ready, or ignition on).",
                        color = Site.Muted, textAlign = TextAlign.Center)
                }
                latest == null || stale -> {
                    BigLine("Waiting for the car…", Site.Muted)
                    Text("The car is on; its data starts in a moment.", color = Site.Muted, textAlign = TextAlign.Center)
                }
                latest.reading == null -> {
                    BigLine("No reading", Site.Muted)
                    Text("Nothing in front of the radar, or out of its range.", color = Site.Muted, textAlign = TextAlign.Center)
                    Speed(latest.vEgo)
                }
                else -> {
                    val reading = latest.reading ?: 0.0
                    val fromComma = RadarMath.fromComma(reading, model.calibration)
                    Text(RadarMath.metres(fromComma ?: reading), fontSize = 64.sp, lineHeight = 70.sp,
                        fontWeight = FontWeight.Bold, color = Site.Text)
                    Text(if (fromComma != null) "from the comma · radar reads ${RadarMath.metres(reading)}"
                    else "radar reading (no calibration yet)", color = Site.Muted, textAlign = TextAlign.Center)
                    val held = radar.steadySeconds
                    val value = radar.steadyValue
                    if (value != null && held >= 1) {
                        Text("steady ${shortSpan(held)} at ${RadarMath.metres(value)}", color = Site.Ok, fontWeight = FontWeight.SemiBold)
                    }
                    Speed(latest.vEgo)
                }
            }
        }
    }
}

@Composable
private fun BigLine(text: String, color: Color) {
    Text(text, fontSize = 34.sp, lineHeight = 40.sp, fontWeight = FontWeight.Bold, color = color, textAlign = TextAlign.Center)
}

@Composable
private fun Speed(vEgo: Double?) {
    if (vEgo == null) return
    val still = abs(vEgo) < CaptureRules.STILL_MPS
    Text(String.format(Locale.ROOT, "speed %.2f m/s", vEgo) + if (still) " · standing still" else " · moving",
        color = if (still) Site.Muted else Site.Todo, style = MaterialTheme.typography.bodyMedium)
}

@Composable
fun CalibrationNote(model: AppModel) {
    val calibration = model.info?.calibration
    val values = calibration?.values
    if (values == null) {
        Note("No calibration from the site yet: the readings are the radar's own. A test with a tape gives one.", center = false)
    } else {
        val when_ = model.infoAtUnix?.let { " (from the site, ${shortDate(it)})" } ?: ""
        Note("From the comma = reading ${RadarMath.signedMetres(values.correctionM)} correction " +
            "+ ${RadarMath.metres(values.radarToCameraM)} radar to comma$when_.", center = false)
    }
}

/** The SSH servers found when no key is trusted yet: the owner picks the comma by its fingerprint. */
@Composable
fun ChooseComma(candidates: List<SshHost>, onPick: (SshHost) -> Unit) {
    SectionCard(title = "Which one is your comma?") {
        Text("The app does not know your comma's key yet (it learns it from the site at the first sync). Accept one only " +
            "on your own hotspot or Wi-Fi. The comma's key fingerprint is also in the site's Settings.", color = Site.Muted)
        candidates.forEach { host ->
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(host.host, fontWeight = FontWeight.SemiBold)
                Mono(host.fingerprint)
                SecondaryButton("This is my comma") { onPick(host) }
            }
        }
    }
}

/** The raw readings of the last 30 s; gaps where the radar had no target. */
@Composable
fun RadarChart(points: List<HistoryPoint>, now: Long) {
    val values = points.mapNotNull { it.readingM }
    val low = if (values.isEmpty()) 0.0 else floor((values.min() - 0.25) * 2) / 2
    val high = if (values.isEmpty()) 5.0 else maxOf(ceil((values.max() + 0.25) * 2) / 2, low + 1.0)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(RadarMath.metres(high), style = MaterialTheme.typography.labelSmall, color = Site.Muted)
            Text(if (values.isEmpty()) "no reading" else "now ${points.lastOrNull()?.readingM?.let(RadarMath::metres) ?: "—"}",
                style = MaterialTheme.typography.labelSmall, color = Site.Muted)
        }
        Canvas(Modifier.fillMaxWidth().height(130.dp).clip(androidx.compose.foundation.shape.RoundedCornerShape(10.dp))
            .background(Site.Surface2)) {
            val width = size.width
            val height = size.height
            for (line in 1..3) {
                val y = height * line / 4f
                drawLine(Color(0x14FFFFFF), Offset(0f, y), Offset(width, y), strokeWidth = 1f)
            }
            fun x(atMs: Long) = width * (1f - ((now - atMs) / 30_000f).coerceIn(0f, 1f))
            fun y(value: Double) = (height * (1 - (value - low) / (high - low))).toFloat()
            val path = Path()
            var drawing = false
            var previous: HistoryPoint? = null
            for (point in points) {
                val reading = point.readingM
                val gap = previous != null && point.atMs - previous.atMs > 400
                if (reading == null) {
                    drawing = false
                } else if (!drawing || gap) {
                    path.moveTo(x(point.atMs), y(reading))
                    drawing = true
                } else {
                    path.lineTo(x(point.atMs), y(reading))
                }
                previous = point
            }
            drawPath(path, Site.Series, style = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round))
            points.lastOrNull()?.readingM?.let { drawCircle(Site.Series, 4.dp.toPx(), Offset(x(points.last().atMs), y(it))) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(RadarMath.metres(low), style = MaterialTheme.typography.labelSmall, color = Site.Muted)
            Text("30 s ago … now", style = MaterialTheme.typography.labelSmall, color = Site.Muted)
        }
    }
}
