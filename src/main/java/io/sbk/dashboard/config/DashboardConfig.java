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

import java.nio.file.Path;
import java.util.Map;

/** Runtime paths and public service locations. */
public record DashboardConfig(int port, boolean authenticationEnabled, Path dataDirectory,
                              int scrapeIntervalSeconds, int diskRetentionDays) {
    /** Default dashboard HTTP port. */
    public static final int DEFAULT_PORT = 9721;
    /** Default persistent history retention per endpoint. */
    public static final int DEFAULT_DISK_RETENTION_DAYS = 7;

    /**
     * Creates configuration from CLI values and environment overrides.
     *
     * @param port dashboard HTTP port
     * @param authenticationEnabled requested authentication state
     * @return runtime configuration
     */
    public static DashboardConfig fromEnvironment(int port, boolean authenticationEnabled) {
        return fromOptions(port, authenticationEnabled, null, null);
    }

    /**
     * Creates configuration with command-line values taking precedence over environment values.
     *
     * @param port dashboard HTTP port
     * @param authenticationEnabled requested authentication state
     * @param dataDirectory command-line data directory, or null
     * @param diskRetentionDays command-line retention days, or null
     * @return runtime configuration
     */
    public static DashboardConfig fromOptions(int port, boolean authenticationEnabled,
                                              String dataDirectory, String diskRetentionDays) {
        return fromSources(port, authenticationEnabled, dataDirectory, diskRetentionDays, System.getenv());
    }

    static DashboardConfig fromSources(int port, boolean authenticationEnabled,
                                       String dataDirectory, String diskRetentionDays,
                                       Map<String, String> environment) {
        String defaultData = Path.of(System.getProperty("user.home"), ".sbk-dashboard").toString();
        String selectedData = commandOrEnvironment(dataDirectory, "-data", environment,
                "SBK_DASHBOARD_DATA_DIR", defaultData);
        Path data = Path.of(selectedData).toAbsolutePath().normalize();
        int scrapeInterval = positiveInteger(environment, "SBK_DASHBOARD_SCRAPE_SECONDS", 5);
        String selectedDiskRetention = commandOrEnvironment(diskRetentionDays, "-retention", environment,
                "SBK_DASHBOARD_DISK_RETENTION_DAYS", Integer.toString(DEFAULT_DISK_RETENTION_DAYS));
        String retentionSource = diskRetentionDays == null
                ? "SBK_DASHBOARD_DISK_RETENTION_DAYS" : "-retention";
        int diskRetention = positiveInteger(retentionSource, selectedDiskRetention);
        return new DashboardConfig(port, authenticationEnabled, data, scrapeInterval, diskRetention);
    }

    private static String environment(Map<String, String> environment, String name, String defaultValue) {
        String value = environment.get(name);
        return value == null || value.isBlank() ? defaultValue : value.trim();
    }

    private static String commandOrEnvironment(String commandValue, String commandName,
                                               Map<String, String> environment, String environmentName,
                                               String defaultValue) {
        if (commandValue == null) {
            return environment(environment, environmentName, defaultValue);
        }
        if (commandValue.isBlank()) {
            throw new IllegalArgumentException(commandName + " must not be blank");
        }
        return commandValue.trim();
    }

    private static int positiveInteger(Map<String, String> environment, String name, int defaultValue) {
        return positiveInteger(name, environment(environment, name, Integer.toString(defaultValue)));
    }

    private static int positiveInteger(String name, String value) {
        try {
            int parsed = Integer.parseInt(value);
            if (parsed < 1) {
                throw new IllegalArgumentException(name + " must be positive");
            }
            return parsed;
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException(name + " must be a number", exception);
        }
    }
}
