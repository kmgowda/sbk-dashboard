/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.net.URI;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Tests managed monitoring option precedence and validation. */
class MonitoringConfigTest {
    /** Command-line values override environment values. */
    @Test
    void commandOptionsHaveHighestPrecedence() {
        MonitoringConfig config = MonitoringConfig.fromSources("/cli/prometheus", "/cli/grafana",
                "19090", "13000", "https://dash.example/grafana", Map.of(
                        "SBK_DASHBOARD_PROMETHEUS_BIN", "/env/prometheus",
                        "SBK_DASHBOARD_GRAFANA_HOME", "/env/grafana",
                        "SBK_DASHBOARD_PROMETHEUS_PORT", "9091",
                        "SBK_DASHBOARD_GRAFANA_PORT", "3001",
                        "SBK_DASHBOARD_GRAFANA_URL", "http://env.example:3001"));

        assertEquals(Path.of("/cli/prometheus"), config.prometheusBinary());
        assertEquals(Path.of("/cli/grafana"), config.grafanaHome());
        assertEquals(19090, config.prometheusPort());
        assertEquals(13000, config.grafanaPort());
        assertEquals(URI.create("https://dash.example/grafana"), config.grafanaPublicUrl());
    }

    /** Defaults produce the conventional native server locations and ports. */
    @Test
    void usesBuiltInDefaults() {
        MonitoringConfig config = MonitoringConfig.fromSources(null, null, null, null, null, Map.of());

        assertEquals(Path.of("prometheus"), config.prometheusBinary());
        assertEquals(Path.of("/usr/share/grafana"), config.grafanaHome());
        assertEquals(9090, config.prometheusPort());
        assertEquals(3000, config.grafanaPort());
        assertEquals(URI.create("http://localhost:3000"), config.grafanaPublicUrl());
    }

    /** Only valid HTTP ports and public HTTP(S) URLs are accepted. */
    @Test
    void rejectsInvalidPortsAndUrls() {
        assertThrows(IllegalArgumentException.class,
                () -> MonitoringConfig.fromSources(null, null, "0", null, null, Map.of()));
        assertThrows(IllegalArgumentException.class,
                () -> MonitoringConfig.fromSources(null, null, null, null, "file:///tmp/grafana", Map.of()));
    }
}
