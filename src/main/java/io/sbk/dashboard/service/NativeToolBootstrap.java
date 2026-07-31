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

import io.sbk.dashboard.config.MonitoringConfig;
import io.sbk.dashboard.config.MonitoringDownloadConfig;
import io.sbk.dashboard.config.MonitoringDownloadConfig.ToolArchive;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.stream.Stream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream;

/** Downloads and securely installs missing native Prometheus and Grafana distributions. */
public final class NativeToolBootstrap {
    private static final Duration DOWNLOAD_TIMEOUT = Duration.ofMinutes(15);
    private final HttpClient httpClient;

    /** Creates a bootstrapper that follows release-download redirects. */
    public NativeToolBootstrap() {
        this(HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL)
                .connectTimeout(Duration.ofSeconds(20)).build());
    }

    NativeToolBootstrap(HttpClient httpClient) {
        this.httpClient = httpClient;
    }

    /**
     * Resolves installed tools first and downloads only missing components.
     *
     * @param configured requested process configuration
     * @param downloads verified archive configuration
     * @return process configuration containing executable installed paths
     * @throws IOException when a required tool cannot be prepared
     * @throws InterruptedException when a download is interrupted
     */
    public MonitoringConfig resolve(MonitoringConfig configured, MonitoringDownloadConfig downloads)
            throws IOException, InterruptedException {
        Path prometheus = findExecutable(configured.prometheusBinary());
        if (prometheus == null) {
            System.out.println("Prometheus is not installed at " + configured.prometheusBinary()
                    + "; bootstrapping from properties");
            prometheus = install("Prometheus", downloads, downloads.prometheus());
        } else {
            System.out.println("Using installed Prometheus: " + prometheus);
        }

        Path grafanaHome = configured.grafanaHome().toAbsolutePath().normalize();
        if (grafanaExecutable(grafanaHome) == null) {
            System.out.println("Grafana is not installed under " + grafanaHome
                    + "; bootstrapping from properties");
            Path executable = install("Grafana", downloads, downloads.grafana());
            grafanaHome = executable.getParent().getParent();
        } else {
            System.out.println("Using installed Grafana: " + grafanaHome);
        }
        return configured.withNativeTools(prometheus, grafanaHome);
    }

    private Path install(String name, MonitoringDownloadConfig config, ToolArchive tool)
            throws IOException, InterruptedException {
        Path home = config.installDirectory().resolve(tool.archiveDirectory()).normalize();
        Path executable = home.resolve(tool.executable()).normalize();
        if (!executable.startsWith(home)) {
            throw new IOException(name + " executable escapes its installation directory");
        }
        if (Files.isExecutable(executable)) {
            System.out.println("Using cached " + name + ": " + executable);
            return executable;
        }
        Files.createDirectories(config.downloadDirectory());
        Files.createDirectories(config.installDirectory());
        Path archive = config.downloadDirectory().resolve(tool.fileName());
        if (!Files.isRegularFile(archive) || !checksum(archive).equals(tool.sha256())) {
            download(name, tool, archive);
        } else {
            System.out.println("Using verified cached " + name + " archive: " + archive);
        }
        if (!checksum(archive).equals(tool.sha256())) {
            throw new IOException(name + " archive SHA-256 verification failed: " + archive);
        }
        installArchive(name, archive, config.installDirectory(), tool);
        if (!Files.isExecutable(executable)) {
            throw new IOException(name + " executable is missing after extraction: " + executable);
        }
        System.out.println(name + " installed at " + home);
        return executable;
    }

    private void download(String name, ToolArchive tool, Path destination)
            throws IOException, InterruptedException {
        Path temporary = Files.createTempFile(destination.getParent(), tool.fileName(), ".part");
        System.out.println("Downloading " + name + " from " + tool.url());
        System.out.println("Download destination: " + destination);
        try {
            HttpRequest request = HttpRequest.newBuilder(tool.url()).timeout(DOWNLOAD_TIMEOUT).GET().build();
            HttpResponse<Path> response = httpClient.send(request, HttpResponse.BodyHandlers.ofFile(temporary));
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IOException(name + " download returned HTTP " + response.statusCode());
            }
            String actual = checksum(temporary);
            if (!actual.equals(tool.sha256())) {
                throw new IOException(name + " download SHA-256 mismatch; expected " + tool.sha256()
                        + " but received " + actual);
            }
            replace(temporary, destination);
        } finally {
            Files.deleteIfExists(temporary);
        }
        System.out.println(name + " download verified successfully");
    }

    void installArchive(String name, Path archive, Path installDirectory, ToolArchive tool) throws IOException {
        Files.createDirectories(installDirectory);
        Path extraction = Files.createTempDirectory(installDirectory, ".extract-");
        try {
            extractTarGzip(archive, extraction);
            Path extractedHome = extraction.resolve(tool.archiveDirectory()).normalize();
            if (!Files.isDirectory(extractedHome)) {
                throw new IOException(name + " archive does not contain " + tool.archiveDirectory());
            }
            Path destination = installDirectory.resolve(tool.archiveDirectory()).normalize();
            if (Files.exists(destination)) {
                Path preserved = destination.resolveSibling(destination.getFileName()
                        + ".incomplete-" + Instant.now().toEpochMilli());
                Files.move(destination, preserved);
                System.err.println("WARNING: Preserved incomplete " + name + " installation at " + preserved);
            }
            move(extractedHome, destination);
        } finally {
            deleteTree(extraction);
        }
    }

    private void extractTarGzip(Path archive, Path destination) throws IOException {
        try (InputStream file = new BufferedInputStream(Files.newInputStream(archive));
             GzipCompressorInputStream gzip = new GzipCompressorInputStream(file);
             TarArchiveInputStream tar = new TarArchiveInputStream(gzip)) {
            TarArchiveEntry entry;
            while ((entry = tar.getNextEntry()) != null) {
                Path target = destination.resolve(entry.getName()).normalize();
                if (!target.startsWith(destination)) {
                    throw new IOException("Archive entry escapes extraction directory: " + entry.getName());
                }
                if (entry.isSymbolicLink() || entry.isLink()) {
                    throw new IOException("Archive links are not supported: " + entry.getName());
                }
                if (entry.isDirectory()) {
                    Files.createDirectories(target);
                } else if (entry.isFile()) {
                    Files.createDirectories(target.getParent());
                    Files.copy(tar, target, StandardCopyOption.REPLACE_EXISTING);
                    if ((entry.getMode() & 0111) != 0 && !target.toFile().setExecutable(true, false)) {
                        throw new IOException("Unable to mark executable: " + target);
                    }
                }
            }
        }
    }

    private Path findExecutable(Path configured) {
        if (configured.isAbsolute() || configured.getNameCount() > 1) {
            Path normalized = configured.toAbsolutePath().normalize();
            return Files.isExecutable(normalized) ? normalized : null;
        }
        for (String directory : System.getenv().getOrDefault("PATH", "").split(java.io.File.pathSeparator)) {
            Path candidate = Path.of(directory).resolve(configured);
            if (Files.isExecutable(candidate)) {
                return candidate.toAbsolutePath().normalize();
            }
        }
        return null;
    }

    private Path grafanaExecutable(Path home) {
        for (String name : new String[] {"grafana", "grafana-server"}) {
            Path candidate = home.resolve("bin").resolve(name);
            if (Files.isExecutable(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    private String checksum(Path path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
        try (InputStream input = new DigestInputStream(Files.newInputStream(path), digest)) {
            input.transferTo(java.io.OutputStream.nullOutputStream());
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private void replace(Path source, Path destination) throws IOException {
        try {
            Files.move(source, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException exception) {
            Files.move(source, destination, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private void move(Path source, Path destination) throws IOException {
        try {
            Files.move(source, destination, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException exception) {
            Files.move(source, destination);
        }
    }

    private void deleteTree(Path root) throws IOException {
        if (!Files.exists(root)) {
            return;
        }
        try (Stream<Path> paths = Files.walk(root)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }
}
