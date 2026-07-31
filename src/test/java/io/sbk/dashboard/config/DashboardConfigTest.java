/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Tests command-line, environment, and default configuration precedence. */
class DashboardConfigTest {
    /** Command-line values have the highest precedence. */
    @Test
    void commandOptionsOverrideEnvironment() {
        DashboardConfig config = DashboardConfig.fromSources(9721, false, "/cli/data", "30",
                Map.of("SBK_DASHBOARD_DATA_DIR", "/environment/data",
                        "SBK_DASHBOARD_DISK_RETENTION_DAYS", "14"));

        assertEquals(Path.of("/cli/data"), config.dataDirectory());
        assertEquals(30, config.diskRetentionDays());
    }

    /** Environment values are selected when command options are absent. */
    @Test
    void environmentOverridesDefaults() {
        DashboardConfig config = DashboardConfig.fromSources(9721, false, null, null,
                Map.of("SBK_DASHBOARD_DATA_DIR", "/environment/data",
                        "SBK_DASHBOARD_DISK_RETENTION_DAYS", "14"));

        assertEquals(Path.of("/environment/data"), config.dataDirectory());
        assertEquals(14, config.diskRetentionDays());
    }

    /** Built-in values are selected when neither command nor environment supplies a value. */
    @Test
    void usesBuiltInDefaults() {
        DashboardConfig config = DashboardConfig.fromSources(9721, false, null, null, Map.of());

        assertEquals(Path.of(System.getProperty("user.home"), ".sbk-dashboard"), config.dataDirectory());
        assertEquals(7, config.diskRetentionDays());
    }

    /** Invalid command retention is rejected instead of silently using another source. */
    @Test
    void rejectsInvalidCommandRetention() {
        assertThrows(IllegalArgumentException.class,
                () -> DashboardConfig.fromSources(9721, false, "/data", "0",
                        Map.of("SBK_DASHBOARD_DISK_RETENTION_DAYS", "14")));
    }
}
