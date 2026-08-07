package com.spatulox.mcbumptest;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/**
 * Gives the unit-tests job something real to run.
 *
 * <p>Also the fixture for tests.unit.require-non-empty: delete this file and the
 * pipeline must go red rather than report a green with zero tests.
 */
class TestModTest {

    @Test
    void aHealthyRunReportsEveryMarker() {
        // MCBUMP_BREAK is unset here, so the reported count is the real one.
        assertEquals(3, TestMod.reportedCount());
    }

    @Test
    void nothingIsBrokenByDefault() {
        assertEquals("", TestMod.broken());
    }
}
