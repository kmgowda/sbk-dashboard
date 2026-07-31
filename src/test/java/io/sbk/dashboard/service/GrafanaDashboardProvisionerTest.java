/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import io.sbk.dashboard.model.BenchmarkKind;
import io.sbk.dashboard.model.BenchmarkTarget;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests endpoint-specific clones of the canonical Grafana dashboard. */
class GrafanaDashboardProvisionerTest {
    @TempDir
    private Path temporaryDirectory;

    /** A clone preserves the dashboard while scoping every SBK PromQL selector. */
    @Test
    void scopesEveryMetricExpressionToEndpoint() throws Exception {
        GrafanaDashboardProvisioner provisioner = provisioner();
        BenchmarkTarget target = target("abc123", "bench.example", 9718);

        JsonNode dashboard = JsonSupport.mapper().readTree(provisioner.generatedDashboard(target));
        List<String> expressions = expressions(dashboard);

        assertEquals("sbk-abc123", dashboard.path("uid").asText());
        assertEquals("SBK Dashboard — bench.example:9718", dashboard.path("title").asText());
        assertFalse(expressions.isEmpty());
        assertTrue(expressions.stream().filter(value -> value.contains("SBK_"))
                .allMatch(value -> value.contains("sbk_endpoint_id=\"abc123\"")));
        assertTrue(expressions.stream().anyMatch(value -> value.contains(
                "action=\"Writing\",sbk_endpoint_id=\"abc123\"")));
    }

    /** Reconciliation creates one stable URL per endpoint and deletes orphan clones. */
    @Test
    void reconcilesDistinctDashboardsAndRemovesOrphans() throws Exception {
        GrafanaDashboardProvisioner provisioner = provisioner();
        BenchmarkTarget first = target("first", "same.example", 9718);
        BenchmarkTarget second = target("second", "same.example", 9719);

        provisioner.reconcile(List.of(first, second));

        assertTrue(Files.exists(temporaryDirectory.resolve("sbk-first.json")));
        assertTrue(Files.exists(temporaryDirectory.resolve("sbk-second.json")));
        assertEquals("http://grafana.example:3000/d/sbk-first/", provisioner.dashboardUrl("first"));
        assertEquals("http://grafana.example:3000/d/sbk-second/", provisioner.dashboardUrl("second"));

        provisioner.reconcile(List.of(second));
        assertFalse(Files.exists(temporaryDirectory.resolve("sbk-first.json")));
        assertTrue(Files.exists(temporaryDirectory.resolve("sbk-second.json")));
    }

    private GrafanaDashboardProvisioner provisioner() throws Exception {
        return new GrafanaDashboardProvisioner(temporaryDirectory, URI.create("http://grafana.example:3000/"));
    }

    private BenchmarkTarget target(String id, String host, int port) {
        return new BenchmarkTarget(id, id, host, port, "/metrics", BenchmarkKind.SBK,
                "2026-01-01T00:00:00Z");
    }

    private List<String> expressions(JsonNode node) {
        List<String> result = new ArrayList<>();
        collectExpressions(node, result);
        return result;
    }

    private void collectExpressions(JsonNode node, List<String> result) {
        if (node.isObject()) {
            node.properties().forEach(entry -> {
                if (entry.getKey().equals("expr") && entry.getValue().isTextual()) {
                    result.add(entry.getValue().asText());
                }
                collectExpressions(entry.getValue(), result);
            });
        } else if (node.isArray()) {
            node.forEach(child -> collectExpressions(child, result));
        }
    }
}
