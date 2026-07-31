/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests append-only segment rollover, recovery, corruption handling, and deletion. */
class SegmentedMetricStoreTest {
    private static final String TARGET_ID = "0123456789abcdef";
    private static final String SECOND_TARGET_ID = "fedcba9876543210";
    @TempDir
    private Path temporaryDirectory;

    /** Replays checksummed frames across multiple small segments. */
    @Test
    void rollsSegmentsAndRecoversMetrics() throws Exception {
        Path root = temporaryDirectory.resolve("series");
        List<PrometheusTextParser.ParsedMetric> metrics = List.of(
                new PrometheusTextParser.ParsedMetric("SBK_Writing_MBPerSec",
                        Map.of("class", "File", "action", "Writing"), 10.25));
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 96)) {
            store.append(TARGET_ID, 1000, metrics);
            store.append(TARGET_ID, 2000, metrics);
            store.append(TARGET_ID, 3000, metrics);
        }
        try (var files = Files.list(root.resolve(TARGET_ID))) {
            assertTrue(files.count() > 1);
        }

        List<Long> timestamps = new ArrayList<>();
        List<PrometheusTextParser.ParsedMetric> recoveredMetrics = new ArrayList<>();
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 96)) {
            SegmentedMetricStore.RecoveryReport report = store.recover(TARGET_ID, (timestamp, batch) -> {
                timestamps.add(timestamp);
                recoveredMetrics.addAll(batch);
            });
            assertEquals(3, report.recoveredFrames());
        }
        assertEquals(List.of(1000L, 2000L, 3000L), timestamps);
        assertEquals("File", recoveredMetrics.getFirst().labels().get("class"));
        assertEquals(10.25, recoveredMetrics.getFirst().value());
    }

    /** Ignores a truncated tail while retaining all preceding complete frames. */
    @Test
    void recoversBeforeTruncatedTail() throws Exception {
        Path root = temporaryDirectory.resolve("truncated");
        var metric = new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1);
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 1024)) {
            store.append(TARGET_ID, 1000, List.of(metric));
        }
        Path segment;
        try (var files = Files.list(root.resolve(TARGET_ID))) {
            segment = files.findFirst().orElseThrow();
        }
        Files.write(segment, new byte[] {0, 0, 0, 20, 1, 2}, StandardOpenOption.APPEND);

        List<Long> timestamps = new ArrayList<>();
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 1024)) {
            SegmentedMetricStore.RecoveryReport report = store.recover(TARGET_ID,
                    (timestamp, batch) -> timestamps.add(timestamp));
            assertEquals(1, report.recoveredFrames());
            assertEquals(1, report.damagedSegments());
        }
        assertEquals(List.of(1000L), timestamps);
    }

    /** Rejects a frame whose payload no longer matches its checksum. */
    @Test
    void rejectsChecksumCorruption() throws Exception {
        Path root = temporaryDirectory.resolve("checksum");
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 1024)) {
            store.append(TARGET_ID, 1000,
                    List.of(new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1)));
        }
        Path segment;
        try (var files = Files.list(root.resolve(TARGET_ID))) {
            segment = files.findFirst().orElseThrow();
        }
        byte[] bytes = Files.readAllBytes(segment);
        bytes[bytes.length - 1] ^= 1;
        Files.write(segment, bytes);

        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 1024)) {
            SegmentedMetricStore.RecoveryReport report = store.recover(TARGET_ID,
                    (timestamp, batch) -> { });
            assertEquals(0, report.recoveredFrames());
            assertEquals(1, report.damagedSegments());
        }
    }

    /** Prunes segments older than the configured disk retention period. */
    @Test
    void prunesExpiredSegmentsDuringRecovery() throws Exception {
        Path root = temporaryDirectory.resolve("retention");
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 1, 1024)) {
            store.append(TARGET_ID, 1000,
                    List.of(new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1)));
        }
        Path segment;
        try (var files = Files.list(root.resolve(TARGET_ID))) {
            segment = files.findFirst().orElseThrow();
        }
        Files.setLastModifiedTime(segment, FileTime.fromMillis(System.currentTimeMillis() - 172_800_000));

        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 1, 1024)) {
            SegmentedMetricStore.RecoveryReport report = store.recover(TARGET_ID,
                    (timestamp, batch) -> { });
            assertEquals(0, report.recoveredFrames());
            assertEquals(1, report.expiredSegments());
        }
        assertFalse(Files.exists(segment));
    }

    /** Background maintenance removes stale active data without requiring another scrape. */
    @Test
    void prunesExpiredActiveSegmentsWhileRunning() throws Exception {
        Path root = temporaryDirectory.resolve("background-retention");
        var metric = new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1);
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 1, 1024)) {
            store.append(TARGET_ID, 1000, List.of(metric));
            store.append(SECOND_TARGET_ID, 1000, List.of(metric));
            Path expired;
            Path retained;
            try (var files = Files.list(root.resolve(TARGET_ID))) {
                expired = files.findFirst().orElseThrow();
            }
            try (var files = Files.list(root.resolve(SECOND_TARGET_ID))) {
                retained = files.findFirst().orElseThrow();
            }
            Files.setLastModifiedTime(expired,
                    FileTime.fromMillis(System.currentTimeMillis() - 172_800_000));

            SegmentedMetricStore.MaintenanceReport report = store.pruneExpired();

            assertEquals(1, report.deletedSegments());
            assertEquals(0, report.failedEndpoints());
            assertFalse(Files.exists(expired));
            assertTrue(Files.exists(retained));
            store.append(TARGET_ID, 2000, List.of(metric));
            try (var files = Files.list(root.resolve(TARGET_ID))) {
                assertEquals(1, files.count());
            }
        }
    }

    /** Deletes only the requested endpoint partition. */
    @Test
    void deletesEndpointPartition() throws Exception {
        Path root = temporaryDirectory.resolve("delete");
        try (SegmentedMetricStore store = new SegmentedMetricStore(root, 7, 1024)) {
            store.append(TARGET_ID, 1000,
                    List.of(new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1)));
            store.delete(TARGET_ID);
        }
        assertFalse(Files.exists(root.resolve(TARGET_ID)));
    }
}
