package ch.bodhi.perception

import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException

/** What the radar screen shows of the connection. */
sealed class RadarPhase {
    data object Idle : RadarPhase()
    data object Searching : RadarPhase()
    /** No trusted key yet: the owner picks the comma among the SSH servers found, by its fingerprint. */
    data class Choose(val candidates: List<SshHost>) : RadarPhase()
    data class Connecting(val host: String) : RadarPhase()
    data class Live(val host: String) : RadarPhase()
    data class Waiting(val reason: String, val retryAtMs: Long) : RadarPhase()
    data class Stopped(val kind: StopKind, val title: String, val detail: String) : RadarPhase()
}

enum class StopKind { NO_KEY, NOT_FOUND, KEY_CHANGED, LOGIN_REFUSED, LISTENER_ERROR, GAVE_UP, PAUSED }

/** A point of the 30 s chart: phone time and the raw reading (null: no target). */
data class HistoryPoint(val atMs: Long, val readingM: Double?)

/** When to try the listener again after a failure: never a fast loop, since every start takes a carState reader
 * slot on the comma until the next ignition (app/comma_listener.py). */
object ListenerBackoff {
    val SECONDS = listOf(5, 15, 30, 60, 120)
    const val MAX_FAILURES = 6
    /** A stream that ran this long was healthy: its end starts the count again. */
    const val HEALTHY_MS = 60_000L

    fun delaySeconds(failures: Int): Int = SECONDS[(failures - 1).coerceIn(0, SECONDS.lastIndex)]
}

/** How many listeners the app starts on the comma during one drive (one ignition). Each one that reads carState
 * keeps a reader slot of carState until the next ignition; msgq has 25 and openpilot's own processes take about
 * 14 onroad; with every slot taken msgq makes all readers reconnect, openpilot's too. */
class StartBudget(private val max: Int = MAX_PER_DRIVE) {
    private var drive: String? = null
    private var used = 0

    /** A listener of this session read carState during the drive [route]. */
    fun count(route: String?) {
        val key = route ?: "?"
        if (key != drive) {
            drive = key
            used = 0
        }
        used++
    }

    val usedThisDrive: Int get() = used

    /** Whether one more start could take the last slots of the drive the app counted for. */
    val exhausted: Boolean get() = used >= max

    /** The drive of now (the newest recording on the comma, null offroad): a new drive or the car off frees the
     * slots. */
    fun allows(onroad: Boolean, newestRoute: String?): Boolean {
        if (!exhausted) return true
        if (!onroad || newestRoute == null || newestRoute != drive) {
            drive = null
            used = 0
            return true
        }
        return false
    }

    companion object {
        const val MAX_PER_DRIVE = 6

        /** The read-only command that tells the drive of now: the IsOffroad param, then the newest route by its
         * counter (fixed-width hex: the order of the names is the order of the counter). */
        const val DRIVE_COMMAND = "cat /data/params/d/IsOffroad 2>/dev/null; echo; " +
            "ls -1 /data/media/0/realdata 2>/dev/null | grep -E '^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]+$' | cut -c1-20 | sort -u | tail -n 1"

        private val ROUTE = Regex("^[0-9a-f]{8}--[0-9a-f]{10}$")

        /** The answer of [DRIVE_COMMAND]: (onroad, newest route). */
        fun parseDrive(text: String): Pair<Boolean, String?> {
            val lines = text.lines().map { it.trim() }
            val onroad = lines.firstOrNull() == "0"
            val route = lines.drop(1).lastOrNull { ROUTE.matches(it) }
            return onroad to route
        }
    }
}

/** The comma's radar, live: one listener on the comma while a radar screen is open (the Radar tab, the whole radar
 * test), shared by them; closed [GRACE_MS] after the last one goes away. A few starts per drive at most
 * ([StartBudget]): then it pauses and says why. */
class RadarSession(private val model: AppModel) {
    var phase by mutableStateOf<RadarPhase>(RadarPhase.Idle)
        private set
    var hello by mutableStateOf<ListenerLine.Hello?>(null)
        private set
    /** From the listener's status lines and its radar lines; null before the first. */
    var onroad by mutableStateOf<Boolean?>(null)
        private set
    var route by mutableStateOf<String?>(null)
        private set
    var latest by mutableStateOf<ListenerLine.Radar?>(null)
        private set
    var latestAtMs by mutableStateOf(0L)
        private set
    var steadyValue by mutableStateOf<Double?>(null)
        private set
    var steadySeconds by mutableStateOf(0.0)
        private set
    val history = mutableStateListOf<HistoryPoint>()
    /** Listener starts since the app started (each takes a carState reader slot on the comma). */
    var starts by mutableIntStateOf(0)
        private set
    /** Listener starts that read carState during the drive of now. */
    var startsThisDrive by mutableIntStateOf(0)
        private set
    /** +1 for every listener session: a capture that sees it change lost its stream. */
    var epoch by mutableIntStateOf(0)
        private set

    val samples = MutableSharedFlow<RadarSample>(extraBufferCapacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    private val steady = SteadyTracker()
    private val budget = StartBudget()
    private val owners = mutableSetOf<String>()
    private var job: Job? = null
    private var stopping: Job? = null

    /** A screen that shows the radar opened. */
    fun acquire(owner: String) {
        owners += owner
        stopping?.cancel()
        stopping = null
        if (job?.isActive != true && phase !is RadarPhase.Stopped && phase !is RadarPhase.Choose) start()
    }

    /** The screen went away: the listener is stopped (its channel closed) unless a radar screen takes it again
     * within [GRACE_MS]: a tab switch or the screen turning off must not cost a new start (and a reader slot). */
    fun release(owner: String) {
        owners -= owner
        if (owners.isNotEmpty()) return
        stopping?.cancel()
        stopping = model.scope.launch {
            delay(GRACE_MS)
            if (owners.isEmpty()) stopNow()
        }
    }

    fun retry() {
        job?.cancel()
        phase = RadarPhase.Idle
        if (owners.isNotEmpty()) start()
    }

    fun choose(host: SshHost) {
        model.acceptComma(host)
        retry()
    }

    fun stopNow() {
        job?.cancel()
        job = null
        phase = RadarPhase.Idle
        clearLive()
    }

    private fun clearLive() {
        latest = null
        onroad = null
        steady.reset()
        steadyValue = null
        steadySeconds = 0.0
        history.clear()
    }

    private fun start() {
        job = model.scope.launch {
            try {
                loop()
            } catch (_: CancellationException) {
            }
        }
    }

    private sealed class Outcome {
        data object Bye : Outcome()
        data object Paused : Outcome()
        data class Lost(val reason: String, val streamedMs: Long) : Outcome()
        data class Failed(val reason: String, val fatal: Boolean) : Outcome()
    }

    private suspend fun loop() {
        var failures = 0
        while (currentCoroutineContext().isActive) {
            var reason = ""
            phase = RadarPhase.Searching
            try {
                if (model.phoneKey.publicLine() == null) {
                    phase = RadarPhase.Stopped(StopKind.NO_KEY, "No phone key yet",
                        "Connect this phone to your comma first: Settings › Phone key, or the setup.")
                    return
                }
                val found = model.findComma()
                val comma = when (found) {
                    is FindResult.Found -> found.comma
                    is FindResult.Choose -> {
                        phase = RadarPhase.Choose(found.candidates)
                        return
                    }
                    is FindResult.NoNetwork -> null
                    is FindResult.NotFound -> {
                        if (found.changed != null) {
                            val check = model.notFound(found)
                            phase = RadarPhase.Stopped(StopKind.KEY_CHANGED, check.title, check.detail)
                            return
                        }
                        null
                    }
                }
                if (comma == null) {
                    reason = if (found is FindResult.NoNetwork) "This phone is on no Wi-Fi and its hotspot is off."
                    else "No comma found on this phone's networks."
                    failures++
                } else {
                    model.rememberComma(comma.host)
                    phase = RadarPhase.Connecting(comma.host)
                    when (val outcome = stream(comma)) {
                        Outcome.Bye -> {
                            failures = 0
                            continue
                        }
                        Outcome.Paused -> {
                            phase = RadarPhase.Stopped(StopKind.PAUSED, "Paused to protect openpilot",
                                "The comma has room for only a few readers of the car's data per drive, openpilot's own " +
                                    "first; this app started ${budget.usedThisDrive} during this drive. Switch the car off " +
                                    "and on again, then tap Try again.")
                            return
                        }
                        is Outcome.Lost -> {
                            if (outcome.streamedMs >= ListenerBackoff.HEALTHY_MS) failures = 0
                            failures++
                            reason = outcome.reason
                        }
                        is Outcome.Failed -> {
                            if (outcome.fatal) {
                                phase = RadarPhase.Stopped(StopKind.LISTENER_ERROR, "The listener failed on the comma", outcome.reason)
                                return
                            }
                            failures++
                            reason = outcome.reason
                        }
                    }
                }
            } catch (_: SshProblem.LoginRefused) {
                phase = RadarPhase.Stopped(StopKind.LOGIN_REFUSED, "The comma does not accept this phone yet",
                    "Add this phone's key to the comma: Settings › Phone key (one tap from home, or GitHub).")
                return
            } catch (problem: SshProblem.HostKeyRefused) {
                phase = RadarPhase.Stopped(StopKind.KEY_CHANGED, "The comma's key changed?", (problem.message ?: "") +
                    " If the comma was reset or reinstalled, forget it in Settings and connect again.")
                return
            } catch (problem: CancellationException) {
                throw problem
            } catch (problem: Exception) {
                failures++
                reason = problem.message ?: "The connection to the comma failed."
            }
            clearLive()
            if (failures >= ListenerBackoff.MAX_FAILURES) {
                phase = RadarPhase.Stopped(StopKind.GAVE_UP, "Stopped trying", "$reason Tap Try again when the comma is on and reachable.")
                return
            }
            val seconds = ListenerBackoff.delaySeconds(failures)
            phase = RadarPhase.Waiting(reason, SystemClock.elapsedRealtime() + seconds * 1000L)
            delay(seconds * 1000L)
        }
    }

    /** One listener session: SSH, the script, its lines until the end, the channel closed whatever happens (also
     * when the screen goes away while the connection is still being opened). */
    private suspend fun stream(comma: SshHost): Outcome = coroutineScope {
        val paths = model.info?.comma ?: SiteComma.DEFAULT
        val command = ListenerProtocol.command(paths.openpilot, paths.python)
        val script = withContext(Dispatchers.IO) { model.listenerScript() }
        val trusted = model.trustedFingerprints()
        // Set inside the blocking block, so a cancel that comes while it runs still finds what to close.
        var opened: CommaShell? = null
        var channel: ListenerChannel? = null
        try {
            withContext(Dispatchers.IO) {
                val seed = model.phoneKey.seed() ?: throw SshProblem.Failed("The phone key cannot be read.")
                try {
                    opened = CommaShell.open(comma.host, comma.subnet, trusted, seed)
                } finally {
                    seed.fill(0)
                }
            }
            val shell = opened ?: throw SshProblem.Failed("The SSH connection did not open.")
            if (budget.exhausted) {
                // Before one more start in a drive that had many: is it still the same drive?
                val (onroadNow, newest) = StartBudget.parseDrive(withContext(Dispatchers.IO) { shell.run(StartBudget.DRIVE_COMMAND) })
                if (!budget.allows(onroadNow, newest)) return@coroutineScope Outcome.Paused
                startsThisDrive = budget.usedThisDrive
            }
            withContext(Dispatchers.IO) { channel = shell.startListener(command, script) }
            val open = channel ?: throw SshProblem.Failed("The listener did not start.")
            starts++
            epoch++
            hello = null
            var counted = false
            fun readsCarState() {
                if (counted) return
                counted = true
                budget.count(route)
                startsThisDrive = budget.usedThisDrive
            }
            val lines = Channel<String>(256, BufferOverflow.DROP_OLDEST)
            val reader = launch(Dispatchers.IO) {
                try {
                    open.readLines { lines.trySend(it) }
                } catch (_: IOException) {
                } finally {
                    lines.close()
                }
            }
            var lastLineMs = SystemClock.elapsedRealtime()
            val startedMs = lastLineMs
            val watchdog = launch {
                // Offroad a status line comes every second, onroad ten radar lines: silence means a dead link.
                while (isActive) {
                    delay(1_000)
                    if (SystemClock.elapsedRealtime() - lastLineMs > 8_000) {
                        withContext(Dispatchers.IO) { open.close(); shell.close() }
                        break
                    }
                }
            }
            var outcome: Outcome? = null
            for (line in lines) {
                lastLineMs = SystemClock.elapsedRealtime()
                when (val event = ListenerProtocol.parse(line)) {
                    is ListenerLine.Hello -> {
                        hello = event
                        route = event.route
                        phase = RadarPhase.Live(comma.host)
                    }
                    is ListenerLine.Radar -> {
                        readsCarState()
                        onRadar(event, lastLineMs)
                    }
                    is ListenerLine.Status -> {
                        onroad = event.onroad
                        route = event.route ?: route
                        if (event.onroad) readsCarState()
                        if (!event.onroad) {
                            steady.reset()
                            steadyValue = null
                            steadySeconds = 0.0
                        }
                    }
                    is ListenerLine.Bye -> outcome = Outcome.Bye
                    is ListenerLine.Error -> outcome = Outcome.Failed(event.message,
                        fatal = event.message.startsWith("openpilot messaging"))
                    else -> Unit
                }
                if (outcome != null) break
            }
            watchdog.cancel()
            reader.cancel()
            outcome ?: if (hello == null) {
                Outcome.Failed(open.errorText().ifBlank { "The listener did not start on the comma." }, fatal = false)
            } else {
                Outcome.Lost("The connection to the comma ended.", SystemClock.elapsedRealtime() - startedMs)
            }
        } finally {
            withContext(kotlinx.coroutines.NonCancellable + Dispatchers.IO) {
                channel?.close()
                opened?.close()
            }
        }
    }

    private fun onRadar(event: ListenerLine.Radar, nowMs: Long) {
        latest = event
        latestAtMs = nowMs
        onroad = true
        val reading = event.reading?.let(RadarMath::round2)
        steady.add(reading, nowMs)
        steadyValue = steady.value
        steadySeconds = steady.seconds(nowMs)
        history.add(HistoryPoint(nowMs, reading))
        while (history.isNotEmpty() && nowMs - history.first().atMs > 30_000) history.removeAt(0)
        samples.tryEmit(RadarSample.of(event, nowMs))
    }

    companion object {
        /** How long the listener waits for a radar screen to come back before it is stopped. */
        const val GRACE_MS = 45_000L
    }

    /** Records [durationS] of the live stream (the car on, standing still) and judges it by [CaptureRules]. */
    suspend fun capture(durationS: Double = CaptureRules.DURATION_S, onProgress: (Double) -> Unit): CaptureResult {
        val startEpoch = epoch
        val startedUnix = System.currentTimeMillis() / 1000.0
        val startMs = SystemClock.elapsedRealtime()
        val collected = mutableListOf<RadarSample>()
        var sawOnroad = onroad == true
        withTimeoutOrNull((durationS * 1000).toLong()) {
            launch {
                while (isActive) {
                    onProgress(((SystemClock.elapsedRealtime() - startMs) / 1000.0 / durationS).coerceIn(0.0, 1.0))
                    if (onroad == true) sawOnroad = true
                    delay(200)
                }
            }
            samples.collect { collected += it }
        }
        onProgress(1.0)
        val lost = epoch != startEpoch || phase !is RadarPhase.Live
        return CaptureRules.evaluate(collected, startedUnix, sawOnroad || collected.isNotEmpty(), durationS, lost)
    }
}
