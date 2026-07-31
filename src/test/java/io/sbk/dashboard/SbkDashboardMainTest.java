/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/** Tests application runtime and argument diagnostics. */
class SbkDashboardMainTest {
    /** Help startup reports the Java runtime and supplied arguments. */
    @Test
    void reportsRuntimeAndArguments() {
        PrintStream original = System.out;
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try {
            System.setOut(new PrintStream(output, true, StandardCharsets.UTF_8));
            SbkDashboardMain.main(new String[] {"-h"});
        } finally {
            System.setOut(original);
        }

        String text = output.toString(StandardCharsets.UTF_8);
        assertTrue(text.contains("Java version: " + System.getProperty("java.version")));
        assertTrue(text.contains("Java home: " + System.getProperty("java.home")));
        assertTrue(text.contains("Supplied arguments: -h"));
        assertTrue(text.contains("-retention"));
    }
}
