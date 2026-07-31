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
                                       ToolArchive prometheus, ToolArchive grafana, String source) {
    private static final String RESOURCE = "/monitoring-download.properties";
    private static final String ENVIRONMENT = "SBK_DASHBOARD_MONITORING_PROPERTIES";

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
        Properties properties = defaults();
        Path external = externalPath(propertiesFile, environment);
        String selectedSource = "packaged monitoring-download.properties";
        if (external != null) {
            try (InputStream input = Files.newInputStream(external)) {
                properties.load(input);
            }
            selectedSource = external.toAbsolutePath().normalize().toString();
        }
        Map<String, String> variables = Map.of(
                "data.directory", dataDirectory.toAbsolutePath().normalize().toString(),
                "user.home", System.getProperty("user.home"),
                "os.arch", System.getProperty("os.arch"),
                "os.name", System.getProperty("os.name").toLowerCase(Locale.ROOT));
        Path downloads = path(property(properties, "download.directory"), variables);
        Path installs = path(property(properties, "install.directory"), variables);
        return new MonitoringDownloadConfig(downloads, installs,
                tool(properties, variables, "prometheus"), tool(properties, variables, "grafana"),
                selectedSource);
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

    private static ToolArchive tool(Properties properties, Map<String, String> variables, String prefix) {
        URI url = URI.create(expand(property(properties, prefix + ".download.url"), variables));
        if (url.getScheme() == null || url.getHost() == null || !url.getScheme().equalsIgnoreCase("https")) {
            throw new IllegalArgumentException(prefix + " download URL must be an absolute HTTPS URL");
        }
        String file = expand(property(properties, prefix + ".download.file"), variables);
        if (!Path.of(file).getFileName().toString().equals(file)) {
            throw new IllegalArgumentException(prefix + " download file must be a file name");
        }
        String checksum = property(properties, prefix + ".download.sha256").toLowerCase(Locale.ROOT);
        if (!checksum.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(prefix + " SHA-256 must contain 64 hexadecimal characters");
        }
        Path archiveDirectory = relative(property(properties, prefix + ".archive.directory"), prefix);
        Path executable = relative(property(properties, prefix + ".executable"), prefix);
        return new ToolArchive(url, file, checksum, archiveDirectory, executable);
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

    /** A checksummed tar.gz distribution and paths inside it. */
    public record ToolArchive(URI url, String fileName, String sha256,
                              Path archiveDirectory, Path executable) { }
}
