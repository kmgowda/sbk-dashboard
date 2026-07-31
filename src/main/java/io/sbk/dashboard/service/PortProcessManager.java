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
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import oshi.SystemInfo;
import oshi.software.os.InternetProtocolStats.IPConnection;
import oshi.software.os.InternetProtocolStats.TcpState;

/** Safely identifies and terminates native monitoring listeners across supported operating systems. */
final class PortProcessManager {
    private static final Duration STOP_TIMEOUT = Duration.ofSeconds(5);
    private static final SystemInfo SYSTEM = new SystemInfo();

    private PortProcessManager() {
    }

    /** Verifies all occupied ports before terminating any matching process. */
    static void terminateExisting(int prometheusPort, int grafanaPort, ManagedProcessRegistry registry)
            throws IOException, InterruptedException {
        List<Listener> listeners = new ArrayList<>();
        inspect("Prometheus", "prometheus", prometheusPort, Tool.PROMETHEUS, registry, listeners);
        inspect("Grafana", "grafana", grafanaPort, Tool.GRAFANA, registry, listeners);
        Set<Long> stopped = new HashSet<>();
        for (Listener listener : listeners) {
            for (ProcessHandle process : listener.processes()) {
                if (stopped.add(process.pid())) {
                    System.err.println("Stopping existing " + listener.name() + " process on port "
                            + listener.port() + " (pid " + process.pid() + ')');
                    stop(process, listener.name());
                }
            }
        }
        for (Listener listener : listeners) {
            if (!awaitAvailable(listener.port())) {
                throw new IOException(listener.name() + " port " + listener.port()
                        + " remains occupied after stopping its existing process");
            }
        }
    }

    /** Returns whether no TCP listener currently owns the supplied port. */
    static boolean available(int port) {
        try {
            return connections(port).isEmpty();
        } catch (RuntimeException | LinkageError exception) {
            return bindAvailable(port);
        }
    }

    private static boolean bindAvailable(int port) {
        try (ServerSocket socket = new ServerSocket()) {
            socket.setReuseAddress(false);
            socket.bind(new InetSocketAddress(port));
            return true;
        } catch (IOException | SecurityException exception) {
            return false;
        }
    }

    private static void inspect(String name, String component, int port, Tool tool,
                                ManagedProcessRegistry registry, List<Listener> listeners) throws IOException {
        List<IPConnection> connections;
        try {
            connections = connections(port);
        } catch (RuntimeException | LinkageError exception) {
            if (bindAvailable(port)) {
                return;
            }
            ProcessHandle owned = registry.find(component, port);
            if (owned == null) {
                throw new IOException("Port " + port + " is occupied, but listener discovery is unavailable; "
                        + "no process was stopped", exception);
            }
            connections = List.of();
            validate(name, port, tool, Set.of(owned.pid()), listeners);
            return;
        }
        if (connections.isEmpty()) {
            return;
        }
        Set<Long> identifiers = new HashSet<>();
        for (IPConnection connection : connections) {
            if (connection.getowningProcessId() <= 0) {
                ProcessHandle owned = registry.find(component, port);
                if (owned == null) {
                    throw new IOException("Port " + port
                            + " is occupied, but its owner cannot be identified safely; no process was stopped");
                }
                identifiers.add(owned.pid());
            } else {
                identifiers.add((long) connection.getowningProcessId());
            }
        }
        validate(name, port, tool, identifiers, listeners);
    }

    private static void validate(String name, int port, Tool tool, Set<Long> identifiers,
                                 List<Listener> listeners) throws IOException {
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

    private static List<IPConnection> connections(int port) {
        return SYSTEM.getOperatingSystem().getInternetProtocolStats().getConnections().stream()
                .filter(connection -> connection.getState() == TcpState.LISTEN)
                .filter(connection -> connection.getLocalPort() == port)
                .toList();
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
                return baseName(command).equals("prometheus");
            }
        },
        GRAFANA {
            @Override
            boolean matches(String command) {
                String name = baseName(command);
                return name.equals("grafana") || name.equals("grafana-server");
            }
        };

        abstract boolean matches(String command);

        static String baseName(String command) {
            if (command.isBlank()) {
                return "";
            }
            String name = Path.of(command).getFileName().toString().toLowerCase(Locale.ROOT);
            return name.endsWith(".exe") ? name.substring(0, name.length() - 4) : name;
        }
    }

    private record Listener(String name, int port, List<ProcessHandle> processes) { }
}
