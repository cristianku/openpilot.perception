package ch.bodhi.perception

import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.roundToInt

/** One radar line as the capture keeps it: the comma's time (logMonoTime of carState) and the phone's. */
data class RadarSample(val monoNs: Long?, val phoneMs: Long, val vEgo: Double?, val readingM: Double?) {
    /** Standing still as the site counts it (perception/radar/series.py STANDSTILL_SPEED_MPS). */
    val still: Boolean get() = vEgo != null && abs(vEgo) < CaptureRules.STILL_MPS

    companion object {
        fun of(line: ListenerLine.Radar, phoneMs: Long) =
            RadarSample(line.monoNs, phoneMs, line.vEgo, line.reading?.let { RadarMath.round2(it) })
    }
}

/** What a capture sends to the site (POST /api/app/v1/tests "capture"), and how long it held steady. */
data class CaptureSummary(
    val startedUnix: Double,
    val durationS: Double,
    val samples: Int,
    val withReading: Int,
    val readingM: Double?,
    val steadyShare: Double?,
    val histogram: Map<String, Int>,
    val speedMaxMps: Double?,
    val onroad: Boolean,
    val steadyS: Double
)

data class CaptureResult(val summary: CaptureSummary, val problems: List<String>) {
    val ok: Boolean get() = problems.isEmpty()
}

/** The rules of a capture: 20 s of the live stream with the car on and standing still; good when at least
 * 10 s of it held one reading with the speed under 0.05 m/s, and that reading was the radar's most of the time
 * (the standstill rules of perception/radar/series.py: a target at least half of the time, one reading at least
 * 80% of it). */
object CaptureRules {
    const val DURATION_S = 20.0
    const val MIN_STEADY_S = 10.0
    const val STILL_MPS = 0.05
    const val MIN_TARGET_SHARE = 0.5
    const val MIN_MODE_SHARE = 0.8
    /** One sample stands for the time to the next one, at most this (a gap in the stream is not steady time). */
    const val MAX_SAMPLE_S = 0.25
    /** The listener sends 10 lines a second. */
    const val PERIOD_S = 0.1

    fun histogramKey(readingM: Double): String = String.format(Locale.ROOT, "%.2f", readingM)

    /** How long each sample stands for: the time to the next one by the comma's clock (the phone's when the
     * comma's is missing), capped; the last one a period. */
    fun weights(samples: List<RadarSample>): List<Double> = samples.indices.map { index ->
        if (index == samples.lastIndex) {
            PERIOD_S
        } else {
            val a = samples[index]
            val b = samples[index + 1]
            val seconds = if (a.monoNs != null && b.monoNs != null) (b.monoNs - a.monoNs) / 1e9
            else (b.phoneMs - a.phoneMs) / 1000.0
            seconds.coerceIn(0.0, MAX_SAMPLE_S)
        }
    }

    fun evaluate(samples: List<RadarSample>, startedUnix: Double, onroad: Boolean, durationS: Double = DURATION_S,
                 connectionLost: Boolean = false): CaptureResult {
        val readings = samples.mapNotNull { it.readingM }
        val histogram = readings.groupingBy { histogramKey(it) }.eachCount().toSortedMap()
        // The reading the radar kept most while standing still; ties go to the nearer one.
        val stillReadings = samples.filter { it.still }.mapNotNull { it.readingM }
        val mode = stillReadings.groupingBy { it }.eachCount().entries
            .sortedWith(compareByDescending<Map.Entry<Double, Int>> { it.value }.thenBy { it.key }).firstOrNull()?.key
        val weights = weights(samples)
        var steadyS = 0.0
        samples.forEachIndexed { index, sample ->
            if (mode != null && sample.still && sample.readingM == mode) steadyS += weights[index]
        }
        val steadyShare = if (mode == null || readings.isEmpty()) null
        else RadarMath.round2(readings.count { it == mode }.toDouble() / readings.size)
        val speeds = samples.mapNotNull { it.vEgo?.let(::abs) }
        val summary = CaptureSummary(
            startedUnix = startedUnix,
            durationS = durationS,
            samples = samples.size,
            withReading = readings.size,
            readingM = mode,
            steadyShare = steadyShare,
            histogram = histogram,
            speedMaxMps = speeds.maxOrNull()?.let { Math.round(it * 1000.0) / 1000.0 },
            onroad = onroad,
            steadyS = Math.round(steadyS * 10.0) / 10.0)

        val problems = mutableListOf<String>()
        if (connectionLost) problems += "The connection to the comma dropped during the capture: try again."
        if (samples.isEmpty()) {
            problems += if (onroad) "No radar data arrived from the comma: try again." else "The car is off: switch it on and try again."
            return CaptureResult(summary, problems)
        }
        if (steadyS + 1e-9 >= MIN_STEADY_S && (steadyShare ?: 0.0) >= MIN_MODE_SHARE &&
            readings.size.toDouble() / samples.size >= MIN_TARGET_SHARE) {
            return CaptureResult(summary, problems)
        }
        val moving = samples.count { it.vEgo != null && !it.still }
        if (samples.all { it.vEgo == null }) {
            problems += "The comma sent no speed of the car, so it cannot tell the car stood still: try again."
        } else if (moving > 0) {
            problems += "The car moved (up to ${String.format(Locale.ROOT, "%.2f", summary.speedMaxMps ?: 0.0)} m/s): " +
                "keep it standing still, in P, foot off the pedals."
        }
        val targetShare = readings.size.toDouble() / samples.size
        if (targetShare < MIN_TARGET_SHARE) {
            problems += "The radar saw something only ${(targetShare * 100).roundToInt()}% of the time: nothing and " +
                "nobody between the car and the target, and the right distance."
        } else if (steadyShare != null && steadyShare < MIN_MODE_SHARE) {
            problems += "The reading kept changing (${RadarMath.metres(mode!!)} only ${(steadyShare * 100).roundToInt()}% " +
                "of the time): is something moving in front of the car?"
        }
        if (steadyS < MIN_STEADY_S) {
            problems += "Only ${String.format(Locale.ROOT, "%.1f", floor((steadyS + 1e-9) * 10) / 10)} s of steady reading: " +
                "${MIN_STEADY_S.roundToInt()} s are needed."
        }
        return CaptureResult(summary, problems)
    }
}

/** The live "steady 12 s at 2.25 m": how long the reading has stayed the same. A short loss of the target
 * (under [maxGapMs]) does not end it; another reading does. */
class SteadyTracker(private val maxGapMs: Long = 1_000) {
    var value: Double? = null
        private set
    private var sinceMs = 0L
    private var lastSeenMs = 0L

    fun add(readingM: Double?, nowMs: Long) {
        if (readingM == null) {
            if (value != null && nowMs - lastSeenMs > maxGapMs) value = null
            return
        }
        if (readingM != value || nowMs - lastSeenMs > maxGapMs) {
            value = readingM
            sinceMs = nowMs
        }
        lastSeenMs = nowMs
    }

    /** Seconds the current reading has held, 0 without one. */
    fun seconds(nowMs: Long): Double =
        if (value == null) 0.0 else (minOf(nowMs, lastSeenMs) - sinceMs).coerceAtLeast(0) / 1000.0

    fun reset() {
        value = null
    }
}
