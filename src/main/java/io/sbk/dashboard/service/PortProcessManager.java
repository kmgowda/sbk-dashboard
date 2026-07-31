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

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Safely identifies and terminates native monitoring listeners on Linux. */
final class PortProcessManager {
    private static final String SOCKET_PREFIX = "socket:[";
    private static final Duration STOP_TIMEOUT = Duration.ofSeconds(5);

    private PortProcessManager() {
    }

    /** Verifies all occupied ports before terminating any matching process. */
    static void terminateExisting(int prometheusPort, int grafanaPort) throws IOException, InterruptedException {
        List<Listener> listeners = new ArrayList<>();
        inspect("Prometheus", prometheusPort, Tool.PROMETHEUS, listeners);
        inspect("Grafana", grafanaPort, Tool.GRAFANA, listeners);
        for (Listener listener : listeners) {
            for (ProcessHandle process : listener.processes()) {
                System.err.println("Stopping existing " + listener.name() + " process on port "
                        + listener.port() + " (pid " + process.pid() + ')');
                stop(process, listener.name());
            }
        }
        for (Listener listener : listeners) {
            if (!awaitAvailable(listener.port())) {
                throw new IOException(listener.name() + " port " + listener.port()
                        + " remains occupied after stopping its existing process");
            }
        }
    }

    /** Returns whether a new process can bind the supplied TCP port. */
    static boolean available(int port) {
        try {
            if (Files.isReadable(Path.of("/proc/net/tcp"))) {
                Set<String> listeners = new HashSet<>();
                collectSocketInodes(Path.of("/proc/net/tcp"), port, listeners);
                collectSocketInodes(Path.of("/proc/net/tcp6"), port, listeners);
                return listeners.isEmpty();
            }
        } catch (IOException exception) {
            // Fall through to a bind probe when the operating-system table cannot be read.
        }
        try (ServerSocket socket = new ServerSocket()) {
            socket.setReuseAddress(false);
            socket.bind(new InetSocketAddress(port));
            return true;
        } catch (IOException | SecurityException exception) {
            return false;
        }
    }

    private static void inspect(String name, int port, Tool tool, List<Listener> listeners) throws IOException {
        if (available(port)) {
            return;
        }
        Set<Long> identifiers = listenerProcessIds(port);
        if (identifiers.isEmpty()) {
            throw new IOException("Port " + port + " is occupied, but its owner cannot be identified safely; "
                    + "no process was stopped");
        }
        List<ProcessHandle> processes = new ArrayList<>();
        for (long identifier : identifiers) {
            ProcessHandle process = ProcessHandle.of(identifier).orElseThrow(() ->
                    new IOException("Listener process " + identifier + " disappeared from port " + port));
            String command = process.info().command().orElse("");
            if (!tool.matches(command)) {
                throw new IOException("Port " + port + " is owned by unrelated process " + identifier
                        + " (" + (command.isBlank() ? "unknown command" : command) + "); no process was stopped");
            }
            processes.add(process);
        }
        listeners.add(new Listener(name, port, List.copyOf(processes)));
    }

    private static Set<Long> listenerProcessIds(int port) throws IOException {
        Set<String> sockets = new HashSet<>();
        collectSocketInodes(Path.of("/proc/net/tcp"), port, sockets);
        collectSocketInodes(Path.of("/proc/net/tcp6"), port, sockets);
        if (sockets.isEmpty()) {
            return Set.of();
        }
        Map<String, Long> owners = new HashMap<>();
        try (DirectoryStream<Path> processes = Files.newDirectoryStream(Path.of("/proc"),
                entry -> entry.getFileName().toString().matches("[0-9]+"))) {
            for (Path process : processes) {
                long identifier = Long.parseLong(process.getFileName().toString());
                Path descriptors = process.resolve("fd");
                try (DirectoryStream<Path> files = Files.newDirectoryStream(descriptors)) {
                    for (Path file : files) {
                        try {
                            String target = Files.readSymbolicLink(file).toString();
                            if (sockets.contains(target)) {
                                owners.put(target, identifier);
                            }
                        } catch (IOException | SecurityException exception) {
                            // Processes can exit or restrict their descriptors during discovery.
                        }
                    }
                } catch (IOException | SecurityException exception) {
                    // Ignore processes whose descriptor directory is inaccessible.
                }
            }
        }
        return Set.copyOf(owners.values());
    }

    private static void collectSocketInodes(Path table, int port, Set<String> sockets) throws IOException {
        if (!Files.isReadable(table)) {
            return;
        }
        String expectedPort = String.format(Locale.ROOT, "%04X", port);
        for (String line : Files.readAllLines(table)) {
            String[] fields = line.trim().split("\\s+");
            if (fields.length > 9 && fields[1].endsWith(':' + expectedPort)
                    && fields[3].equals("0A")) {
                sockets.add(SOCKET_PREFIX + fields[9] + ']');
            }
        }
    }

    private static void stop(ProcessHandle process, String name) throws IOException, InterruptedException {
        process.destroy();
        long deadline = System.nanoTime() + STOP_TIMEOUT.toNanos();
        while (process.isAlive() && System.nanoTime() < deadline) {
            Thread.sleep(100);
        }
        if (process.isAlive()) {
            System.err.println("WARNING: " + name + " pid " + process.pid()
                    + " did not stop gracefully; forcing termination");
            process.destroyForcibly();
            deadline = System.nanoTime() + STOP_TIMEOUT.toNanos();
            while (process.isAlive() && System.nanoTime() < deadline) {
                Thread.sleep(100);
            }
        }
        if (process.isAlive()) {
            throw new IOException("Unable to stop existing " + name + " process " + process.pid());
        }
    }

    private static boolean awaitAvailable(int port) throws InterruptedException {
        long deadline = System.nanoTime() + STOP_TIMEOUT.toNanos();
        while (System.nanoTime() < deadline) {
            if (available(port)) {
                return true;
            }
            Thread.sleep(100);
        }
        return available(port);
    }

    private enum Tool {
        PROMETHEUS {
            @Override
            boolean matches(String command) {
                return fileName(command).equals("prometheus");
            }
        },
        GRAFANA {
            @Override
            boolean matches(String command) {
                String name = fileName(command);
                return name.equals("grafana") || name.equals("grafana-server");
            }
        };

        abstract boolean matches(String command);

        static String fileName(String command) {
            if (command.isBlank()) {
                return "";
            }
            return Path.of(command).getFileName().toString().toLowerCase(Locale.ROOT);
        }
    }

    private record Listener(String name, int port, List<ProcessHandle> processes) { }
}
