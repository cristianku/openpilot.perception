package ch.bodhi.perception

import org.json.JSONException
import org.json.JSONObject

/** One line of app/comma_listener.py (its docstring is the protocol): one JSON object per line. */
sealed class ListenerLine {
    /** First, once. git_commit and git_branch come from the comma's Params files; route and segment are the
     * newest recording by route counter, or null. */
    data class Hello(val version: Int, val gitCommit: String?, val gitBranch: String?, val route: String?,
                     val segment: String?, val python: String?, val pid: Int?, val radarFields: Boolean,
                     val maxSeconds: Int?) : ListenerLine()

    /** 10 Hz while carState arrives (the car is on). Numbers that were not finite on the comma are null. */
    data class Radar(val monoNs: Long?, val vEgo: Double?, val valid: Boolean, val distanceM: Double?,
                     val relSpeedMps: Double?, val relSpeedValid: Boolean, val measuredNs: Long?) : ListenerLine() {
        /** A distance the radar stands behind: valid and a finite number. */
        val reading: Double? get() = if (valid) distanceM else null
    }

    data class Status(val onroad: Boolean, val route: String?, val segment: String?) : ListenerLine()

    /** The listener ends by itself (after 20 minutes): start a new one if the screen is still open. */
    data class Bye(val reason: String) : ListenerLine()

    /** An unexpected error on the comma (openpilot could not be imported, …); the listener exits. */
    data class Error(val message: String) : ListenerLine()

    /** Valid JSON of a type this app does not know (a newer listener): ignored. */
    data class Unknown(val type: String) : ListenerLine()
}

object ListenerProtocol {
    /** The protocol version the app speaks (VERSION in app/comma_listener.py). */
    const val VERSION = 1

    /** The command the app runs over SSH; the script follows on its standard input, then end of input. -B: no
     * __pycache__ written on the comma. Paths come from the site (GET /api/app/v1 comma) and are checked. */
    fun command(openpilot: String, python: String): String {
        require(SAFE_PATH.matches(openpilot) && SAFE_PATH.matches(python)) { "unsafe path for the comma" }
        return "cd $openpilot && exec nice -n 19 $python -B -u -"
    }

    val SAFE_PATH = Regex("^/[A-Za-z0-9._/-]{1,200}$")
    private val ROUTE = Regex("^[0-9a-f]{8}--[0-9a-f]{10}$")
    private val SEGMENT = Regex("^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]{1,4}$")
    private val COMMIT = Regex("^[0-9a-f]{7,40}$")

    /** The line parsed, or null when it is not a JSON object with a type (noise is skipped, never fatal). */
    fun parse(line: String): ListenerLine? {
        val text = line.trim()
        if (text.isEmpty() || text[0] != '{') return null
        val json = try {
            JSONObject(text)
        } catch (_: JSONException) {
            return null
        }
        return when (val type = json.optString("type", "")) {
            "hello" -> ListenerLine.Hello(
                version = json.int("version") ?: 0,
                gitCommit = json.text("git_commit")?.takeIf { COMMIT.matches(it) },
                gitBranch = json.text("git_branch")?.take(120),
                route = json.text("route")?.takeIf { ROUTE.matches(it) },
                segment = json.text("segment")?.takeIf { SEGMENT.matches(it) },
                python = json.text("python")?.take(40),
                pid = json.int("pid"),
                radarFields = json.optBoolean("radar_fields", false),
                maxSeconds = json.int("max_seconds"))
            "radar" -> ListenerLine.Radar(
                monoNs = json.long("mono_ns"),
                vEgo = json.number("v_ego"),
                valid = json.optBoolean("valid", false) && json.number("distance_m") != null,
                distanceM = json.number("distance_m"),
                relSpeedMps = json.number("rel_speed_mps"),
                relSpeedValid = json.optBoolean("rel_speed_valid", false),
                measuredNs = json.long("measured_ns"))
            "status" -> ListenerLine.Status(
                onroad = json.optBoolean("onroad", false),
                route = json.text("route")?.takeIf { ROUTE.matches(it) },
                segment = json.text("segment")?.takeIf { SEGMENT.matches(it) })
            "bye" -> ListenerLine.Bye(json.text("reason") ?: "")
            "error" -> ListenerLine.Error((json.text("message") ?: "unknown error").take(300))
            "" -> null
            else -> ListenerLine.Unknown(type.take(40))
        }
    }

    private fun JSONObject.text(key: String): String? =
        if (!has(key) || isNull(key)) null else (opt(key) as? String)

    private fun JSONObject.number(key: String): Double? {
        if (!has(key) || isNull(key)) return null
        val value = opt(key)
        return if (value is Number) value.toDouble().takeIf { it.isFinite() } else null
    }

    private fun JSONObject.long(key: String): Long? {
        if (!has(key) || isNull(key)) return null
        val value = opt(key)
        return if (value is Int || value is Long) (value as Number).toLong() else null
    }

    private fun JSONObject.int(key: String): Int? {
        if (!has(key) || isNull(key)) return null
        val value = opt(key)
        return if (value is Int) value else if (value is Long && value in Int.MIN_VALUE..Int.MAX_VALUE) value.toInt() else null
    }
}
