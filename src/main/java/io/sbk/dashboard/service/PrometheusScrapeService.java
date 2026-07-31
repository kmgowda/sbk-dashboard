/**
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package io.sbk.dashboard.service;

import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.model.BenchmarkTarget;
import io.sbk.dashboard.model.EndpointSnapshot;
import io.sbk.dashboard.model.TargetStatus;
import java.io.InputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** In-JVM Prometheus scraper with one independently scheduled collector per registered endpoint. */
public final class PrometheusScrapeService implements AutoCloseable {
    private static final int MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
    private static final int RETENTION_MAINTENANCE_HOURS = 1;
    private final int intervalSeconds;
    private final MetricRepository repository;
    private final SegmentedMetricStore persistentStore;
    private final PrometheusTextParser parser = new PrometheusTextParser();
    private final ExecutorService scrapeThreads = Executors.newVirtualThreadPerTaskExecutor();
    private final ScheduledExecutorService scheduler;
    private final ScheduledFuture<?> retentionSchedule;
    private final HttpClient httpClient;
    private final Map<String, Collector> collectors = new ConcurrentHashMap<>();

    /**
     * Creates the scraper and schedules all persisted targets.
     *
     * @param registry persisted target registry
     * @param config runtime retention and persistence configuration
     */
    public PrometheusScrapeService(TargetRegistry registry, DashboardConfig config) {
        this(registry, config, RETENTION_MAINTENANCE_HOURS, TimeUnit.HOURS);
    }

    PrometheusScrapeService(TargetRegistry registry, DashboardConfig config,
                            long retentionMaintenancePeriod, TimeUnit retentionMaintenanceUnit) {
        this.intervalSeconds = config.scrapeIntervalSeconds();
        this.repository = new MetricRepository(config.retentionSamples());
        SegmentedMetricStore store = null;
        try {
            store = new SegmentedMetricStore(config.dataDirectory().resolve("timeseries"),
                    config.diskRetentionDays(), config.segmentSizeBytes());
        } catch (IOException | RuntimeException exception) {
            System.err.println("WARNING: Persistent metric history is unavailable: " + safeMessage(exception)
                    + ". The dashboard will continue with in-memory collection.");
        }
        this.persistentStore = store;
        int schedulerThreads = Math.max(1, Math.min(4, Runtime.getRuntime().availableProcessors()));
        this.scheduler = Executors.newScheduledThreadPool(schedulerThreads);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .executor(scrapeThreads)
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
        for (BenchmarkTarget target : registry.list()) {
            recover(target);
            register(target);
        }
        this.retentionSchedule = persistentStore == null ? null : scheduler.scheduleWithFixedDelay(
                this::runRetentionMaintenance, retentionMaintenancePeriod,
                retentionMaintenancePeriod, retentionMaintenanceUnit);
    }

    /**
     * Starts an independent schedule for a newly registered endpoint.
     *
     * @param target endpoint to scrape
     */
    public void register(BenchmarkTarget target) {
        collectors.computeIfAbsent(target.id(), ignored -> {
            Collector collector = new Collector(target);
            collector.schedule = scheduler.scheduleWithFixedDelay(
                    () -> scrapeThreads.submit(() -> scrape(collector)), 0, intervalSeconds, TimeUnit.SECONDS);
            return collector;
        });
    }

    /**
     * Stops collection and releases retained data for an endpoint.
     *
     * @param targetId endpoint identifier
     */
    public void unregister(String targetId) {
        Collector collector = collectors.remove(targetId);
        if (collector != null) {
            synchronized (collector) {
                collector.active = false;
                if (collector.schedule != null) {
                    collector.schedule.cancel(false);
                }
                repository.remove(targetId);
                deletePersisted(targetId);
            }
        } else {
            repository.remove(targetId);
            deletePersisted(targetId);
        }
    }

    /**
     * Returns latest status for inventory display.
     *
     * @param targetId endpoint identifier
     * @return scrape status
     */
    public TargetStatus status(String targetId) {
        return repository.status(targetId);
    }

    /**
     * Returns retained metric series for a dedicated dashboard.
     *
     * @param targetId endpoint identifier
     * @param maxPoints maximum points returned per series
     * @return endpoint snapshot
     */
    public EndpointSnapshot snapshot(String targetId, int maxPoints) {
        return repository.snapshot(targetId, maxPoints);
    }

    /** Stops every endpoint schedule and all Java collection threads. */
    @Override
    public void close() {
        collectors.values().forEach(collector -> {
            synchronized (collector) {
                collector.active = false;
                collector.schedule.cancel(false);
            }
        });
        collectors.clear();
        if (retentionSchedule != null) {
            retentionSchedule.cancel(false);
        }
        scheduler.shutdownNow();
        scrapeThreads.shutdownNow();
        if (persistentStore != null) {
            persistentStore.close();
        }
    }

    private void scrape(Collector collector) {
        if (!collector.running.compareAndSet(false, true)) {
            return;
        }
        if (!collector.active) {
            collector.running.set(false);
            return;
        }
        BenchmarkTarget target = collector.target;
        try {
            URI uri = URI.create("http://" + target.prometheusAddress() + target.metricsPath());
            HttpRequest request = HttpRequest.newBuilder(uri)
                    .timeout(Duration.ofSeconds(Math.max(4, intervalSeconds)))
                    .header("Accept", "text/plain; version=0.0.4, application/openmetrics-text; q=0.9")
                    .GET().build();
            HttpResponse<InputStream> response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                response.body().close();
                updateDown(collector, "HTTP " + response.statusCode());
                return;
            }
            byte[] bytes;
            try (InputStream body = response.body()) {
                bytes = body.readNBytes(MAX_RESPONSE_BYTES + 1);
            }
            if (bytes.length > MAX_RESPONSE_BYTES) {
                updateDown(collector, "Metrics response exceeds 16 MiB");
                return;
            }
            var metrics = parser.parse(new String(bytes, StandardCharsets.UTF_8));
            if (metrics.isEmpty()) {
                updateDown(collector, "Metrics response contained no samples");
                return;
            }
            long timestamp = System.currentTimeMillis();
            synchronized (collector) {
                if (collector.active) {
                    String persistenceDetail = "";
                    if (persistentStore == null) {
                        persistenceDetail = "; WARNING: persistence unavailable";
                    } else {
                        try {
                            persistentStore.append(target.id(), timestamp, metrics);
                        } catch (IOException exception) {
                            persistenceDetail = "; WARNING: persistence error";
                            System.err.println("WARNING: Unable to persist metrics for " + target.id() + ": "
                                    + exception.getMessage() + ". Live dashboard operation will continue.");
                        }
                    }
                    repository.append(target.id(), timestamp, metrics);
                    repository.status(target.id(), new TargetStatus("up", Instant.ofEpochMilli(timestamp).toString(),
                            metrics.size() + " samples" + persistenceDetail));
                }
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        } catch (Exception exception) {
            updateDown(collector, safeMessage(exception));
        } finally {
            collector.running.set(false);
        }
    }

    private void updateDown(Collector collector, String detail) {
        synchronized (collector) {
            if (collector.active) {
                repository.status(collector.target.id(), new TargetStatus("down", Instant.now().toString(), detail));
            }
        }
    }

    private String safeMessage(Exception exception) {
        String message = exception.getMessage();
        if (message == null || message.isBlank()) {
            return exception.getClass().getSimpleName();
        }
        return message.length() > 180 ? message.substring(0, 180) : message;
    }

    private void recover(BenchmarkTarget target) {
        if (persistentStore == null) {
            repository.status(target.id(), new TargetStatus("pending", Instant.now().toString(),
                    "WARNING: Historical metrics are unavailable; waiting for live endpoint"));
            return;
        }
        try {
            SegmentedMetricStore.RecoveryReport report = persistentStore.recover(target.id(),
                    (timestamp, metrics) -> repository.append(target.id(), timestamp, metrics));
            if (report.recoveredFrames() > 0 || report.expiredSegments() > 0 || report.damagedSegments() > 0) {
                String detail = recoveryDetail(report);
                repository.status(target.id(), new TargetStatus("pending",
                        recoveredAt(target.id(), report.recoveredFrames()), detail));
            }
        } catch (IOException | RuntimeException exception) {
            String detail = "WARNING: Historical metrics could not be recovered; live collection will continue";
            repository.status(target.id(), new TargetStatus("pending", Instant.now().toString(), detail));
            System.err.println(detail + " for " + target.id() + ": " + safeMessage(exception));
        }
    }

    private void deletePersisted(String targetId) {
        if (persistentStore == null) {
            return;
        }
        try {
            persistentStore.delete(targetId);
        } catch (IOException exception) {
            System.err.println("WARNING: Unable to delete persisted metrics for " + targetId + ": "
                    + exception.getMessage() + ". Live dashboard operation will continue.");
        }
    }

    private void runRetentionMaintenance() {
        try {
            SegmentedMetricStore.MaintenanceReport report = persistentStore.pruneExpired();
            if (report.deletedSegments() > 0) {
                System.out.println("Background retention removed " + report.deletedSegments()
                        + " expired time-series segment(s)");
            }
        } catch (IOException | RuntimeException exception) {
            System.err.println("WARNING: Background time-series retention failed: " + safeMessage(exception)
                    + ". The dashboard will continue and retry automatically.");
        }
    }

    private String recoveredAt(String targetId, int recoveredFrames) {
        if (recoveredFrames == 0) {
            return Instant.now().toString();
        }
        return Instant.ofEpochMilli(repository.snapshot(targetId, 10).collectedAt()).toString();
    }

    private String recoveryDetail(SegmentedMetricStore.RecoveryReport report) {
        String detail = "Recovered " + report.recoveredFrames() + " persisted scrapes";
        if (report.expiredSegments() > 0) {
            detail += "; removed " + report.expiredSegments() + " segment(s) older than retention";
        }
        if (report.damagedSegments() > 0) {
            detail += "; WARNING: ignored " + report.damagedSegments() + " damaged segment(s)";
        }
        return detail + "; waiting for live endpoint";
    }

    private static final class Collector {
        private final BenchmarkTarget target;
        private final AtomicBoolean running = new AtomicBoolean();
        private volatile boolean active = true;
        private volatile ScheduledFuture<?> schedule;

        private Collector(BenchmarkTarget target) {
            this.target = target;
        }
    }
}
