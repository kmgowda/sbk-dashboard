/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.config.MonitoringConfig;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests attachment and safe replacement behavior for occupied monitoring ports. */
class ManagedMonitoringStackTest {
    @TempDir
    private Path temporaryDirectory;

    /** Continue mode attaches to healthy services and never stops processes it does not own. */
    @Test
    void continuesHealthyExistingServicesWithoutStoppingThem() throws Exception {
        HttpServer prometheus = server();
        HttpServer grafana = server();
        prometheus.createContext("/-/ready", exchange -> respond(exchange, "Prometheus Ready"));
        prometheus.createContext("/api/v1/targets", exchange -> respond(exchange,
                "{\"status\":\"success\",\"data\":{\"activeTargets\":[]}}"));
        grafana.createContext("/api/health", exchange -> respond(exchange, "{\"database\":\"ok\"}"));
        prometheus.start();
        grafana.start();
        ManagedMonitoringStack stack = null;
        try {
            stack = new ManagedMonitoringStack(dashboardConfig(), monitoringConfig(prometheus, grafana),
                    List.of(), true);
            assertTrue(stack.healthy());
            stack.close();
            stack = null;

            assertEquals(200, get("http://127.0.0.1:" + prometheus.getAddress().getPort() + "/-/ready"));
            assertEquals(200, get("http://127.0.0.1:" + grafana.getAddress().getPort() + "/api/health"));
        } finally {
            if (stack != null) {
                stack.close();
            }
            prometheus.stop(0);
            grafana.stop(0);
        }
    }

    /** Replacement mode refuses to terminate an unrelated process that owns a requested port. */
    @Test
    void refusesToStopUnrelatedListeners() throws Exception {
        HttpServer prometheus = server();
        HttpServer grafana = server();
        prometheus.start();
        grafana.start();
        try {
            IOException failure = assertThrows(IOException.class, () -> new ManagedMonitoringStack(
                    dashboardConfig(), monitoringConfig(prometheus, grafana), List.of(), false));
            assertTrue(failure.getMessage().contains("unrelated process"));
        } finally {
            prometheus.stop(0);
            grafana.stop(0);
        }
    }

    private HttpServer server() throws IOException {
        return HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    }

    private DashboardConfig dashboardConfig() {
        return new DashboardConfig(9721, false, temporaryDirectory, 5, 7);
    }

    private MonitoringConfig monitoringConfig(HttpServer prometheus, HttpServer grafana) {
        int grafanaPort = grafana.getAddress().getPort();
        return new MonitoringConfig(Path.of("prometheus"), temporaryDirectory.resolve("grafana"),
                prometheus.getAddress().getPort(), grafanaPort, URI.create("http://localhost:" + grafanaPort));
    }

    private int get(String uri) throws Exception {
        return HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create(uri)).GET().build(),
                HttpResponse.BodyHandlers.discarding()).statusCode();
    }

    private void respond(HttpExchange exchange, String value) throws IOException {
        byte[] body = value.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(200, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }
}
