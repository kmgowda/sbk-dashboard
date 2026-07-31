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

package io.sbk.dashboard.web;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.model.BenchmarkTarget;
import io.sbk.dashboard.model.EndpointSnapshot;
import io.sbk.dashboard.model.TargetStatus;
import io.sbk.dashboard.service.JsonSupport;
import io.sbk.dashboard.service.PrometheusScrapeService;
import io.sbk.dashboard.service.TargetRegistry;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** JDK HTTP server providing the dashboard UI and endpoint registry API. */
public final class DashboardHttpServer implements AutoCloseable {
    private static final int MAX_REQUEST_BYTES = 64 * 1024;
    private final DashboardConfig config;
    private final TargetRegistry registry;
    private final PrometheusScrapeService scraper;
    private final HttpServer server;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    /**
     * Creates, but does not start, the HTTP server.
     *
     * @param config runtime configuration
     * @param registry target registry
     * @param scraper embedded metrics collection engine
     * @throws IOException when the listening socket cannot be created
     */
    public DashboardHttpServer(DashboardConfig config, TargetRegistry registry,
                               PrometheusScrapeService scraper) throws IOException {
        this.config = config;
        this.registry = registry;
        this.scraper = scraper;
        this.server = HttpServer.create(new InetSocketAddress(config.port()), 1024);
        server.setExecutor(executor);
        server.createContext("/", this::handle);
    }

    /** Starts accepting requests. */
    public void start() {
        server.start();
    }

    /** Stops accepting requests and closes worker threads. */
    @Override
    public void close() {
        server.stop(1);
        executor.shutdownNow();
    }

    private void handle(HttpExchange exchange) throws IOException {
        try {
            String path = exchange.getRequestURI().getPath();
            if (path.equals("/api/health")) {
                requireMethod(exchange, "GET");
                json(exchange, 200, Map.of("status", "ok", "authentication", false,
                        "targets", registry.list().size()));
            } else if (path.equals("/api/targets")) {
                handleTargets(exchange);
            } else if (path.startsWith("/api/targets/")) {
                handleTarget(exchange, path.substring("/api/targets/".length()));
            } else {
                handleAsset(exchange, path);
            }
        } catch (MethodNotAllowedException exception) {
            json(exchange, 405, Map.of("error", exception.getMessage()));
        } catch (IllegalArgumentException | IllegalStateException exception) {
            json(exchange, 400, Map.of("error", exception.getMessage()));
        } catch (JsonProcessingException exception) {
            json(exchange, 400, Map.of("error", "Request body is not valid JSON"));
        } catch (IOException exception) {
            json(exchange, 500, Map.of("error", "Unable to update dashboard state"));
            System.err.println("Request failed: " + exception.getMessage());
        } catch (RuntimeException exception) {
            json(exchange, 500, Map.of("error", "Unexpected server error"));
            System.err.println("Request failed: " + exception.getMessage());
        } finally {
            exchange.close();
        }
    }

    private void handleTargets(HttpExchange exchange) throws IOException {
        if (exchange.getRequestMethod().equals("GET")) {
            List<TargetView> views = registry.list().stream().map(this::view).toList();
            json(exchange, 200, views);
            return;
        }
        requireMethod(exchange, "POST");
        CreateTargetRequest request = readJson(exchange, CreateTargetRequest.class);
        BenchmarkTarget target = registry.register(request.name(), request.host(), request.port(),
                request.metricsPath());
        scraper.register(target);
        json(exchange, 201, view(target));
    }

    private void handleTarget(HttpExchange exchange, String encodedId) throws IOException {
        int slash = encodedId.indexOf('/');
        String encodedIdentifier = slash < 0 ? encodedId : encodedId.substring(0, slash);
        String action = slash < 0 ? "" : encodedId.substring(slash + 1);
        String id = URLDecoder.decode(encodedIdentifier, StandardCharsets.UTF_8);
        if (id.contains("/") || registry.find(id) == null) {
            json(exchange, 404, Map.of("error", "Target not found"));
            return;
        }
        if (action.equals("dashboard")) {
            requireMethod(exchange, "GET");
            int points = queryPoints(exchange.getRequestURI().getRawQuery());
            EndpointSnapshot snapshot = scraper.snapshot(id, points);
            json(exchange, 200, snapshot);
            return;
        }
        if (!action.isEmpty()) {
            json(exchange, 404, Map.of("error", "Not found"));
            return;
        }
        requireMethod(exchange, "DELETE");
        if (!registry.remove(id)) {
            json(exchange, 404, Map.of("error", "Target not found"));
            return;
        }
        scraper.unregister(id);
        exchange.sendResponseHeaders(204, -1);
    }

    private void handleAsset(HttpExchange exchange, String path) throws IOException {
        requireMethod(exchange, "GET");
        String resource = switch (path) {
            case "/", "/index.html" -> "/web/index.html";
            case "/dashboard.html" -> "/web/dashboard.html";
            case "/app.css" -> "/web/app.css";
            case "/app.js" -> "/web/app.js";
            case "/dashboard.js" -> "/web/dashboard.js";
            default -> null;
        };
        if (resource == null) {
            json(exchange, 404, Map.of("error", "Not found"));
            return;
        }
        try (InputStream input = DashboardHttpServer.class.getResourceAsStream(resource)) {
            if (input == null) {
                json(exchange, 500, Map.of("error", "Missing application asset"));
                return;
            }
            byte[] body = input.readAllBytes();
            exchange.getResponseHeaders().set("Content-Type", contentType(resource));
            exchange.getResponseHeaders().set("Cache-Control", resource.endsWith("index.html")
                    ? "no-cache" : "public, max-age=3600");
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
        }
    }

    private TargetView view(BenchmarkTarget target) {
        TargetStatus status = scraper.status(target.id());
        String dashboardUrl = "/dashboard.html?id=" + target.id();
        return new TargetView(target.id(), target.name(), target.host(), target.port(), target.metricsPath(),
                target.createdAt(), status, dashboardUrl);
    }

    private <T> T readJson(HttpExchange exchange, Class<T> type) throws IOException {
        byte[] body = exchange.getRequestBody().readNBytes(MAX_REQUEST_BYTES + 1);
        if (body.length > MAX_REQUEST_BYTES) {
            throw new IllegalArgumentException("Request body exceeds 64 KiB");
        }
        return JsonSupport.mapper().readValue(body, type);
    }

    private void json(HttpExchange exchange, int status, Object value) throws IOException {
        if (exchange.getResponseCode() != -1) {
            return;
        }
        byte[] body = JsonSupport.mapper().writeValueAsBytes(value);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
    }

    private void requireMethod(HttpExchange exchange, String expected) {
        if (!exchange.getRequestMethod().equals(expected)) {
            exchange.getResponseHeaders().set("Allow", expected);
            throw new MethodNotAllowedException("Use " + expected + " for this endpoint");
        }
    }

    private String contentType(String resource) {
        if (resource.endsWith(".css")) {
            return "text/css; charset=utf-8";
        }
        if (resource.endsWith(".js")) {
            return "text/javascript; charset=utf-8";
        }
        return "text/html; charset=utf-8";
    }

    private int queryPoints(String query) {
        if (query == null || query.isBlank()) {
            return 240;
        }
        for (String parameter : query.split("&")) {
            String[] pair = parameter.split("=", 2);
            if (pair.length == 2 && pair[0].equals("points")) {
                try {
                    return Math.max(10, Math.min(1000, Integer.parseInt(pair[1])));
                } catch (NumberFormatException exception) {
                    throw new IllegalArgumentException("points must be a number", exception);
                }
            }
        }
        return 240;
    }

    private record CreateTargetRequest(String name, String host, int port, String metricsPath) { }

    private record TargetView(String id, String name, String host, int port, String metricsPath,
                              String createdAt, TargetStatus status, String dashboardUrl) { }

    private static final class MethodNotAllowedException extends RuntimeException {
        private MethodNotAllowedException(String message) {
            super(message);
        }
    }
}
