package ch.bodhi.perception

import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sqrt

/** The two values of the car's radar calibration (web/settings.py): distance from the comma =
 * reading + correction + radar_to_camera. */
data class CalibrationValues(val radarToCameraM: Double, val correctionM: Double)

/** One test as the means need it: the steady reading and the two tapes. */
data class TapePoint(val readingM: Double, val tapeRadarM: Double, val tapeCommaM: Double) {
    val correctionM: Double get() = RadarMath.round2(tapeRadarM - readingM)
    val radarToCameraM: Double get() = RadarMath.round2(tapeCommaM - tapeRadarM)
}

/** What a set of tests gives together, as web/radar.py RadarTests.summary. */
data class Combined(val count: Int, val correctionM: Double, val radarToCameraM: Double, val spreadM: Double,
                    val uncertaintyM: Double, val testedToM: Double)

/** A check of a typed tape distance: [bad] stops the step, [warning] only says it. */
data class TapeCheck(val bad: String = "", val warning: String = "", val info: String = "") {
    val ok: Boolean get() = bad.isEmpty()
}

/** The rules and numbers of the site's radar calibration (web/radar.py, web/settings.py, the test wizard of
 * web/static/app.js), the same here so a test made offline gives what the site will compute. */
object RadarMath {
    /** Readings come in 0.25 m steps: half a step is the uncertainty of one reading (about ±0.13 m). */
    const val READING_STEP_M = 0.25
    const val TAPE_MIN_M = 0.3
    const val TAPE_MAX_M = 100.0
    /** The comma sits behind the radar, between the bumper and the windshield. */
    const val RADAR_TO_CAMERA_MAX_M = 5.0
    /** The radar's zero against a tape: a few centimetres to a few tens; beyond, a slipped tape. */
    const val CORRECTION_MIN_M = -1.0
    const val CORRECTION_MAX_M = 1.0
    /** Confirmed pairs become labels from here: a test this far checks the calibration where it matters. */
    const val FAR_TEST_M = 5.0
    /** Kept corrections farther apart than this disagree beyond what the 25 cm steps explain. */
    const val DISAGREE_M = 0.5

    fun round2(value: Double): Double = Math.round(value * 100.0) / 100.0

    /** A radar reading as a distance from the comma; null without a calibration (show the raw reading). */
    fun fromComma(readingM: Double, calibration: CalibrationValues?): Double? =
        calibration?.let { readingM + it.correctionM + it.radarToCameraM }

    /** The tests together: means of the corrections and of the radar-to-comma distances, their spread, the
     * uncertainty (half a step, less with more tests: they are not fully independent, kept honest). */
    fun combine(points: List<TapePoint>): Combined? {
        if (points.isEmpty()) return null
        val corrections = points.map { it.tapeRadarM - it.readingM }
        val offsets = points.map { it.tapeCommaM - it.tapeRadarM }
        return Combined(
            count = points.size,
            correctionM = round2(corrections.average()),
            radarToCameraM = round2(offsets.average()),
            spreadM = round2(corrections.max() - corrections.min()),
            uncertaintyM = round2(READING_STEP_M / 2 / sqrt(max(1, points.size).toDouble())),
            testedToM = points.maxOf { it.tapeRadarM })
    }

    /** "Reads 0.93 m too long" (the reading is longer than the tape), "Reads just right". */
    fun verdict(correctionM: Double): String = when {
        abs(correctionM) < 0.005 -> "Reads just right"
        correctionM < 0 -> "Reads ${metres(abs(correctionM))} too long"
        else -> "Reads ${metres(correctionM)} too short"
    }

    fun correctionInRange(correctionM: Double): Boolean = correctionM in CORRECTION_MIN_M..CORRECTION_MAX_M

    /** The calibration status the site would give (web/radar.py calibration_view) with these tests. */
    fun statusOf(combined: Combined?): String = when {
        combined == null -> "not_set"
        combined.testedToM >= FAR_TEST_M && combined.spreadM <= DISAGREE_M -> "ready"
        else -> "close_only"
    }

    /** Step "how far is the wall / other car from the radar": the checks and warnings of the site. */
    fun checkTapeRadar(value: Double?, target: Target, labelRange: ClosedFloatingPointRange<Double> = 5.0..30.0): TapeCheck {
        if (value == null) return TapeCheck(bad = "Write the distance first.")
        if (value < TAPE_MIN_M || value > TAPE_MAX_M) return TapeCheck(bad = "Between 0.30 and 100 m.")
        return when {
            target == Target.WALL && value < 0.8 ->
                TapeCheck(warning = "Very close: the radar may see nothing. Back up to 1.5–2 m and measure again.")
            target == Target.WALL && value > 5 ->
                TapeCheck(warning = "Walls farther than a few metres are often not seen. Try the parked-car test.")
            target == Target.CAR && (value < 3 || value > 40) ->
                TapeCheck(warning = "For this test, ${plain(labelRange.start)} to ${plain(labelRange.endInclusive)} m works best.")
            else -> TapeCheck()
        }
    }

    /** Step "and how far is the comma": behind the radar, at most 5 m, and close to the saved distance. */
    fun checkTapeComma(value: Double?, tapeRadarM: Double, target: Target, savedRadarToCameraM: Double?): TapeCheck {
        if (value == null) return TapeCheck(bad = "Write the distance first.")
        if (value < TAPE_MIN_M || value > TAPE_MAX_M) return TapeCheck(bad = "Between 0.30 and 100 m.")
        val apart = round2(value - tapeRadarM)
        val thing = if (target == Target.CAR) "other car" else "wall"
        if (apart <= 0) return TapeCheck(bad = "The comma must be farther from the $thing than the radar.")
        if (apart > RADAR_TO_CAMERA_MAX_M) return TapeCheck(bad = "More than 5 m from the radar: check the tape.")
        val info = "So the comma sits ${format2(apart)} m behind the radar."
        if (savedRadarToCameraM != null) {
            val off = (abs(apart - savedRadarToCameraM) * 100).roundToInt()
            if (off >= 3) {
                return TapeCheck(info = info, warning = "Last time it was ${format2(savedRadarToCameraM)} m: this is " +
                    "$off cm different. Check that the tape is straight and level.")
            }
        }
        return TapeCheck(info = info)
    }

    /** A typed distance in metres: "1.32", "1,32", " 1.3 m"; null when it is not one. Rounded to the cm. */
    fun parseMetres(text: String): Double? {
        val cleaned = text.trim().removeSuffix("m").trim().replace(',', '.')
        if (cleaned.isEmpty() || cleaned.count { it == '.' } > 1 || !cleaned.all { it.isDigit() || it == '.' }) return null
        val value = cleaned.toDoubleOrNull() ?: return null
        return if (value.isFinite()) round2(value) else null
    }

    fun format2(value: Double): String = String.format(Locale.ROOT, "%.2f", value)

    fun metres(value: Double): String = "${format2(value)} m"

    /** A signed correction: "−0.89 m", "+0.10 m". */
    fun signedMetres(value: Double): String =
        (if (value < -0.004) "−" else if (value > 0.004) "+" else "") + metres(abs(value))

    private fun plain(value: Double): String =
        if (value == Math.floor(value)) value.toInt().toString() else format2(value)
}

/** What the radar looks at during a test (web/radar.py TARGETS). */
enum class Target(val wire: String, val title: String) {
    WALL("wall", "Wall"), CAR("car", "Parked car");

    companion object {
        fun of(wire: String?): Target = entries.firstOrNull { it.wire == wire } ?: WALL
    }
}
