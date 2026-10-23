package ch.bodhi.perception

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StartBudgetTest {
    @Test fun aFewStartsPerDriveThenAPause() {
        val budget = StartBudget(max = 3)
        repeat(3) { budget.count("00000134--a5c8197883") }
        assertTrue(budget.exhausted)
        // Same drive, car on: no more listeners (each would keep a carState slot until the next ignition).
        assertFalse(budget.allows(onroad = true, newestRoute = "00000134--a5c8197883"))
        // A new drive frees the slots.
        assertTrue(budget.allows(onroad = true, newestRoute = "00000135--0b1c2d3e4f"))
        assertFalse(budget.exhausted)
        repeat(3) { budget.count("00000135--0b1c2d3e4f") }
        // The car off: the next ignition frees them anyway.
        assertTrue(budget.allows(onroad = false, newestRoute = "00000135--0b1c2d3e4f"))
    }

    @Test fun anotherDriveCountsFromZero() {
        val budget = StartBudget(max = 2)
        budget.count("00000001--aaaaaaaaaa")
        budget.count("00000002--bbbbbbbbbb")
        assertEquals(1, budget.usedThisDrive)
        assertFalse(budget.exhausted)
    }

    @Test fun readsTheDriveOfNow() {
        assertEquals(true to "00000134--a5c8197883", StartBudget.parseDrive("0\n00000134--a5c8197883\n"))
        assertEquals(false to "00000134--a5c8197883", StartBudget.parseDrive("1\n00000134--a5c8197883"))
        val (onroad, route) = StartBudget.parseDrive("\n\n")
        assertFalse(onroad)
        assertNull(route)
    }
}
