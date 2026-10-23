package ch.bodhi.perception

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Garage
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
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
import kotlinx.coroutines.launch
import java.util.UUID
import kotlin.math.abs
import kotlin.math.roundToInt

/** A radar test in progress; kept by the app (not the screen), so turning the phone loses nothing. */
class TestDraft {
    val clientId: String = UUID.randomUUID().toString()
    var step by mutableIntStateOf(1)
    var target by mutableStateOf(Target.WALL)
    var targetChosen = false
    var capture by mutableStateOf<CaptureResult?>(null)
    var comma by mutableStateOf(CommaRef(null, null))
    var tapeRadar by mutableStateOf<Double?>(null)
    var tapeComma by mutableStateOf<Double?>(null)
    var note by mutableStateOf("")
}

private const val STEPS = 6

/** The radar test with a tape, fully offline: what the radar looks at, park, 20 s captured from the live stream,
 * the two tapes, the result; saved on the phone and sent to the site when the app is open where it answers. */
@Composable
fun TestWizard(model: AppModel, onClose: () -> Unit, onSaved: () -> Unit) {
    val draft = model.draft()
    var leaving by remember { mutableStateOf(false) }
    fun back() {
        if (draft.step == 1) leaving = true else draft.step -= 1
    }
    BackHandler { back() }
    // The comma stays connected for the whole test (one listener, not one per step, and none again for "Capture
    // again" after the tapes): each start takes a carState reader slot on the comma until the next ignition.
    UseRadar(model, "radar-test")
    when (draft.step) {
        1 -> TargetStep(model, draft, onBack = ::back)
        2 -> ParkStep(draft, onBack = ::back)
        3 -> CaptureStep(model, draft, onBack = ::back)
        4 -> TapeRadarStep(model, draft, onBack = ::back)
        5 -> TapeCommaStep(model, draft, onBack = ::back)
        else -> ResultStep(model, draft, onBack = ::back, onSave = { upload ->
            model.saveTest(upload)
            model.clearDraft()
            onSaved()
        }, onThrowAway = { leaving = true })
    }
    if (leaving) {
        Confirm("Leave the test?", if (draft.capture != null) "The capture and the tapes typed so far are thrown away." else
            "Nothing of this test is kept.", "Leave", onConfirm = {
            model.clearDraft()
            onClose()
        }, onDismiss = { leaving = false })
    }
}

@Composable
private fun TargetStep(model: AppModel, draft: TestDraft, onBack: () -> Unit) {
    val points = model.calibrationPoints()
    val needFar = points.none { it.tapeRadarM >= RadarMath.FAR_TEST_M }
    // As the site: the far test is offered first once close tests exist and none is far yet.
    val target = if (draft.targetChosen) draft.target else if (points.isNotEmpty() && needFar) Target.CAR else Target.WALL
    StepScreen("What will the radar look at?", "A test takes about 10 minutes and a tape measure. It works without internet.",
        1, STEPS, onBack, bottom = { PrimaryButton("Continue") { draft.target = target; draft.targetChosen = true; draft.step = 2 } }) {
        ChoiceCard("A wall, close", "1.5 to 2 m from a garage wall or a closed door. The easiest one.", Icons.Outlined.Garage,
            target == Target.WALL) { draft.target = Target.WALL; draft.targetChosen = true }
        ChoiceCard("A parked car, farther", "About 10 m behind a parked car.", Icons.Outlined.DirectionsCar,
            target == Target.CAR, tag = if (needFar) "Needed" else null) { draft.target = Target.CAR; draft.targetChosen = true }
        Note(if (target == Target.WALL) "Tells how much the radar reads too long, close up."
        else "Checks the correction where the labels will be, at 5–30 m.")
    }
}

@Composable
private fun ParkStep(draft: TestDraft, onBack: () -> Unit) {
    val car = draft.target == Target.CAR
    StepScreen(if (car) "Park behind the other car" else "Park facing the wall",
        if (car) "In a straight line, about 10 m behind it, on flat ground." else "Straight on, with the bumper about 1.5–2 m from the wall.",
        2, STEPS, onBack, bottom = { PrimaryButton("I'm parked, the car is on") { draft.step = 3 } }) {
        SectionCard {
            (if (car) listOf("Both cars straight, one behind the other.", "The other car stays still; switched off is fine.",
                "You need a 10 m tape or a laser meter.")
            else listOf("Stop straight, not at an angle.", "Nothing and nobody between the car and the wall.")).forEach { CheckLine(it) }
            CheckLine("Keep your car on (ready, or ignition on) and in P: the comma reads the radar only then.", Tone.TODO)
            CheckLine("The phone: hotspot on (or the same Wi-Fi as the comma). No internet needed.", Tone.INFO)
        }
    }
}

@Composable
private fun CaptureStep(model: AppModel, draft: TestDraft, onBack: () -> Unit) {
    val radar = model.radar
    val scope = rememberCoroutineScope()
    var capturing by remember { mutableStateOf(false) }
    var progress by remember { mutableDoubleStateOf(0.0) }
    val now = rememberTicker()
    val result = draft.capture
    val live = radar.phase is RadarPhase.Live
    val latest = radar.latest
    val reason = when {
        capturing -> ""
        !live -> "Waiting for the comma (connection above)."
        radar.onroad != true -> "Switch the car on first."
        latest == null || now - radar.latestAtMs >= 1_500 -> "Waiting for the car's data."
        latest.reading == null -> "The radar sees nothing yet: check the target is straight ahead."
        latest.vEgo == null || abs(latest.vEgo) >= CaptureRules.STILL_MPS -> "The car is moving: stand still, in P."
        else -> ""
    }
    StepScreen(when {
        result?.ok == true -> "Captured"
        result != null -> "Not steady enough"
        else -> "Hold still for 20 seconds"
    }, if (result == null) "Car on, in P, foot off the pedals, nobody in front of it. The phone records the radar from the comma."
    else "", 3, STEPS, onBack, bottom = {
        when {
            result?.ok == true -> {
                PrimaryButton("Continue") { draft.step = 4 }
                LinkButton("Capture again") { draft.capture = null }
            }
            result != null -> PrimaryButton("Try again") { draft.capture = null }
            else -> PrimaryButton(if (capturing) "Recording…" else "Start the 20 s capture", reason, busy = capturing) {
                capturing = true
                progress = 0.0
                scope.launch {
                    try {
                        val captured = radar.capture { progress = it }
                        draft.comma = CommaRef(radar.route, radar.hello?.gitCommit)
                        draft.capture = captured
                    } finally {
                        capturing = false
                    }
                }
            }
        }
    }) {
        if (result == null) {
            ConnectionSummary(model, now)
            if (capturing) {
                LinearProgressIndicator(progress = { progress.toFloat() }, modifier = Modifier.fillMaxWidth().height(8.dp))
                Note("${(progress * CaptureRules.DURATION_S).roundToInt()} of ${CaptureRules.DURATION_S.roundToInt()} s · keep still")
            }
            (radar.phase as? RadarPhase.Choose)?.let { ChooseComma(it.candidates, onPick = radar::choose) }
            (radar.phase as? RadarPhase.Stopped)?.let {
                Callout("${it.title}. ${it.detail}", Tone.BAD)
                SecondaryButton("Try again", onClick = radar::retry)
            }
        } else {
            val summary = result.summary
            SectionCard {
                if (summary.readingM != null) {
                    Text(RadarMath.metres(summary.readingM), fontSize = 52.sp, fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
                    Note("the radar's steady reading")
                }
                CheckLine("Steady for ${String.format(java.util.Locale.ROOT, "%.1f", summary.steadyS)} s (10 s needed)",
                    if (summary.steadyS >= CaptureRules.MIN_STEADY_S) Tone.OK else Tone.BAD)
                CheckLine("${summary.withReading} of ${summary.samples} readings with a target" +
                    (summary.steadyShare?.let { ", ${(it * 100).roundToInt()}% at this value" } ?: ""), Tone.MUTED)
                CheckLine("Top speed ${String.format(java.util.Locale.ROOT, "%.2f", summary.speedMaxMps ?: 0.0)} m/s",
                    if ((summary.speedMaxMps ?: 0.0) < CaptureRules.STILL_MPS) Tone.OK else Tone.TODO)
                if (summary.histogram.size > 1) {
                    CheckLine("Readings: " + summary.histogram.entries.joinToString { "${it.key} m ×${it.value}" }, Tone.MUTED)
                }
            }
            result.problems.forEach { Callout(it, Tone.BAD) }
        }
    }
}

/** The live state in short, for the capture step. */
@Composable
private fun ConnectionSummary(model: AppModel, now: Long) {
    val radar = model.radar
    val latest = radar.latest
    SectionCard {
        val fresh = latest != null && now - radar.latestAtMs < 1_500
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            when {
                radar.phase !is RadarPhase.Live -> {
                    CircularProgressIndicator(Modifier.size(32.dp))
                    Text(when (val phase = radar.phase) {
                        is RadarPhase.Connecting -> "Connecting to ${phase.host}…"
                        is RadarPhase.Waiting -> phase.reason
                        else -> "Looking for the comma…"
                    }, color = Site.Muted, textAlign = TextAlign.Center)
                }
                radar.onroad != true -> Text("Switch the car on", fontSize = 28.sp, fontWeight = FontWeight.Bold, color = Site.Todo)
                latest == null || !fresh -> Text("Waiting for the car…", fontSize = 24.sp, color = Site.Muted)
                latest.reading == null -> Text("No reading", fontSize = 28.sp, fontWeight = FontWeight.Bold, color = Site.Muted)
                else -> {
                    Text(RadarMath.metres(latest.reading ?: 0.0), fontSize = 44.sp, fontWeight = FontWeight.Bold)
                    Note("radar reading" + (radar.steadyValue?.let { " · steady ${shortSpan(radar.steadySeconds)}" } ?: ""))
                }
            }
            latest?.vEgo?.takeIf { fresh }?.let {
                Note(String.format(java.util.Locale.ROOT, "speed %.2f m/s", it), if (abs(it) < CaptureRules.STILL_MPS) Site.Muted else Site.Todo)
            }
        }
    }
}

@Composable
private fun TapeRadarStep(model: AppModel, draft: TestDraft, onBack: () -> Unit) {
    val car = draft.target == Target.CAR
    val check = RadarMath.checkTapeRadar(draft.tapeRadar, draft.target, model.info?.labelRangeM ?: 5.0..30.0)
    StepScreen(if (car) "How far is the other car from the radar?" else "How far is the wall from the radar?",
        if (car) "From the back of the other car to the front of your bumper, in front of the radar. Keep the tape straight and tight."
        else "Tape from the wall to the front of your bumper, right in front of the radar. Use the same spot every time.",
        4, STEPS, onBack, bottom = { PrimaryButton("Continue", if (check.ok) "" else check.bad) { draft.step = 5 } }) {
        MetreStepper(draft.tapeRadar, { draft.tapeRadar = it }, "From the ${if (car) "other car" else "wall"} to the bumper, in metres",
            start = if (car) 10.0 else 1.5)
        if (check.warning.isNotEmpty()) Callout(check.warning, Tone.TODO)
    }
}

@Composable
private fun TapeCommaStep(model: AppModel, draft: TestDraft, onBack: () -> Unit) {
    val car = draft.target == Target.CAR
    val radarTape = draft.tapeRadar ?: 0.0
    val saved = model.info?.calibration?.radarToCameraM
    val check = RadarMath.checkTapeComma(draft.tapeComma, radarTape, draft.target, saved)
    StepScreen("And how far is the comma?",
        if (car) "From the back of the other car to the comma, along the side of your car."
        else "From the wall to the comma behind the windshield, along the side of the car.",
        5, STEPS, onBack, bottom = { PrimaryButton("Continue", if (check.ok) "" else check.bad) { draft.step = 6 } }) {
        MetreStepper(draft.tapeComma, { draft.tapeComma = it }, "From the ${if (car) "other car" else "wall"} to the comma, in metres",
            start = RadarMath.round2(radarTape + (saved ?: 1.5)))
        if (check.info.isNotEmpty()) Note(check.info)
        if (check.warning.isNotEmpty()) Callout(check.warning, Tone.TODO)
    }
}

@Composable
private fun ResultStep(model: AppModel, draft: TestDraft, onBack: () -> Unit, onSave: (TestUpload) -> Unit, onThrowAway: () -> Unit) {
    val capture = draft.capture
    val reading = capture?.summary?.readingM
    val tapeRadar = draft.tapeRadar
    val tapeComma = draft.tapeComma
    if (capture == null || reading == null || tapeRadar == null || tapeComma == null) {
        androidx.compose.runtime.LaunchedEffect(Unit) { draft.step = if (capture == null) 3 else 4 }
        return
    }
    val point = TapePoint(reading, tapeRadar, tapeComma)
    val correction = point.correctionM
    val others = model.calibrationPoints()
    val combined = RadarMath.combine(others + point)!!
    val fromComma = RadarMath.round2(reading + combined.correctionM + combined.radarToCameraM)
    val apart = abs(RadarMath.round2(fromComma - tapeComma))
    val close = apart <= RadarMath.READING_STEP_M / 2 + 1e-9
    val othersMean = if (others.isEmpty()) null else others.map { it.correctionM }.average()
    val away = othersMean?.let { abs(correction - it) } ?: 0.0
    val outlier = away > RadarMath.DISAGREE_M
    val tooFar = !RadarMath.correctionInRange(correction)
    var verdict = RadarMath.verdict(correction)
    if (draft.target == Target.CAR) verdict += " at ${RadarMath.metres(tapeRadar)}"
    StepScreen("The radar read ${RadarMath.metres(reading)}", "Your tape said ${RadarMath.metres(tapeRadar)}.", 6, STEPS, onBack,
        bottom = {
            PrimaryButton(if (outlier) "Save anyway" else "Save the test",
                if (tooFar) "The tape and the reading are more than 1 m apart: this test cannot be saved." else "") {
                onSave(TestUpload(draft.clientId, System.currentTimeMillis() / 1000.0, draft.target, tapeRadar, tapeComma,
                    capture.summary, draft.comma, draft.note.trim().take(500)))
            }
            LinkButton("Throw it away", Site.Muted, onThrowAway)
        }) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text(verdict, fontSize = 30.sp, lineHeight = 36.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                color = if (tooFar) Site.Bad else Site.Text)
        }
        SectionCard(title = "With this test") {
            CheckLine((if (combined.count > 1) "With all your tests: " else "With this test: ") +
                "correction ${RadarMath.signedMetres(combined.correctionM)} ± ${RadarMath.format2(combined.uncertaintyM)} m, " +
                "radar to comma ${RadarMath.metres(combined.radarToCameraM)} (${combined.count} test${if (combined.count == 1) "" else "s"})")
            CheckLine("From the comma: ${RadarMath.metres(reading)} becomes ${RadarMath.metres(fromComma)}. Tape: " +
                "${RadarMath.metres(tapeComma)}. ${(apart * 100).roundToInt()} cm apart" + if (close) " ✓" else "",
                if (close) Tone.OK else Tone.TODO)
            CheckLine("The radar counts in steps of 0.25 m, so one test is good to about ±0.13 m.", Tone.MUTED)
            if (tapeRadar >= RadarMath.FAR_TEST_M && others.none { it.tapeRadarM >= RadarMath.FAR_TEST_M } &&
                combined.spreadM <= RadarMath.DISAGREE_M) {
                CheckLine("With this test, confirmed pairs can become labels.")
            }
            model.info?.calibration?.values?.let {
                CheckLine("The site uses now: correction ${RadarMath.signedMetres(it.correctionM)}, radar to comma " +
                    RadarMath.metres(it.radarToCameraM) + ".", Tone.MUTED)
            }
            if (model.info == null) CheckLine("Not synced with the site yet: the means use the tests on this phone only.", Tone.MUTED)
        }
        when {
            tooFar -> Callout("The tape and the radar reading are more than 1 m apart: check the tape, or capture again.", Tone.BAD) {
                SecondaryButton("Capture again") { draft.capture = null; draft.step = 3 }
            }
            outlier -> Callout("This test is ${String.format(java.util.Locale.ROOT, "%.1f", away)} m away from your other tests. " +
                "Measure again before saving?", Tone.TODO) {
                SecondaryButton("Measure again") { draft.step = 4 }
            }
        }
        OutlinedTextField(value = draft.note, onValueChange = { draft.note = it.take(500) }, label = { Text("Note (optional)") },
            shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth())
        Note("Saved on this phone first; it goes to the site the next time the app is open where the site answers " +
            "(home Wi-Fi or ZeroTier).")
    }
}
