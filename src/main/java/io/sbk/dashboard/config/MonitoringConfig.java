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

package io.sbk.dashboard.config;

import java.net.URI;
import java.nio.file.Path;
import java.util.Map;

/** Native Prometheus and Grafana process configuration. */
public record MonitoringConfig(Path prometheusBinary, Path grafanaHome, int prometheusPort,
                               int grafanaPort, URI grafanaPublicUrl) {
    /** Default Prometheus HTTP port. */
    public static final int DEFAULT_PROMETHEUS_PORT = 9090;
    /** Default Grafana HTTP port. */
    public static final int DEFAULT_GRAFANA_PORT = 3000;

    /**
     * Resolves command-line values, environment fallbacks, and defaults.
     *
     * @param prometheusBinary command-line Prometheus binary
     * @param grafanaHome command-line Grafana installation directory
     * @param prometheusPort command-line Prometheus port
     * @param grafanaPort command-line Grafana port
     * @param grafanaPublicUrl externally usable Grafana base URL
     * @return monitoring configuration
     */
    public static MonitoringConfig fromOptions(String prometheusBinary, String grafanaHome,
                                               String prometheusPort, String grafanaPort,
                                               String grafanaPublicUrl) {
        return fromSources(prometheusBinary, grafanaHome, prometheusPort, grafanaPort,
                grafanaPublicUrl, System.getenv());
    }

    static MonitoringConfig fromSources(String prometheusBinary, String grafanaHome,
                                        String prometheusPort, String grafanaPort,
                                        String grafanaPublicUrl, Map<String, String> environment) {
        String selectedPrometheus = select(prometheusBinary, environment,
                "SBK_DASHBOARD_PROMETHEUS_BIN", "prometheus");
        String selectedGrafana = select(grafanaHome, environment,
                "SBK_DASHBOARD_GRAFANA_HOME", "/usr/share/grafana");
        int selectedPrometheusPort = port(select(prometheusPort, environment,
                "SBK_DASHBOARD_PROMETHEUS_PORT", Integer.toString(DEFAULT_PROMETHEUS_PORT)),
                "prometheus port");
        int selectedGrafanaPort = port(select(grafanaPort, environment,
                "SBK_DASHBOARD_GRAFANA_PORT", Integer.toString(DEFAULT_GRAFANA_PORT)), "grafana port");
        String selectedPublicUrl = select(grafanaPublicUrl, environment,
                "SBK_DASHBOARD_GRAFANA_URL", "http://localhost:" + selectedGrafanaPort);
        URI publicUrl = URI.create(selectedPublicUrl);
        if (publicUrl.getHost() == null || !(publicUrl.getScheme().equalsIgnoreCase("http")
                || publicUrl.getScheme().equalsIgnoreCase("https"))) {
            throw new IllegalArgumentException("Grafana URL must be an absolute HTTP or HTTPS URL");
        }
        return new MonitoringConfig(Path.of(selectedPrometheus), Path.of(selectedGrafana),
                selectedPrometheusPort, selectedGrafanaPort, publicUrl);
    }

    private static String select(String option, Map<String, String> environment,
                                 String environmentName, String defaultValue) {
        if (option != null) {
            if (option.isBlank()) {
                throw new IllegalArgumentException("Configuration option must not be blank");
            }
            return option.trim();
        }
        String value = environment.get(environmentName);
        return value == null || value.isBlank() ? defaultValue : value.trim();
    }

    private static int port(String value, String name) {
        try {
            int parsed = Integer.parseInt(value);
            if (parsed < 1 || parsed > 65_535) {
                throw new IllegalArgumentException(name + " must be between 1 and 65535");
            }
            return parsed;
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException(name + " must be a number", exception);
        }
    }
}
