/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.model.BenchmarkKind;
import io.sbk.dashboard.model.BenchmarkTarget;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests persistent endpoint registration. */
class TargetRegistryTest {
    @TempDir
    private Path temporaryDirectory;

    /** Verifies registration persists and reloads an endpoint. */
    @Test
    void registersAndReloadsTarget() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());
        BenchmarkTarget target = registry.register("Primary run", "bench.example", 9718, "/metrics",
                BenchmarkKind.SBK);

        TargetRegistry reloaded = new TargetRegistry(config());
        assertEquals(target, reloaded.find(target.id()));
        assertEquals(1, reloaded.list().size());
    }

    /** Verifies producer classification is not required for registration. */
    @Test
    void registersWithoutProducerClassification() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());

        BenchmarkTarget target = registry.register("Unclassified run", "bench.example", 9719, "/metrics");

        assertEquals("bench.example", target.host());
        assertEquals(9719, target.port());
    }

    /** Verifies host and port are the uniqueness boundary. */
    @Test
    void rejectsDuplicateAddress() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());
        registry.register("SBK", "HOST.example", 9718, "/metrics", BenchmarkKind.SBK);
        assertThrows(IllegalStateException.class,
                () -> registry.register("SBM", "host.example", 9718, "/other", BenchmarkKind.SBM));
    }

    /** Verifies the same host remains valid with a different port. */
    @Test
    void acceptsSameHostWithDifferentPort() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());
        registry.register("One", "host.example", 9718, "/metrics", BenchmarkKind.SBK);
        registry.register("Two", "host.example", 9719, "/metrics", BenchmarkKind.SBM);
        assertEquals(2, registry.list().size());
    }

    /** Verifies removal updates persistent state. */
    @Test
    void removesTarget() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());
        BenchmarkTarget target = registry.register("Run", "127.0.0.1", 9718, "/metrics", BenchmarkKind.SBK);
        assertTrue(registry.remove(target.id()));
        assertFalse(registry.remove(target.id()));
        assertTrue(new TargetRegistry(config()).list().isEmpty());
    }

    /** Verifies malformed endpoint inputs are rejected. */
    @Test
    void validatesEndpointInput() throws Exception {
        TargetRegistry registry = new TargetRegistry(config());
        assertThrows(IllegalArgumentException.class,
                () -> registry.register("Bad", "http://host", 9718, "/metrics", BenchmarkKind.SBK));
        assertThrows(IllegalArgumentException.class,
                () -> registry.register("Bad", "host", 0, "/metrics", BenchmarkKind.SBK));
        assertThrows(IllegalArgumentException.class,
                () -> registry.register("Bad", "host", 9718, "metrics", BenchmarkKind.SBK));
    }

    private DashboardConfig config() {
        return new DashboardConfig(9721, false, temporaryDirectory.resolve("data"), 5, 7);
    }
}
