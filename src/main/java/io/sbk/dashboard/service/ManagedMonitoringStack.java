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

import com.fasterxml.jackson.databind.JsonNode;
import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.config.MonitoringConfig;
import io.sbk.dashboard.config.RuntimePlatform;
import io.sbk.dashboard.model.BenchmarkTarget;
import io.sbk.dashboard.model.TargetStatus;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Owns native Prometheus/Grafana processes and reconciles targets and dashboards. */
public final class ManagedMonitoringStack implements AutoCloseable {
    private static final Duration STARTUP_TIMEOUT = Duration.ofSeconds(45);
    private final DashboardConfig dashboardConfig;
    private final MonitoringConfig monitoringConfig;
    private final Path runtimeDirectory;
    private final PrometheusTargetDiscovery targetDiscovery;
    private final GrafanaDashboardProvisioner dashboardProvisioner;
    private final ManagedProcessRegistry processRegistry;
    private final Map<String, TargetStatus> statuses = new ConcurrentHashMap<>();
    private final ScheduledExecutorService monitor = Executors.newSingleThreadScheduledExecutor();
    private final HttpClient httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private final RuntimePlatform platform = RuntimePlatform.current();
    private volatile List<BenchmarkTarget> targets = List.of();
    private Process prometheusProcess;
    private Process grafanaProcess;
    private boolean prometheusAttached;
    private boolean grafanaAttached;

    /**
     * Prepares configuration, starts both native servers, and reconciles registered endpoints.
     *
     * @param dashboardConfig dashboard runtime configuration
     * @param monitoringConfig native monitoring configuration
     * @param initialTargets persisted endpoints
     * @param continueExisting attach to compatible services already using the configured ports
     * @throws IOException when configuration or processes cannot be initialized
     * @throws InterruptedException when startup is interrupted
     */
    public ManagedMonitoringStack(DashboardConfig dashboardConfig, MonitoringConfig monitoringConfig,
                                  List<BenchmarkTarget> initialTargets, boolean continueExisting)
            throws IOException, InterruptedException {
        this.dashboardConfig = dashboardConfig;
        this.monitoringConfig = monitoringConfig;
        this.runtimeDirectory = dashboardConfig.dataDirectory().resolve("monitoring");
        this.targetDiscovery = new PrometheusTargetDiscovery(
                runtimeDirectory.resolve("prometheus/targets.json"));
        this.dashboardProvisioner = new GrafanaDashboardProvisioner(
                runtimeDirectory.resolve("grafana/dashboards"), monitoringConfig.grafanaPublicUrl());
        this.processRegistry = new ManagedProcessRegistry(runtimeDirectory.resolve("managed-processes.json"));
        prepareConfiguration();
        reconcile(initialTargets);
        try {
            if (!continueExisting) {
                PortProcessManager.terminateExisting(monitoringConfig.prometheusPort(),
                        monitoringConfig.grafanaPort(), processRegistry);
            }
            URI prometheusHealth = prometheusHealth();
            if (continueExisting && ready(prometheusHealth)) {
                prometheusAttached = true;
                System.out.println("Continuing existing Prometheus on port " + monitoringConfig.prometheusPort());
            } else {
                requireAvailableWhenContinuing("Prometheus", monitoringConfig.prometheusPort(), continueExisting);
                prometheusProcess = startPrometheus();
                processRegistry.record("prometheus", prometheusProcess, monitoringConfig.prometheusPort());
                awaitReady("Prometheus", prometheusProcess, prometheusHealth);
            }
            URI grafanaHealth = grafanaHealth();
            if (continueExisting && ready(grafanaHealth)) {
                grafanaAttached = true;
                System.out.println("Continuing existing Grafana on port " + monitoringConfig.grafanaPort());
            } else {
                requireAvailableWhenContinuing("Grafana", monitoringConfig.grafanaPort(), continueExisting);
                grafanaProcess = startGrafana();
                processRegistry.record("grafana", grafanaProcess, monitoringConfig.grafanaPort());
                awaitReady("Grafana", grafanaProcess, grafanaHealth);
            }
        } catch (IOException | InterruptedException exception) {
            close();
            throw exception;
        }
        refreshStatuses();
        monitor.scheduleWithFixedDelay(this::refreshStatuses, 5, 5, TimeUnit.SECONDS);
    }

    /** Atomically reconciles discovery, dashboards, and persisted URL mappings. */
    public synchronized void reconcile(List<BenchmarkTarget> registeredTargets) throws IOException {
        targets = List.copyOf(registeredTargets);
        targetDiscovery.write(targets);
        dashboardProvisioner.reconcile(targets);
        writeMappings();
        for (BenchmarkTarget target : targets) {
            statuses.putIfAbsent(target.id(), new TargetStatus("pending", Instant.now().toString(),
                    "Waiting for Prometheus target discovery"));
        }
        statuses.keySet().retainAll(targets.stream().map(BenchmarkTarget::id).toList());
    }

    /** Returns the current Prometheus target health for an endpoint. */
    public TargetStatus status(String targetId) {
        return statuses.getOrDefault(targetId, TargetStatus.pending());
    }

    /** Returns the deterministic dedicated Grafana URL. */
    public String dashboardUrl(String targetId) {
        return dashboardProvisioner.dashboardUrl(targetId);
    }

    /** Returns whether both managed native processes are alive. */
    public boolean healthy() {
        return componentHealthy(prometheusProcess, prometheusAttached, prometheusHealth())
                && componentHealthy(grafanaProcess, grafanaAttached, grafanaHealth());
    }

    /** Stops monitoring and both managed native processes. */
    @Override
    public void close() {
        monitor.shutdownNow();
        stop(grafanaProcess);
        stop(prometheusProcess);
        removeOwnership("grafana", grafanaProcess);
        removeOwnership("prometheus", prometheusProcess);
    }

    private void prepareConfiguration() throws IOException {
        Path prometheusDirectory = runtimeDirectory.resolve("prometheus");
        Path grafanaDirectory = runtimeDirectory.resolve("grafana");
        Files.createDirectories(prometheusDirectory.resolve("data"));
        Files.createDirectories(grafanaDirectory.resolve("data/plugins"));
        Files.createDirectories(grafanaDirectory.resolve("logs"));
        Files.createDirectories(grafanaDirectory.resolve("provisioning/datasources"));
        Files.createDirectories(grafanaDirectory.resolve("provisioning/dashboards"));
        Files.createDirectories(grafanaDirectory.resolve("dashboards"));
        Files.createDirectories(runtimeDirectory.resolve("logs"));
        AtomicFiles.write(prometheusDirectory.resolve("prometheus.yml"), prometheusConfiguration());
        AtomicFiles.write(grafanaDirectory.resolve("grafana.ini"), grafanaConfiguration());
        AtomicFiles.write(grafanaDirectory.resolve("provisioning/datasources/prometheus.yml"),
                datasourceConfiguration());
        AtomicFiles.write(grafanaDirectory.resolve("provisioning/dashboards/sbk.yml"),
                dashboardProviderConfiguration());
    }

    private Process startPrometheus() throws IOException {
        Path executable = resolveExecutable(monitoringConfig.prometheusBinary(), "Prometheus");
        Path directory = runtimeDirectory.resolve("prometheus");
        List<String> command = List.of(executable.toString(),
                "--config.file=" + directory.resolve("prometheus.yml"),
                "--storage.tsdb.path=" + directory.resolve("data"),
                "--storage.tsdb.retention.time=" + dashboardConfig.diskRetentionDays() + "d",
                "--web.listen-address=0.0.0.0:" + monitoringConfig.prometheusPort());
        return start(command, runtimeDirectory.resolve("logs/prometheus.log"));
    }

    private Process startGrafana() throws IOException {
        Path home = monitoringConfig.grafanaHome().toAbsolutePath().normalize();
        Path executable = grafanaExecutable(home);
        if (executable == null) {
            throw new IOException("Grafana executable not found under " + home.resolve("bin"));
        }
        List<String> command = new ArrayList<>();
        command.add(executable.toString());
        if (executableBaseName(executable).equals("grafana")) {
            command.add("server");
        }
        command.add("--homepath=" + home);
        command.add("--config=" + runtimeDirectory.resolve("grafana/grafana.ini"));
        return start(command, runtimeDirectory.resolve("logs/grafana.log"));
    }

    private Process start(List<String> command, Path log) throws IOException {
        Files.createDirectories(log.getParent());
        ProcessBuilder builder = new ProcessBuilder(command).redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.appendTo(log.toFile()));
        Process process = builder.start();
        System.out.println("Started managed process " + command.getFirst() + " (pid " + process.pid() + ')');
        return process;
    }

    private void awaitReady(String name, Process process, URI health) throws IOException, InterruptedException {
        long deadline = System.nanoTime() + STARTUP_TIMEOUT.toNanos();
        while (System.nanoTime() < deadline) {
            if (!process.isAlive()) {
                throw new IOException(name + " exited during startup with code " + process.exitValue()
                        + "; check whether port " + health.getPort() + " is already in use");
            }
            try {
                HttpResponse<Void> response = httpClient.send(HttpRequest.newBuilder(health)
                        .timeout(Duration.ofSeconds(2)).GET().build(), HttpResponse.BodyHandlers.discarding());
                if (response.statusCode() >= 200 && response.statusCode() < 300) {
                    Thread.sleep(500);
                    if (!process.isAlive()) {
                        throw new IOException(name + " exited during startup with code " + process.exitValue()
                                + "; check whether port " + health.getPort() + " is already in use");
                    }
                    System.out.println(name + " ready at " + health);
                    return;
                }
            } catch (IOException exception) {
                // The process is still starting; retry until the bounded deadline.
            }
            Thread.sleep(250);
        }
        throw new IOException(name + " did not become ready within " + STARTUP_TIMEOUT.toSeconds() + " seconds");
    }

    private boolean ready(URI health) {
        try {
            HttpResponse<Void> response = httpClient.send(HttpRequest.newBuilder(health)
                    .timeout(Duration.ofSeconds(2)).GET().build(), HttpResponse.BodyHandlers.discarding());
            return response.statusCode() >= 200 && response.statusCode() < 300;
        } catch (IOException exception) {
            return false;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private boolean componentHealthy(Process process, boolean attached, URI health) {
        return attached ? ready(health) : process != null && process.isAlive();
    }

    private void requireAvailableWhenContinuing(String name, int port, boolean continueExisting) throws IOException {
        if (continueExisting && !PortProcessManager.available(port)) {
            throw new IOException("Port " + port + " is occupied but does not expose a healthy " + name
                    + " service; -continue true cannot attach to it");
        }
    }

    private URI prometheusHealth() {
        return URI.create("http://127.0.0.1:" + monitoringConfig.prometheusPort() + "/-/ready");
    }

    private URI grafanaHealth() {
        return URI.create("http://127.0.0.1:" + monitoringConfig.grafanaPort() + "/api/health");
    }

    private void refreshStatuses() {
        try {
            URI uri = URI.create("http://127.0.0.1:" + monitoringConfig.prometheusPort()
                    + "/api/v1/targets?state=active");
            HttpResponse<String> response = httpClient.send(HttpRequest.newBuilder(uri)
                    .timeout(Duration.ofSeconds(4)).GET().build(), HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                throw new IOException("Prometheus returned HTTP " + response.statusCode());
            }
            JsonNode activeTargets = JsonSupport.mapper().readTree(response.body()).path("data").path("activeTargets");
            Map<String, JsonNode> byId = new java.util.HashMap<>();
            activeTargets.forEach(target -> byId.put(target.path("labels").path("sbk_endpoint_id").asText(), target));
            for (BenchmarkTarget target : targets) {
                JsonNode prometheusTarget = byId.get(target.id());
                if (prometheusTarget == null) {
                    statuses.put(target.id(), new TargetStatus("pending", Instant.now().toString(),
                            "Prometheus is discovering the endpoint"));
                } else {
                    String health = prometheusTarget.path("health").asText("unknown");
                    String error = prometheusTarget.path("lastError").asText();
                    String scrape = prometheusTarget.path("lastScrape").asText(Instant.now().toString());
                    statuses.put(target.id(), new TargetStatus(health.equals("up") ? "up" : "down", scrape,
                            error.isBlank() ? "Prometheus target " + health : error));
                }
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        } catch (IOException | RuntimeException exception) {
            System.err.println("WARNING: Unable to refresh Prometheus target health: " + exception.getMessage());
        }
    }

    private void writeMappings() throws IOException {
        List<DashboardMapping> mappings = targets.stream().map(target -> new DashboardMapping(
                target.id(), target.prometheusAddress(), dashboardProvisioner.dashboardUid(target.id()),
                dashboardProvisioner.dashboardUrl(target.id()))).toList();
        AtomicFiles.write(dashboardConfig.dataDirectory().resolve("dashboard-mappings.json"),
                JsonSupport.mapper().writerWithDefaultPrettyPrinter().writeValueAsBytes(mappings));
    }

    private byte[] prometheusConfiguration() {
        String targets = yaml(runtimeDirectory.resolve("prometheus/targets.json"));
        String value = "global:\n  scrape_interval: " + dashboardConfig.scrapeIntervalSeconds() + "s\n"
                + "  evaluation_interval: " + dashboardConfig.scrapeIntervalSeconds() + "s\n"
                + "scrape_configs:\n  - job_name: sbk-dashboard\n"
                + "    fallback_scrape_protocol: PrometheusText0.0.4\n    file_sd_configs:\n"
                + "      - files: ['" + targets + "']\n        refresh_interval: 2s\n"
                + "    relabel_configs:\n      - source_labels: [sbk_metrics_path]\n"
                + "        target_label: __metrics_path__\n      - regex: sbk_metrics_path\n"
                + "        action: labeldrop\n";
        return value.getBytes(StandardCharsets.UTF_8);
    }

    private byte[] datasourceConfiguration() {
        String value = "apiVersion: 1\ndatasources:\n  - name: Prometheus\n"
                + "    uid: " + GrafanaDashboardProvisioner.DATASOURCE_UID + "\n"
                + "    type: prometheus\n    access: proxy\n    url: http://127.0.0.1:"
                + monitoringConfig.prometheusPort() + "\n    isDefault: true\n    editable: false\n";
        return value.getBytes(StandardCharsets.UTF_8);
    }

    private byte[] dashboardProviderConfiguration() {
        String dashboards = yaml(runtimeDirectory.resolve("grafana/dashboards"));
        String value = "apiVersion: 1\nproviders:\n  - name: sbk-dashboard-managed\n"
                + "    orgId: 1\n    type: file\n    disableDeletion: false\n    updateIntervalSeconds: 2\n"
                + "    allowUiUpdates: false\n    options:\n      path: '" + dashboards + "'\n";
        return value.getBytes(StandardCharsets.UTF_8);
    }

    private byte[] grafanaConfiguration() {
        Path directory = runtimeDirectory.resolve("grafana");
        String value = "[paths]\ndata = " + portablePath(directory.resolve("data")) + "\nlogs = "
                + portablePath(directory.resolve("logs")) + "\nplugins = "
                + portablePath(directory.resolve("data/plugins")) + "\nprovisioning = "
                + portablePath(directory.resolve("provisioning"))
                + "\n\n[server]\nhttp_addr = 0.0.0.0\nhttp_port = "
                + monitoringConfig.grafanaPort() + "\n\n[auth]\ndisable_login_form = true\n\n"
                + "[auth.anonymous]\nenabled = true\norg_name = Main Org.\norg_role = Viewer\n\n"
                + "[users]\ndefault_theme = dark\n\n[dashboards]\nmin_refresh_interval = 1s\n";
        return value.getBytes(StandardCharsets.UTF_8);
    }

    private Path resolveExecutable(Path configured, String name) throws IOException {
        if (configured.isAbsolute() || configured.getNameCount() > 1) {
            Path normalized = configured.toAbsolutePath().normalize();
            if (!executable(normalized)) {
                throw new IOException(name + " executable is not available: " + normalized);
            }
            return normalized;
        }
        for (String directory : System.getenv().getOrDefault("PATH", "").split(java.io.File.pathSeparator)) {
            for (String executableName : executableNames(configured.toString())) {
                Path candidate = Path.of(directory).resolve(executableName);
                if (executable(candidate)) {
                    return candidate.toAbsolutePath().normalize();
                }
            }
        }
        throw new IOException(name + " executable was not found on PATH: " + configured);
    }

    private String yaml(Path path) {
        return portablePath(path).replace("'", "''");
    }

    private Path grafanaExecutable(Path home) {
        for (String base : new String[] {"grafana", "grafana-server"}) {
            for (String name : executableNames(base)) {
                Path candidate = home.resolve("bin").resolve(name);
                if (executable(candidate)) {
                    return candidate;
                }
            }
        }
        return null;
    }

    private List<String> executableNames(String name) {
        return platform.windows() && !name.toLowerCase(Locale.ROOT).endsWith(".exe")
                ? List.of(name + ".exe", name) : List.of(name);
    }

    private boolean executable(Path path) {
        return platform.windows() ? Files.isRegularFile(path) : Files.isExecutable(path);
    }

    private String executableBaseName(Path executable) {
        String name = executable.getFileName().toString().toLowerCase(Locale.ROOT);
        return name.endsWith(".exe") ? name.substring(0, name.length() - 4) : name;
    }

    private String portablePath(Path path) {
        return path.toAbsolutePath().normalize().toString().replace('\\', '/');
    }

    private void stop(Process process) {
        if (process == null || !process.isAlive()) {
            return;
        }
        process.destroy();
        try {
            if (!process.waitFor(5, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                process.waitFor(5, TimeUnit.SECONDS);
            }
        } catch (InterruptedException exception) {
            process.destroyForcibly();
            Thread.currentThread().interrupt();
        }
    }

    private void removeOwnership(String component, Process process) {
        if (process == null) {
            return;
        }
        try {
            processRegistry.remove(component, process.pid());
        } catch (IOException exception) {
            System.err.println("WARNING: Unable to update managed process ownership: " + exception.getMessage());
        }
    }

    private record DashboardMapping(String targetId, String prometheusTarget,
                                    String grafanaDashboardUid, String grafanaDashboardUrl) { }
}
