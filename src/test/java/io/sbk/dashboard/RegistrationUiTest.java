/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/** Tests the endpoint registration presentation. */
class RegistrationUiTest {
    /** Registration omits producer selection while retaining default port guidance. */
    @Test
    void doesNotRequireProducerSelection() throws Exception {
        String page;
        try (var input = RegistrationUiTest.class.getResourceAsStream("/web/index.html")) {
            page = new String(input.readAllBytes(), StandardCharsets.UTF_8);
        }
        String script;
        try (var input = RegistrationUiTest.class.getResourceAsStream("/web/app.js")) {
            script = new String(input.readAllBytes(), StandardCharsets.UTF_8);
        }

        assertFalse(page.contains("Producer"));
        assertFalse(page.contains("name=\"kind\""));
        assertFalse(script.contains("target.kind"));
        assertTrue(page.contains("SBK :9718"));
        assertTrue(page.contains("SBM :9719"));
    }
}
