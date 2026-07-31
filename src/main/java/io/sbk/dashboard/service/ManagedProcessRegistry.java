/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import com.fasterxml.jackson.core.type.TypeReference;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/** Persists enough child-process identity to validate ownership after an unclean shutdown. */
final class ManagedProcessRegistry {
    private static final TypeReference<Map<String, Entry>> ENTRY_MAP = new TypeReference<>() { };
    private final Path file;

    ManagedProcessRegistry(Path file) {
        this.file = file;
    }

    synchronized void record(String component, Process process, int port) throws IOException {
        ProcessHandle.Info info = process.info();
        long started = info.startInstant().orElse(Instant.EPOCH).toEpochMilli();
        String command = info.command().orElse("");
        Map<String, Entry> entries = read();
        entries.put(component, new Entry(process.pid(), started, command, port));
        write(entries);
    }

    synchronized ProcessHandle find(String component, int port) throws IOException {
        Entry entry = read().get(component);
        if (entry == null || entry.port() != port) {
            return null;
        }
        ProcessHandle process = ProcessHandle.of(entry.pid()).orElse(null);
        if (process == null || !process.isAlive()) {
            return null;
        }
        long started = process.info().startInstant().orElse(Instant.EPOCH).toEpochMilli();
        String command = process.info().command().orElse("");
        return started == entry.started() && command.equals(entry.command()) ? process : null;
    }

    synchronized void remove(String component, long pid) throws IOException {
        Map<String, Entry> entries = read();
        Entry entry = entries.get(component);
        if (entry != null && entry.pid() == pid) {
            entries.remove(component);
            write(entries);
        }
    }

    private Map<String, Entry> read() throws IOException {
        if (!Files.isRegularFile(file)) {
            return new LinkedHashMap<>();
        }
        return new LinkedHashMap<>(JsonSupport.mapper().readValue(file.toFile(), ENTRY_MAP));
    }

    private void write(Map<String, Entry> entries) throws IOException {
        byte[] json = JsonSupport.mapper().writerWithDefaultPrettyPrinter().writeValueAsBytes(entries);
        AtomicFiles.write(file, json);
    }

    private record Entry(long pid, long started, String command, int port) { }
}
