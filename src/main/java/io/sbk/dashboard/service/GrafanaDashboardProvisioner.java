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
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.sbk.dashboard.model.BenchmarkTarget;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Generates endpoint-scoped clones of the canonical SBK Grafana dashboard. */
public final class GrafanaDashboardProvisioner {
    /** UID used by the provisioned Prometheus datasource. */
    public static final String DATASOURCE_UID = "PBFA97CFB590B2093";
    private static final Pattern SBK_SELECTOR = Pattern.compile("(SBK_[A-Za-z0-9_]+)(?:\\{([^}]*)\\})?");
    private final JsonNode canonicalDashboard;
    private final Path dashboardDirectory;
    private final URI grafanaPublicUrl;

    /**
     * Creates a dashboard generator.
     *
     * @param dashboardDirectory Grafana-provisioned dashboard directory
     * @param grafanaPublicUrl externally accessible Grafana base URL
     * @throws IOException when the canonical dashboard cannot be loaded
     */
    public GrafanaDashboardProvisioner(Path dashboardDirectory, URI grafanaPublicUrl) throws IOException {
        this.dashboardDirectory = dashboardDirectory;
        this.grafanaPublicUrl = grafanaPublicUrl;
        try (InputStream input = GrafanaDashboardProvisioner.class.getResourceAsStream(
                "/grafana/dashboards/sbk-dashboard.json")) {
            if (input == null) {
                throw new IOException("Canonical SBK Grafana dashboard is missing");
            }
            canonicalDashboard = JsonSupport.mapper().readTree(input);
        }
    }

    /**
     * Writes or updates every endpoint dashboard and removes orphan files.
     *
     * @param targets registered endpoints
     * @throws IOException when provisioning files cannot be reconciled
     */
    public synchronized void reconcile(List<BenchmarkTarget> targets) throws IOException {
        Files.createDirectories(dashboardDirectory);
        Set<Path> expected = new HashSet<>();
        for (BenchmarkTarget target : targets) {
            Path path = dashboardPath(target.id());
            expected.add(path);
            AtomicFiles.write(path, generatedDashboard(target));
        }
        try (var files = Files.list(dashboardDirectory)) {
            for (Path path : files.filter(item -> item.getFileName().toString().startsWith("sbk-")
                    && item.getFileName().toString().endsWith(".json")).toList()) {
                if (!expected.contains(path)) {
                    Files.deleteIfExists(path);
                }
            }
        }
    }

    /** Returns the deterministic Grafana dashboard UID for an endpoint. */
    public String dashboardUid(String targetId) {
        return "sbk-" + targetId;
    }

    /** Returns the dedicated public Grafana URL for an endpoint. */
    public String dashboardUrl(String targetId) {
        String base = grafanaPublicUrl.toString().replaceAll("/+$", "");
        return base + "/d/" + dashboardUid(targetId) + "/";
    }

    byte[] generatedDashboard(BenchmarkTarget target) throws IOException {
        ObjectNode dashboard = canonicalDashboard.deepCopy();
        dashboard.putNull("id");
        dashboard.put("uid", dashboardUid(target.id()));
        dashboard.put("title", "SBK Dashboard — " + target.host() + ':' + target.port());
        dashboard.put("version", 1);
        ArrayNode tags = dashboard.withArray("tags");
        tags.add("sbk-dashboard-managed");
        tags.add("endpoint:" + target.id());
        scopeExpressions(dashboard, target.id());
        return JsonSupport.mapper().writerWithDefaultPrettyPrinter().writeValueAsBytes(dashboard);
    }

    private Path dashboardPath(String targetId) {
        return dashboardDirectory.resolve(dashboardUid(targetId) + ".json");
    }

    private void scopeExpressions(JsonNode node, String targetId) {
        if (node.isObject()) {
            ObjectNode object = (ObjectNode) node;
            JsonNode expression = object.get("expr");
            if (expression != null && expression.isTextual()) {
                object.put("expr", scopePromQl(expression.asText(), targetId));
            }
            Iterator<Map.Entry<String, JsonNode>> fields = object.properties().iterator();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> field = fields.next();
                if (!field.getKey().equals("expr")) {
                    scopeExpressions(field.getValue(), targetId);
                }
            }
        } else if (node.isArray()) {
            node.forEach(child -> scopeExpressions(child, targetId));
        }
    }

    private String scopePromQl(String expression, String targetId) {
        Matcher matcher = SBK_SELECTOR.matcher(expression);
        StringBuilder scoped = new StringBuilder();
        while (matcher.find()) {
            String labels = matcher.group(2);
            String selector = labels == null || labels.isBlank()
                    ? matcher.group(1) + "{sbk_endpoint_id=\"" + targetId + "\"}"
                    : matcher.group(1) + '{' + labels + ",sbk_endpoint_id=\"" + targetId + "\"}";
            matcher.appendReplacement(scoped, Matcher.quoteReplacement(selector));
        }
        matcher.appendTail(scoped);
        return scoped.toString();
    }
}
