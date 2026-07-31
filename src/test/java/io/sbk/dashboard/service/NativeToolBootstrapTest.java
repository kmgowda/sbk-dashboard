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
import io.sbk.dashboard.config.MonitoringDownloadConfig.ToolArchive;
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
                    Path.of("prometheus-home"), Path.of("prometheus"));
            ToolArchive grafana = new ToolArchive(url, "tools.tar.gz", checksum,
                    Path.of("grafana-home"), Path.of("bin/grafana"));
            MonitoringDownloadConfig downloads = new MonitoringDownloadConfig(
                    temporaryDirectory.resolve("downloads"), temporaryDirectory.resolve("installs"),
                    prometheus, grafana, "test");
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
                Path.of("prometheus"));

        assertThrows(IOException.class, () -> new NativeToolBootstrap().installArchive(
                "Prometheus", archive, temporaryDirectory.resolve("install"), tool));
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

    private String sha256(byte[] content) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
    }
}
