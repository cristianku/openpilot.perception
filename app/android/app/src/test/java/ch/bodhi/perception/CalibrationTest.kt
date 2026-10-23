package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CalibrationTest {
    @Test fun distanceFromTheComma() {
        // The site today: radar to comma 1.50 m, correction -0.89 m.
        val site = CalibrationValues(radarToCameraM = 1.5, correctionM = -0.89)
        assertEquals(2.86, RadarMath.fromComma(2.25, site)!!, 1e-9)
        assertNull(RadarMath.fromComma(2.25, null))
    }

    @Test fun oneTestGivesCorrectionAndOffset() {
        val test = TapePoint(readingM = 2.25, tapeRadarM = 1.32, tapeCommaM = 2.82)
        assertEquals(-0.93, test.correctionM, 1e-9)
        assertEquals(1.5, test.radarToCameraM, 1e-9)
        assertEquals("Reads 0.93 m too long", RadarMath.verdict(test.correctionM))
        assertEquals("Reads 0.10 m too short", RadarMath.verdict(0.1))
        assertEquals("Reads just right", RadarMath.verdict(0.004))
    }

    @Test fun theMeansOfTheTests() {
        val combined = RadarMath.combine(listOf(TapePoint(1.75, 0.9, 2.4), TapePoint(2.25, 1.32, 2.82)))!!
        assertEquals(2, combined.count)
        assertEquals(-0.89, combined.correctionM, 1e-9)
        assertEquals(1.5, combined.radarToCameraM, 1e-9)
        assertEquals(0.08, combined.spreadM, 1e-9)
        // Half a step for one test, divided by the square root of the number of tests.
        assertEquals(0.09, combined.uncertaintyM, 1e-9)
        assertEquals(0.13, RadarMath.combine(listOf(TapePoint(2.25, 1.32, 2.82)))!!.uncertaintyM, 1e-9)
        assertNull(RadarMath.combine(emptyList()))
        assertEquals("close_only", RadarMath.statusOf(combined))
        assertEquals("ready", RadarMath.statusOf(RadarMath.combine(listOf(TapePoint(2.25, 1.32, 2.82), TapePoint(11.0, 10.0, 11.5)))))
        // Corrections more than 0.5 m apart disagree: not ready even with a far test.
        assertEquals("close_only", RadarMath.statusOf(RadarMath.combine(listOf(TapePoint(2.25, 1.32, 2.82), TapePoint(10.0, 10.0, 11.5)))))
    }

    @Test fun theTapeChecksOfTheSite() {
        assertEquals("Write the distance first.", RadarMath.checkTapeRadar(null, Target.WALL).bad)
        assertEquals("Between 0.30 and 100 m.", RadarMath.checkTapeRadar(0.2, Target.WALL).bad)
        assertTrue(RadarMath.checkTapeRadar(0.5, Target.WALL).warning.startsWith("Very close"))
        assertTrue(RadarMath.checkTapeRadar(6.0, Target.WALL).warning.startsWith("Walls farther"))
        assertEquals("For this test, 5 to 30 m works best.", RadarMath.checkTapeRadar(2.0, Target.CAR).warning)
        assertTrue(RadarMath.checkTapeRadar(1.5, Target.WALL).let { it.ok && it.warning.isEmpty() })

        assertEquals("The comma must be farther from the wall than the radar.", RadarMath.checkTapeComma(1.32, 1.32, Target.WALL, null).bad)
        assertEquals("More than 5 m from the radar: check the tape.", RadarMath.checkTapeComma(6.4, 1.32, Target.WALL, null).bad)
        val fine = RadarMath.checkTapeComma(2.82, 1.32, Target.WALL, 1.5)
        assertTrue(fine.ok)
        assertEquals("So the comma sits 1.50 m behind the radar.", fine.info)
        assertEquals("", fine.warning)
        assertTrue(RadarMath.checkTapeComma(2.9, 1.32, Target.WALL, 1.5).warning.contains("8 cm different"))
    }

    @Test fun typedMetres() {
        assertEquals(1.32, RadarMath.parseMetres("1,32")!!, 0.0)
        assertEquals(1.32, RadarMath.parseMetres(" 1.32 m")!!, 0.0)
        assertEquals(1.33, RadarMath.parseMetres("1.326")!!, 0.0)
        assertEquals(10.0, RadarMath.parseMetres("10")!!, 0.0)
        for (bad in listOf("", "m", "1.2.3", "-1", "1e3", "abc", "1,3,2")) assertNull(bad, RadarMath.parseMetres(bad))
        assertEquals("−0.89 m", RadarMath.signedMetres(-0.89))
        assertEquals("+0.10 m", RadarMath.signedMetres(0.1))
    }

    @Test fun correctionRange() {
        assertTrue(RadarMath.correctionInRange(-0.93))
        assertFalse(RadarMath.correctionInRange(-1.2))
    }
}
