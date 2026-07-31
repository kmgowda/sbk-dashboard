/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import io.sbk.dashboard.config.MonitoringConfig;
import io.sbk.dashboard.config.MonitoringDownloadConfig;
import io.sbk.dashboard.config.MonitoringDownloadConfig.ArchiveFormat;
import io.sbk.dashboard.config.MonitoringDownloadConfig.ToolArchive;
import io.sbk.dashboard.config.RuntimePlatform;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import org.apache.commons.compress.archivers.zip.ZipArchiveEntry;
import org.apache.commons.compress.archivers.zip.ZipArchiveOutputStream;
import org.apache.commons.compress.compressors.gzip.GzipCompressorOutputStream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Tests verified download, safe extraction, and installation cache reuse. */
class NativeToolBootstrapTest {
    @TempDir
    private Path temporaryDirectory;

    /** Missing tools download once, install executable files, and then reuse the cache. */
    @Test
    void downloadsVerifiesInstallsAndReusesTools() throws Exception {
        Path archive = temporaryDirectory.resolve("tools.tar.gz");
        createArchive(archive, false);
        byte[] bytes = Files.readAllBytes(archive);
        AtomicInteger requests = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/tools.tar.gz", exchange -> {
            requests.incrementAndGet();
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            URI url = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/tools.tar.gz");
            String checksum = sha256(bytes);
            ToolArchive prometheus = new ToolArchive(url, "tools.tar.gz", checksum,
                    Path.of("prometheus-home"), Path.of("prometheus"), ArchiveFormat.TAR_GZ);
            ToolArchive grafana = new ToolArchive(url, "tools.tar.gz", checksum,
                    Path.of("grafana-home"), Path.of("bin/grafana"), ArchiveFormat.TAR_GZ);
            MonitoringDownloadConfig downloads = new MonitoringDownloadConfig(
                    temporaryDirectory.resolve("downloads"), temporaryDirectory.resolve("installs"),
                    prometheus, grafana, RuntimePlatform.current(), "test");
            MonitoringConfig requested = new MonitoringConfig(temporaryDirectory.resolve("missing-prometheus"),
                    temporaryDirectory.resolve("missing-grafana"), 9090, 3000,
                    URI.create("http://localhost:3000"));

            ByteArrayOutputStream progress = new ByteArrayOutputStream();
            NativeToolBootstrap bootstrap = new NativeToolBootstrap(HttpClient.newHttpClient(),
                    new PrintStream(progress, true, StandardCharsets.UTF_8));
            MonitoringConfig resolved = bootstrap.resolve(requested, downloads);
            MonitoringConfig cached = bootstrap.resolve(resolved, downloads);

            assertTrue(Files.isExecutable(resolved.prometheusBinary()));
            assertTrue(Files.isExecutable(resolved.grafanaHome().resolve("bin/grafana")));
            assertEquals(resolved, cached);
            assertEquals(1, requests.get());
            String progressText = progress.toString(StandardCharsets.UTF_8);
            assertTrue(progressText.contains("Prometheus download progress: 0.0%"));
            assertTrue(progressText.contains("Prometheus download progress: 100.0%"));
        } finally {
            server.stop(0);
        }
    }

    /** Archive entries cannot escape the temporary extraction directory. */
    @Test
    void rejectsArchivePathTraversal() throws Exception {
        Path archive = temporaryDirectory.resolve("unsafe.tar.gz");
        createArchive(archive, true);
        ToolArchive tool = new ToolArchive(URI.create("https://example.invalid/unsafe.tar.gz"),
                "unsafe.tar.gz", sha256(Files.readAllBytes(archive)), Path.of("prometheus-home"),
                Path.of("prometheus"), ArchiveFormat.TAR_GZ);

        assertThrows(IOException.class, () -> new NativeToolBootstrap().installArchive(
                "Prometheus", archive, temporaryDirectory.resolve("install"), tool));
    }

    /** Windows ZIP distributions extract normally and retain path-traversal protection. */
    @Test
    void extractsZipAndRejectsPathTraversal() throws Exception {
        Path archive = temporaryDirectory.resolve("windows.zip");
        createZip(archive, "prometheus-home/prometheus.exe");
        ToolArchive tool = new ToolArchive(URI.create("https://example.invalid/windows.zip"),
                "windows.zip", sha256(Files.readAllBytes(archive)), Path.of("prometheus-home"),
                Path.of("prometheus.exe"), ArchiveFormat.ZIP);
        Path installation = temporaryDirectory.resolve("zip-install");

        new NativeToolBootstrap().installArchive("Prometheus", archive, installation, tool);

        assertTrue(Files.isRegularFile(installation.resolve("prometheus-home/prometheus.exe")));

        Path unsafe = temporaryDirectory.resolve("unsafe.zip");
        createZip(unsafe, "../escaped.exe");
        assertThrows(IOException.class, () -> new NativeToolBootstrap().installArchive(
                "Prometheus", unsafe, temporaryDirectory.resolve("unsafe-install"), tool));
    }

    /** Simulated Windows bootstrap accepts .exe tools and ZIP distributions on a non-Windows test host. */
    @Test
    void installsWindowsExecutables() throws Exception {
        Path archive = temporaryDirectory.resolve("windows-tools.zip");
        createZip(archive, "prometheus-win/prometheus.exe", "grafana-win/bin/grafana.exe");
        byte[] bytes = Files.readAllBytes(archive);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/windows-tools.zip", exchange -> {
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        try {
            URI url = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/windows-tools.zip");
            String checksum = sha256(bytes);
            ToolArchive prometheus = new ToolArchive(url, "windows-tools.zip", checksum,
                    Path.of("prometheus-win"), Path.of("prometheus.exe"), ArchiveFormat.ZIP);
            ToolArchive grafana = new ToolArchive(url, "windows-tools.zip", checksum,
                    Path.of("grafana-win"), Path.of("bin/grafana.exe"), ArchiveFormat.ZIP);
            MonitoringDownloadConfig downloads = new MonitoringDownloadConfig(
                    temporaryDirectory.resolve("windows-downloads"), temporaryDirectory.resolve("windows-installs"),
                    prometheus, grafana, RuntimePlatform.from("Windows 11", "amd64"), "test");
            MonitoringConfig requested = new MonitoringConfig(temporaryDirectory.resolve("missing.exe"),
                    temporaryDirectory.resolve("missing-grafana-win"), 9090, 3000,
                    URI.create("http://localhost:3000"));

            MonitoringConfig resolved = new NativeToolBootstrap().resolve(requested, downloads);

            assertTrue(Files.isRegularFile(resolved.prometheusBinary()));
            assertTrue(Files.isRegularFile(resolved.grafanaHome().resolve("bin/grafana.exe")));
        } finally {
            server.stop(0);
        }
    }

    private void createArchive(Path archive, boolean unsafe) throws IOException {
        try (GzipCompressorOutputStream gzip = new GzipCompressorOutputStream(Files.newOutputStream(archive));
             TarArchiveOutputStream tar = new TarArchiveOutputStream(gzip)) {
            tar.setLongFileMode(TarArchiveOutputStream.LONGFILE_POSIX);
            if (unsafe) {
                addFile(tar, "../escaped", "unsafe");
            } else {
                addFile(tar, "prometheus-home/prometheus", "prometheus");
                addFile(tar, "grafana-home/bin/grafana", "grafana");
            }
        }
    }

    private void addFile(TarArchiveOutputStream tar, String name, String value) throws IOException {
        byte[] content = value.getBytes(StandardCharsets.UTF_8);
        TarArchiveEntry entry = new TarArchiveEntry(name);
        entry.setMode(0755);
        entry.setSize(content.length);
        tar.putArchiveEntry(entry);
        tar.write(content);
        tar.closeArchiveEntry();
    }

    private void createZip(Path archive, String... names) throws IOException {
        try (ZipArchiveOutputStream zip = new ZipArchiveOutputStream(archive)) {
            for (String name : names) {
                byte[] content = "executable".getBytes(StandardCharsets.UTF_8);
                ZipArchiveEntry entry = new ZipArchiveEntry(name);
                entry.setSize(content.length);
                entry.setUnixMode(0755);
                zip.putArchiveEntry(entry);
                zip.write(content);
                zip.closeArchiveEntry();
            }
        }
    }

    private String sha256(byte[] content) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
    }
}
