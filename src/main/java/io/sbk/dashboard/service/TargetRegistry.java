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

import com.fasterxml.jackson.core.type.TypeReference;
import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.model.BenchmarkKind;
import io.sbk.dashboard.model.BenchmarkTarget;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Persistent, thread-safe endpoint registry. */
public final class TargetRegistry {
    private static final TypeReference<List<BenchmarkTarget>> TARGET_LIST = new TypeReference<>() { };
    private final DashboardConfig config;
    private volatile Map<String, BenchmarkTarget> targets = Map.of();

    /**
     * Opens the persisted registry.
     *
     * @param config application configuration
     * @throws IOException when persisted state cannot be loaded
     */
    public TargetRegistry(DashboardConfig config) throws IOException {
        this.config = config;
        load();
    }

    /**
     * Returns a stable snapshot sorted by display name.
     *
     * @return registered endpoints
     */
    public List<BenchmarkTarget> list() {
        return targets.values().stream()
                .sorted(Comparator.comparing(BenchmarkTarget::name, String.CASE_INSENSITIVE_ORDER))
                .toList();
    }

    /**
     * Looks up an endpoint.
     *
     * @param id endpoint identifier
     * @return target, or {@code null} when absent
     */
    public BenchmarkTarget find(String id) {
        return targets.get(id);
    }

    /**
     * Registers a unique host and port pair.
     *
     * @param name user-facing name
     * @param host DNS name, IPv4 address, or IPv6 address
     * @param port Prometheus exporter port
     * @param metricsPath HTTP metrics path
     * @param kind SBK or SBM
     * @return new endpoint
     * @throws IOException when state cannot be persisted
     */
    public synchronized BenchmarkTarget register(String name, String host, int port, String metricsPath,
                                                  BenchmarkKind kind) throws IOException {
        String normalizedHost = validateHost(host);
        int normalizedPort = validatePort(port);
        String normalizedPath = validatePath(metricsPath);
        String id = identifier(normalizedHost, normalizedPort);
        if (targets.containsKey(id)) {
            throw new IllegalStateException("The endpoint " + normalizedHost + ':' + normalizedPort
                    + " is already registered");
        }
        String normalizedName = name == null || name.isBlank()
                ? normalizedHost + ':' + normalizedPort : name.trim();
        if (normalizedName.length() > 100) {
            throw new IllegalArgumentException("Name must not exceed 100 characters");
        }
        BenchmarkTarget target = new BenchmarkTarget(id, normalizedName, normalizedHost, normalizedPort,
                normalizedPath, kind == null ? BenchmarkKind.SBK : kind, Instant.now().toString());
        LinkedHashMap<String, BenchmarkTarget> next = new LinkedHashMap<>(targets);
        next.put(id, target);
        persist(next.values());
        targets = Map.copyOf(next);
        return target;
    }

    /**
     * Removes an endpoint.
     *
     * @param id endpoint identifier
     * @return whether an endpoint was removed
     * @throws IOException when state cannot be updated
     */
    public synchronized boolean remove(String id) throws IOException {
        if (!targets.containsKey(id)) {
            return false;
        }
        LinkedHashMap<String, BenchmarkTarget> next = new LinkedHashMap<>(targets);
        next.remove(id);
        persist(next.values());
        targets = Map.copyOf(next);
        return true;
    }

    private void load() throws IOException {
        Files.createDirectories(config.dataDirectory());
        java.nio.file.Path registryFile = config.dataDirectory().resolve("targets.json");
        if (!Files.exists(registryFile)) {
            persist(List.of());
            return;
        }
        List<BenchmarkTarget> loaded = JsonSupport.mapper().readValue(registryFile.toFile(), TARGET_LIST);
        LinkedHashMap<String, BenchmarkTarget> indexed = new LinkedHashMap<>();
        for (BenchmarkTarget target : loaded) {
            if (indexed.put(target.id(), target) != null) {
                throw new IOException("Duplicate target identifier in " + registryFile + ": " + target.id());
            }
        }
        targets = Map.copyOf(indexed);
    }

    private void persist(Iterable<BenchmarkTarget> values) throws IOException {
        List<BenchmarkTarget> snapshot = new ArrayList<>();
        values.forEach(snapshot::add);
        byte[] json = JsonSupport.mapper().writerWithDefaultPrettyPrinter().writeValueAsBytes(snapshot);
        AtomicFiles.write(config.dataDirectory().resolve("targets.json"), json);
    }

    private String validateHost(String host) {
        if (host == null || host.isBlank()) {
            throw new IllegalArgumentException("Host is required");
        }
        String value = host.trim();
        if (value.startsWith("[") && value.endsWith("]")) {
            value = value.substring(1, value.length() - 1);
        }
        if (value.length() > 253 || !value.matches("[A-Za-z0-9._:%-]+") || value.contains("..")) {
            throw new IllegalArgumentException("Host must be a DNS name, IPv4 address, or IPv6 address");
        }
        return value.toLowerCase(java.util.Locale.ROOT);
    }

    private int validatePort(int port) {
        if (port < 1 || port > 65_535) {
            throw new IllegalArgumentException("Port must be between 1 and 65535");
        }
        return port;
    }

    private String validatePath(String metricsPath) {
        String value = metricsPath == null || metricsPath.isBlank() ? "/metrics" : metricsPath.trim();
        if (!value.startsWith("/") || value.contains("?") || value.contains("#") || value.contains(" ")) {
            throw new IllegalArgumentException("Metrics path must be an absolute HTTP path");
        }
        return value;
    }

    private String identifier(String host, int port) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256")
                    .digest((host + ':' + port).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash, 0, 8);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
