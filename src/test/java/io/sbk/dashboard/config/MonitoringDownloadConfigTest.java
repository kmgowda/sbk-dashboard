/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests packaged and external native download properties. */
class MonitoringDownloadConfigTest {
    @TempDir
    private Path temporaryDirectory;

    /** Packaged defaults resolve their directories relative to dashboard data. */
    @Test
    void resolvesPackagedDirectoriesAndPinnedArchives() throws Exception {
        MonitoringDownloadConfig config = MonitoringDownloadConfig.fromSources(null,
                temporaryDirectory.resolve("data"), Map.of());

        assertEquals(temporaryDirectory.resolve("data/downloads"), config.downloadDirectory());
        assertEquals(temporaryDirectory.resolve("data/tools"), config.installDirectory());
        assertEquals("prometheus-3.10.0.linux-amd64.tar.gz", config.prometheus().fileName());
        assertEquals("grafana-12.4.1", config.grafana().archiveDirectory().toString());
        assertTrue(config.prometheus().url().toString().startsWith("https://"));
    }

    /** An explicit properties file overrides packaged directory values. */
    @Test
    void externalPropertiesOverridePackagedDefaults() throws Exception {
        Path properties = temporaryDirectory.resolve("custom.properties");
        Files.writeString(properties, "download.directory=${data.directory}/archives\n"
                + "install.directory=${data.directory}/native\n");

        MonitoringDownloadConfig config = MonitoringDownloadConfig.fromSources(properties.toString(),
                temporaryDirectory.resolve("data"), Map.of());

        assertEquals(temporaryDirectory.resolve("data/archives"), config.downloadDirectory());
        assertEquals(temporaryDirectory.resolve("data/native"), config.installDirectory());
        assertEquals(properties.toAbsolutePath().toString(), config.source());
    }

    /** Missing explicit property files fail before any network operation. */
    @Test
    void rejectsMissingExternalFile() {
        assertThrows(IllegalArgumentException.class, () -> MonitoringDownloadConfig.fromSources(
                temporaryDirectory.resolve("missing.properties").toString(), temporaryDirectory, Map.of()));
    }
}
