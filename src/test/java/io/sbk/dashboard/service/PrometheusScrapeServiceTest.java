/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.model.BenchmarkKind;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Integration test for scheduled HTTP scraping on Java threads. */
class PrometheusScrapeServiceTest {
    @TempDir
    private Path temporaryDirectory;

    /** Scrapes a live HTTP exposition endpoint into a dedicated repository partition. */
    @Test
    void scrapesEndpointOnJavaThreads() throws Exception {
        HttpServer exporter = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        byte[] response = ("# TYPE SBK_Writing_MBPerSec gauge\n"
                + "SBK_Writing_MBPerSec{class=\"File\",action=\"Writing\"} 42.25\n")
                .getBytes(StandardCharsets.UTF_8);
        exporter.createContext("/metrics", exchange -> {
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        exporter.start();
        try {
            DashboardConfig config = new DashboardConfig(9721, false, temporaryDirectory, 1, 20);
            TargetRegistry registry = new TargetRegistry(config);
            var target = registry.register("Test", "127.0.0.1", exporter.getAddress().getPort(),
                    "/metrics", BenchmarkKind.SBK);
            try (PrometheusScrapeService scraper = new PrometheusScrapeService(registry, config)) {
                long deadline = System.nanoTime() + java.time.Duration.ofSeconds(5).toNanos();
                while (scraper.snapshot(target.id(), 20).series().isEmpty() && System.nanoTime() < deadline) {
                    Thread.sleep(25);
                }
                var snapshot = scraper.snapshot(target.id(), 20);
                assertEquals("up", snapshot.status().state());
                assertFalse(snapshot.series().isEmpty());
                assertEquals(42.25, snapshot.series().getFirst().current());
            }
        } finally {
            exporter.stop(0);
        }
    }

    /** Replays persisted endpoint history before the first live scrape completes. */
    @Test
    void replaysPersistedHistoryAtStartup() throws Exception {
        DashboardConfig config = new DashboardConfig(9721, false, temporaryDirectory.resolve("recovery"), 30, 20,
                24, 1024 * 1024);
        TargetRegistry registry = new TargetRegistry(config);
        var target = registry.register("Offline", "127.0.0.1", 61999, "/metrics", BenchmarkKind.SBK);
        try (SegmentedMetricStore store = new SegmentedMetricStore(config.dataDirectory().resolve("timeseries"),
                config.diskRetentionDays(), config.segmentSizeBytes())) {
            store.append(target.id(), 1234,
                    java.util.List.of(new PrometheusTextParser.ParsedMetric("SBK_Writing_MBPerSec",
                            java.util.Map.of(), 77.5)));
        }

        try (PrometheusScrapeService scraper = new PrometheusScrapeService(registry, config)) {
            var snapshot = scraper.snapshot(target.id(), 20);
            assertFalse(snapshot.series().isEmpty());
            assertEquals(77.5, snapshot.series().getFirst().current());
            assertEquals(1234, snapshot.collectedAt());
        }
    }

    /** Starts live collection even when the historical time-series directory cannot be opened. */
    @Test
    void startsWhenPersistentHistoryIsUnavailable() throws Exception {
        Path data = temporaryDirectory.resolve("unavailable");
        DashboardConfig config = new DashboardConfig(9721, false, data, 30, 20);
        TargetRegistry registry = new TargetRegistry(config);
        registry.register("Offline", "127.0.0.1", 61998, "/metrics", BenchmarkKind.SBK);
        Files.writeString(data.resolve("timeseries"), "not a directory");

        try (PrometheusScrapeService scraper = new PrometheusScrapeService(registry, config)) {
            assertEquals(0, scraper.snapshot(registry.list().getFirst().id(), 20).series().size());
        }
    }

    /** The service scheduler removes expired history without scrape or restart activity. */
    @Test
    void schedulesBackgroundRetentionWhileRunning() throws Exception {
        Path data = temporaryDirectory.resolve("scheduled-retention");
        DashboardConfig config = new DashboardConfig(9721, false, data, 30, 20,
                1, 1024 * 1024);
        TargetRegistry registry = new TargetRegistry(config);
        Path segment;
        try (PrometheusScrapeService scraper = new PrometheusScrapeService(
                registry, config, 25, TimeUnit.MILLISECONDS)) {
            String targetId = "0123456789abcdef";
            try (SegmentedMetricStore writer = new SegmentedMetricStore(data.resolve("timeseries"), 1, 1024)) {
                writer.append(targetId, 1000,
                        List.of(new PrometheusTextParser.ParsedMetric("SBK_Writers", Map.of(), 1)));
            }
            try (var files = Files.list(data.resolve("timeseries").resolve(targetId))) {
                segment = files.findFirst().orElseThrow();
            }
            Files.setLastModifiedTime(segment,
                    FileTime.fromMillis(System.currentTimeMillis() - 172_800_000));

            long deadline = System.nanoTime() + java.time.Duration.ofSeconds(3).toNanos();
            while (Files.exists(segment) && System.nanoTime() < deadline) {
                Thread.sleep(10);
            }
            assertFalse(Files.exists(segment));
            assertTrue(Files.isDirectory(data.resolve("timeseries").resolve(targetId)));
        }
    }
}
