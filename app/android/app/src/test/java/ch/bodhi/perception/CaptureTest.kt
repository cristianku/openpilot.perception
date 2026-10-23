package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureTest {
    /** 10 Hz samples from the comma: [count] of them, reading and speed by index. */
    private fun stream(count: Int, reading: (Int) -> Double?, speed: (Int) -> Double? = { 0.0 }, startNs: Long = 5_000_000_000L) =
        (0 until count).map { RadarSample(startNs + it * 100_000_000L, 1_000L + it * 100L, speed(it), reading(it)) }

    @Test fun twentyStillSecondsAtOneReading() {
        val samples = stream(200, { if (it % 50 == 7) 2.5 else 2.25 })
        val result = CaptureRules.evaluate(samples, 1792771170.0, onroad = true)
        assertTrue(result.problems.toString(), result.ok)
        val summary = result.summary
        assertEquals(200, summary.samples)
        assertEquals(200, summary.withReading)
        assertEquals(2.25, summary.readingM!!, 0.0)
        assertEquals(mapOf("2.25" to 196, "2.50" to 4), summary.histogram)
        assertEquals(0.98, summary.steadyShare!!, 0.0)
        assertEquals(19.6, summary.steadyS, 1e-9)
        assertEquals(0.0, summary.speedMaxMps!!, 0.0)
        assertTrue(summary.onroad)
    }

    @Test fun theCarMoved() {
        // Rolling for the second half: only 10 s still minus the moment it started.
        val samples = stream(200, { 2.25 }, { if (it < 95) 0.0 else 0.3 })
        val result = CaptureRules.evaluate(samples, 0.0, onroad = true)
        assertFalse(result.ok)
        assertTrue(result.problems.any { it.startsWith("The car moved (up to 0.30 m/s)") })
        assertTrue(result.problems.toString(), result.problems.any { it.startsWith("Only 9.5 s of steady reading: 10 s are needed") })
        assertEquals(0.3, result.summary.speedMaxMps!!, 1e-9)
    }

    @Test fun exactlyTenSteadySecondsIsEnough() {
        val samples = stream(200, { if (it < 100) 2.25 else null })
        val result = CaptureRules.evaluate(samples, 0.0, onroad = true)
        assertEquals(10.0, result.summary.steadyS, 1e-9)
        assertTrue(result.problems.toString(), result.ok)
    }

    @Test fun theRadarSawNothingMostOfTheTime() {
        val samples = stream(200, { if (it < 60) 2.25 else null })
        val result = CaptureRules.evaluate(samples, 0.0, onroad = true)
        assertFalse(result.ok)
        assertTrue(result.problems.any { it.startsWith("The radar saw something only 30%") })
        assertEquals(60, result.summary.withReading)
    }

    @Test fun theReadingKeptChanging() {
        val samples = stream(200, { if (it % 3 == 0) 2.5 else 2.25 })
        val result = CaptureRules.evaluate(samples, 0.0, onroad = true)
        assertFalse(result.ok)
        assertTrue(result.problems.any { it.startsWith("The reading kept changing (2.25 m only 67%") })
    }

    @Test fun gapsInTheStreamAreNotSteadyTime() {
        // 120 samples with a 9 s hole in the middle: 12 s of samples, but each one stands for 0.1 s only.
        val first = stream(60, { 2.25 })
        val second = stream(60, { 2.25 }, startNs = 5_000_000_000L + 15_000_000_000L)
        val result = CaptureRules.evaluate(first + second, 0.0, onroad = true)
        assertEquals(12.15, result.summary.steadyS, 0.06)
        val weights = CaptureRules.weights(first + second)
        assertEquals(CaptureRules.MAX_SAMPLE_S, weights[59], 1e-9)
    }

    @Test fun noSpeedIsNotStandingStill() {
        val result = CaptureRules.evaluate(stream(200, { 2.25 }, { null }), 0.0, onroad = true)
        assertFalse(result.ok)
        assertNull(result.summary.readingM)
        assertTrue(result.problems.first().startsWith("The comma sent no speed"))
    }

    @Test fun offroadAndLostConnection() {
        val off = CaptureRules.evaluate(emptyList(), 0.0, onroad = false)
        assertEquals(listOf("The car is off: switch it on and try again."), off.problems)
        val lost = CaptureRules.evaluate(stream(200, { 2.25 }), 0.0, onroad = true, connectionLost = true)
        assertFalse(lost.ok)
        assertTrue(lost.problems.first().startsWith("The connection to the comma dropped"))
    }

    @Test fun theLiveSteadyTime() {
        val tracker = SteadyTracker()
        for (t in 0L..5_000L step 100L) tracker.add(2.25, t)
        assertEquals(5.0, tracker.seconds(5_000), 1e-9)
        // A short loss of the target does not end it.
        tracker.add(null, 5_500)
        tracker.add(2.25, 5_800)
        assertEquals(5.8, tracker.seconds(5_800), 1e-9)
        // Another reading does.
        tracker.add(2.5, 6_000)
        assertEquals(2.5, tracker.value!!, 0.0)
        assertEquals(0.0, tracker.seconds(6_000), 1e-9)
        // And so does a loss longer than a second.
        tracker.add(null, 6_100)
        tracker.add(null, 7_200)
        assertNull(tracker.value)
        assertEquals(0.0, tracker.seconds(7_200), 1e-9)
    }
}
