/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;

/** Verifies the imported SBK Grafana definition and its packaged copy. */
class GrafanaDashboardDefinitionTest {
    private static final Pattern METRIC = Pattern.compile("SBK_[A-Za-z0-9_]+");

    /** The original definition is packaged unchanged and retains its complete metric coverage. */
    @Test
    void packagesCompleteGrafanaDashboard() throws Exception {
        byte[] repositoryDefinition = Files.readAllBytes(
                Path.of("grafana", "dashboards", "sbk-dashboard.json"));
        byte[] packagedDefinition;
        try (InputStream input = GrafanaDashboardDefinitionTest.class.getResourceAsStream(
                "/grafana/dashboards/sbk-dashboard.json")) {
            packagedDefinition = input.readAllBytes();
        }
        assertArrayEquals(repositoryDefinition, packagedDefinition);

        JsonNode dashboard = new ObjectMapper().readTree(repositoryDefinition);
        assertEquals("SBK Dashboard", dashboard.path("title").asText());
        assertEquals(53, countPanels(dashboard.path("panels")));
        Set<String> metrics = new HashSet<>();
        dashboard.findValues("expr").forEach(expression -> {
            var matcher = METRIC.matcher(expression.asText());
            while (matcher.find()) {
                metrics.add(matcher.group());
            }
        });
        assertEquals(242, metrics.size());
        assertTrue(metrics.contains("SBK_Writing_MBPerSec"));
        assertTrue(metrics.contains("SBK_Reading_ns_99_99"));
    }

    private int countPanels(JsonNode panels) {
        int count = 0;
        for (JsonNode panel : panels) {
            count++;
            count += countPanels(panel.path("panels"));
        }
        return count;
    }
}
