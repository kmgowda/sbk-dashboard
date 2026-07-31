/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.sbk.dashboard.config.MonitoringDownloadConfig.ArchiveFormat;
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

    /** Every supported runtime selects a complete pinned native tool pair. */
    @Test
    void selectsArchivesForEverySupportedPlatform() throws Exception {
        for (String[] value : new String[][] {
            {"Linux", "amd64", "tar.gz", "prometheus"},
            {"Linux", "aarch64", "tar.gz", "prometheus"},
            {"Mac OS X", "x86_64", "tar.gz", "prometheus"},
            {"Mac OS X", "aarch64", "tar.gz", "prometheus"},
            {"Windows 11", "amd64", "zip", "prometheus.exe"},
            {"Windows 11", "aarch64", "zip", "prometheus.exe"}
        }) {
            RuntimePlatform platform = RuntimePlatform.from(value[0], value[1]);
            MonitoringDownloadConfig config = MonitoringDownloadConfig.fromSources(null,
                    temporaryDirectory.resolve(platform.id()), Map.of(), platform);
            assertEquals(value[2].equals("zip") ? ArchiveFormat.ZIP : ArchiveFormat.TAR_GZ,
                    config.prometheus().format());
            assertEquals(value[3], config.prometheus().executable().toString());
            assertTrue(config.grafana().url().toString().contains("12.4.1"));
            assertEquals(platform, config.platform());
        }
    }

    /** An explicit properties file overrides packaged directory values. */
    @Test
    void externalPropertiesOverridePackagedDefaults() throws Exception {
        Path properties = temporaryDirectory.resolve("custom.properties");
        Files.writeString(properties, "download.directory=${data.directory}/archives\n"
                + "install.directory=${data.directory}/native\n"
                + "prometheus.download.file=custom-prometheus.tar.gz\n");

        MonitoringDownloadConfig config = MonitoringDownloadConfig.fromSources(properties.toString(),
                temporaryDirectory.resolve("data"), Map.of());

        assertEquals(temporaryDirectory.resolve("data/archives"), config.downloadDirectory());
        assertEquals(temporaryDirectory.resolve("data/native"), config.installDirectory());
        assertEquals("custom-prometheus.tar.gz", config.prometheus().fileName());
        assertEquals(properties.toAbsolutePath().toString(), config.source());
    }

    /** Missing explicit property files fail before any network operation. */
    @Test
    void rejectsMissingExternalFile() {
        assertThrows(IllegalArgumentException.class, () -> MonitoringDownloadConfig.fromSources(
                temporaryDirectory.resolve("missing.properties").toString(), temporaryDirectory, Map.of()));
    }
}
