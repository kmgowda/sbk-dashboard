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

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;

/** Download locations and verified native archive definitions. */
public record MonitoringDownloadConfig(Path downloadDirectory, Path installDirectory,
                                       ToolArchive prometheus, ToolArchive grafana, RuntimePlatform platform,
                                       String source) {
    private static final String RESOURCE = "/monitoring-download.properties";
    private static final String ENVIRONMENT = "SBK_DASHBOARD_MONITORING_PROPERTIES";
    private static final String[] TOOL_PROPERTIES = {"download.url", "download.file", "download.sha256",
        "archive.directory", "executable", "archive.format"};

    /**
     * Loads packaged defaults and applies an optional external properties file.
     *
     * @param propertiesFile command-line properties path, or null
     * @param dataDirectory selected dashboard data directory
     * @return validated bootstrap configuration
     * @throws IOException when properties cannot be loaded
     */
    public static MonitoringDownloadConfig fromOptions(String propertiesFile, Path dataDirectory)
            throws IOException {
        return fromSources(propertiesFile, dataDirectory, System.getenv());
    }

    static MonitoringDownloadConfig fromSources(String propertiesFile, Path dataDirectory,
                                                 Map<String, String> environment) throws IOException {
        return fromSources(propertiesFile, dataDirectory, environment, RuntimePlatform.current());
    }

    static MonitoringDownloadConfig fromSources(String propertiesFile, Path dataDirectory,
                                                 Map<String, String> environment, RuntimePlatform platform)
            throws IOException {
        Properties properties = defaults();
        Path external = externalPath(propertiesFile, environment);
        String selectedSource = "packaged monitoring-download.properties";
        if (external != null) {
            Properties overrides = new Properties();
            try (InputStream input = Files.newInputStream(external)) {
                overrides.load(input);
            }
            properties.putAll(overrides);
            applyLegacyOverrides(properties, overrides, platform);
            selectedSource = external.toAbsolutePath().normalize().toString();
        }
        Map<String, String> variables = Map.of(
                "data.directory", dataDirectory.toAbsolutePath().normalize().toString(),
                "user.home", System.getProperty("user.home"),
                "os.arch", platform.architecture().name().toLowerCase(Locale.ROOT),
                "os.name", platform.operatingSystem().name().toLowerCase(Locale.ROOT));
        Path downloads = path(property(properties, "download.directory"), variables);
        Path installs = path(property(properties, "install.directory"), variables);
        return new MonitoringDownloadConfig(downloads, installs,
                tool(properties, variables, "prometheus", platform),
                tool(properties, variables, "grafana", platform), platform, selectedSource);
    }

    private static void applyLegacyOverrides(Properties properties, Properties overrides,
                                             RuntimePlatform platform) {
        for (String tool : new String[] {"prometheus", "grafana"}) {
            for (String suffix : TOOL_PROPERTIES) {
                String legacy = tool + '.' + suffix;
                String selected = tool + '.' + platform.id() + '.' + suffix;
                if (overrides.containsKey(legacy) && !overrides.containsKey(selected)) {
                    properties.setProperty(selected, overrides.getProperty(legacy));
                }
            }
        }
    }

    private static Properties defaults() throws IOException {
        Properties properties = new Properties();
        try (InputStream input = MonitoringDownloadConfig.class.getResourceAsStream(RESOURCE)) {
            if (input == null) {
                throw new IOException("Packaged monitoring-download.properties is missing");
            }
            properties.load(input);
        }
        return properties;
    }

    private static Path externalPath(String option, Map<String, String> environment) {
        if (option != null) {
            if (option.isBlank()) {
                throw new IllegalArgumentException("-monitoring-properties must not be blank");
            }
            Path path = Path.of(option.trim()).toAbsolutePath().normalize();
            if (!Files.isRegularFile(path)) {
                throw new IllegalArgumentException("Monitoring properties file does not exist: " + path);
            }
            return path;
        }
        String configured = environment.get(ENVIRONMENT);
        if (configured != null && !configured.isBlank()) {
            Path path = Path.of(configured.trim()).toAbsolutePath().normalize();
            if (!Files.isRegularFile(path)) {
                throw new IllegalArgumentException("Monitoring properties file does not exist: " + path);
            }
            return path;
        }
        Path distribution = distributionProperties();
        if (distribution != null && Files.isRegularFile(distribution)) {
            return distribution;
        }
        for (Path candidate : new Path[] {Path.of("conf/monitoring-download.properties"),
                Path.of("config/monitoring-download.properties")}) {
            if (Files.isRegularFile(candidate)) {
                return candidate.toAbsolutePath().normalize();
            }
        }
        return null;
    }

    private static Path distributionProperties() {
        try {
            URI location = MonitoringDownloadConfig.class.getProtectionDomain().getCodeSource().getLocation().toURI();
            Path code = Path.of(location).toAbsolutePath().normalize();
            if (Files.isRegularFile(code) && code.getParent() != null && code.getParent().getParent() != null) {
                return code.getParent().getParent().resolve("conf/monitoring-download.properties");
            }
        } catch (URISyntaxException | IllegalArgumentException exception) {
            // A non-file class loader can still use packaged defaults or an explicit override.
        }
        return null;
    }

    private static ToolArchive tool(Properties properties, Map<String, String> variables, String prefix,
                                    RuntimePlatform platform) {
        String platformPrefix = prefix + '.' + platform.id();
        URI url = URI.create(expand(platformProperty(properties, platformPrefix, prefix, "download.url"), variables));
        if (url.getScheme() == null || url.getHost() == null || !url.getScheme().equalsIgnoreCase("https")) {
            throw new IllegalArgumentException(prefix + " download URL must be an absolute HTTPS URL");
        }
        String file = expand(platformProperty(properties, platformPrefix, prefix, "download.file"), variables);
        if (!Path.of(file).getFileName().toString().equals(file)) {
            throw new IllegalArgumentException(prefix + " download file must be a file name");
        }
        String checksum = platformProperty(properties, platformPrefix, prefix, "download.sha256")
                .toLowerCase(Locale.ROOT);
        if (!checksum.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(prefix + " SHA-256 must contain 64 hexadecimal characters");
        }
        Path archiveDirectory = relative(platformProperty(properties, platformPrefix, prefix,
                "archive.directory"), prefix);
        Path executable = relative(platformProperty(properties, platformPrefix, prefix, "executable"), prefix);
        ArchiveFormat format = ArchiveFormat.from(platformProperty(properties, platformPrefix, prefix,
                "archive.format"));
        return new ToolArchive(url, file, checksum, archiveDirectory, executable, format);
    }

    private static String platformProperty(Properties properties, String platformPrefix, String legacyPrefix,
                                           String suffix) {
        String value = properties.getProperty(platformPrefix + '.' + suffix);
        return value == null || value.isBlank() ? property(properties, legacyPrefix + '.' + suffix) : value.trim();
    }

    private static Path relative(String value, String prefix) {
        Path path = Path.of(value).normalize();
        if (path.isAbsolute() || path.startsWith("..") || path.toString().isBlank()) {
            throw new IllegalArgumentException(prefix + " archive paths must be safe relative paths");
        }
        return path;
    }

    private static String property(Properties properties, String name) {
        String value = properties.getProperty(name);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Missing monitoring property: " + name);
        }
        return value.trim();
    }

    private static Path path(String value, Map<String, String> variables) {
        return Path.of(expand(value, variables)).toAbsolutePath().normalize();
    }

    private static String expand(String value, Map<String, String> variables) {
        String expanded = value;
        for (Map.Entry<String, String> variable : variables.entrySet()) {
            expanded = expanded.replace("${" + variable.getKey() + '}', variable.getValue());
        }
        if (expanded.contains("${")) {
            throw new IllegalArgumentException("Unknown placeholder in monitoring property: " + expanded);
        }
        return expanded;
    }

    /** Supported native release archive formats. */
    public enum ArchiveFormat {
        TAR_GZ("tar.gz"), ZIP("zip");

        private final String value;

        ArchiveFormat(String value) {
            this.value = value;
        }

        static ArchiveFormat from(String value) {
            for (ArchiveFormat format : values()) {
                if (format.value.equalsIgnoreCase(value.trim())) {
                    return format;
                }
            }
            throw new IllegalArgumentException("Unsupported archive format: " + value);
        }
    }

    /** A checksummed native distribution and paths inside it. */
    public record ToolArchive(URI url, String fileName, String sha256,
                              Path archiveDirectory, Path executable, ArchiveFormat format) { }
}
