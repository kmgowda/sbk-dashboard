/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import io.sbk.dashboard.model.BenchmarkKind;
import io.sbk.dashboard.model.BenchmarkTarget;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests dynamic Prometheus file-based service discovery. */
class PrometheusTargetDiscoveryTest {
    @TempDir
    private Path temporaryDirectory;

    /** Each host and port becomes an independently labelled scrape target. */
    @Test
    void writesCompleteTargetSnapshot() throws Exception {
        Path file = temporaryDirectory.resolve("prometheus/targets.json");
        BenchmarkTarget first = target("one", "bench.example", 9718, "/metrics");
        BenchmarkTarget second = target("two", "2001:db8::1", 9719, "/prometheus");

        new PrometheusTargetDiscovery(file).write(List.of(first, second));

        JsonNode groups = JsonSupport.mapper().readTree(Files.readAllBytes(file));
        assertEquals(2, groups.size());
        assertEquals("bench.example:9718", groups.get(0).path("targets").get(0).asText());
        assertEquals("one", groups.get(0).path("labels").path("sbk_endpoint_id").asText());
        assertEquals("/metrics", groups.get(0).path("labels").path("sbk_metrics_path").asText());
        assertEquals("[2001:db8::1]:9719", groups.get(1).path("targets").get(0).asText());
    }

    private BenchmarkTarget target(String id, String host, int port, String path) {
        return new BenchmarkTarget(id, id, host, port, path, BenchmarkKind.SBK, "2026-01-01T00:00:00Z");
    }
}
