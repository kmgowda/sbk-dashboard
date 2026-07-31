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

import java.io.BufferedInputStream;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileTime;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BiConsumer;
import java.util.zip.CRC32;

/** Checksummed, append-only, segmented persistence for endpoint scrape batches. */
public final class SegmentedMetricStore implements AutoCloseable {
    private static final int MAGIC = 0x53424B54;
    private static final int VERSION = 1;
    private static final int HEADER_BYTES = Integer.BYTES * 2;
    private static final int FRAME_HEADER_BYTES = Integer.BYTES * 2;
    private static final int MAX_FRAME_BYTES = 32 * 1024 * 1024;
    private static final long MAX_SEGMENT_AGE_MILLIS = Duration.ofHours(1).toMillis();
    private final Path root;
    private final long retentionMillis;
    private final int segmentSizeBytes;
    private final Map<String, WriterState> writers = new ConcurrentHashMap<>();
    private final Map<String, Object> endpointLocks = new ConcurrentHashMap<>();
    private final AtomicInteger sequence = new AtomicInteger();

    /**
     * Creates a persistent store.
     *
     * @param root segment root directory
     * @param retentionDays maximum segment age, independently applied per endpoint
     * @param segmentSizeBytes rollover threshold
     * @throws IOException when the root directory cannot be created
     */
    public SegmentedMetricStore(Path root, int retentionDays, int segmentSizeBytes) throws IOException {
        this.root = root.toAbsolutePath().normalize();
        this.retentionMillis = Duration.ofDays(retentionDays).toMillis();
        this.segmentSizeBytes = segmentSizeBytes;
        Files.createDirectories(this.root);
    }

    /**
     * Appends and synchronizes one complete scrape batch.
     *
     * @param targetId endpoint identifier
     * @param timestamp scrape epoch milliseconds
     * @param metrics parsed samples
     * @throws IOException when the record cannot be persisted
     */
    public void append(String targetId, long timestamp, List<PrometheusTextParser.ParsedMetric> metrics)
            throws IOException {
        validateIdentifier(targetId);
        byte[] payload = encode(timestamp, metrics);
        if (payload.length > MAX_FRAME_BYTES) {
            throw new IOException("Persistent metric frame exceeds 32 MiB");
        }
        Object lock = endpointLocks.computeIfAbsent(targetId, ignored -> new Object());
        synchronized (lock) {
            WriterState writer = null;
            try {
                writer = writer(targetId);
                boolean sizeRollover = writer.size + payload.length + FRAME_HEADER_BYTES > segmentSizeBytes;
                boolean ageRollover = System.currentTimeMillis() - writer.createdAt >= MAX_SEGMENT_AGE_MILLIS;
                if ((sizeRollover || ageRollover) && writer.size > HEADER_BYTES) {
                    closeWriter(targetId, writer);
                    writer = null;
                    writer = writer(targetId);
                }
                CRC32 crc = new CRC32();
                crc.update(payload);
                ByteBuffer frame = ByteBuffer.allocate(payload.length + FRAME_HEADER_BYTES);
                frame.putInt(payload.length).putInt((int) crc.getValue()).put(payload).flip();
                while (frame.hasRemaining()) {
                    writer.channel.write(frame);
                }
                writer.channel.force(true);
                writer.size += payload.length + FRAME_HEADER_BYTES;
                if (System.currentTimeMillis() - writer.lastPruned > Duration.ofHours(1).toMillis()) {
                    prune(targetId, writer.path);
                    writer.lastPruned = System.currentTimeMillis();
                }
            } catch (IOException exception) {
                discardFailedWriter(targetId, writer, exception);
                throw exception;
            }
        }
    }

    /**
     * Replays valid retained frames in chronological segment order.
     * Corrupt or truncated segment tails are ignored.
     *
     * @param targetId endpoint identifier
     * @param consumer receives timestamp and decoded batch
     * @return recovery counts, including any damaged or expired segments
     * @throws IOException when segment discovery fails
     */
    public RecoveryReport recover(String targetId,
                                  BiConsumer<Long, List<PrometheusTextParser.ParsedMetric>> consumer)
            throws IOException {
        validateIdentifier(targetId);
        Path directory = endpointDirectory(targetId);
        if (!Files.isDirectory(directory)) {
            return new RecoveryReport(0, 0, 0);
        }
        int expired = prune(targetId, null);
        int recovered = 0;
        int damaged = 0;
        for (Path segment : segments(directory)) {
            SegmentRecovery result = recoverSegment(segment, consumer);
            recovered += result.recoveredFrames;
            damaged += result.damaged ? 1 : 0;
        }
        return new RecoveryReport(recovered, expired, damaged);
    }

    /**
     * Removes expired segments from every endpoint partition while the service is running.
     * A stale active writer is closed before its segment is removed.
     *
     * @return maintenance counts
     * @throws IOException when the time-series root cannot be scanned
     */
    public MaintenanceReport pruneExpired() throws IOException {
        int deleted = 0;
        int failed = 0;
        List<Path> directories;
        try (var paths = Files.list(root)) {
            directories = paths.filter(Files::isDirectory).sorted().toList();
        }
        for (Path directory : directories) {
            String targetId = directory.getFileName().toString();
            if (!targetId.matches("[0-9a-f]{16}")) {
                continue;
            }
            Object lock = endpointLocks.computeIfAbsent(targetId, ignored -> new Object());
            synchronized (lock) {
                try {
                    WriterState writer = writers.get(targetId);
                    Path activeSegment = writer == null ? null : writer.path;
                    FileTime cutoff = retentionCutoff();
                    if (writer != null && Files.getLastModifiedTime(writer.path).compareTo(cutoff) < 0) {
                        closeWriter(targetId, writer);
                        activeSegment = null;
                    }
                    deleted += prune(targetId, activeSegment);
                } catch (IOException exception) {
                    failed++;
                    System.err.println("WARNING: Unable to prune expired metrics for " + targetId + ": "
                            + exception.getMessage() + ". Background retention will retry later.");
                }
            }
        }
        return new MaintenanceReport(deleted, failed);
    }

    /**
     * Closes the endpoint writer and deletes its exact persisted partition.
     *
     * @param targetId endpoint identifier
     * @throws IOException when files cannot be removed
     */
    public void delete(String targetId) throws IOException {
        validateIdentifier(targetId);
        Object lock = endpointLocks.computeIfAbsent(targetId, ignored -> new Object());
        synchronized (lock) {
            WriterState writer = writers.remove(targetId);
            if (writer != null) {
                writer.channel.close();
            }
            Path directory = endpointDirectory(targetId);
            if (Files.isDirectory(directory)) {
                try (var paths = Files.walk(directory)) {
                    for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                        Files.deleteIfExists(path);
                    }
                }
            }
        }
        endpointLocks.remove(targetId, lock);
    }

    /** Flushes and closes every active segment. */
    @Override
    public void close() {
        for (Map.Entry<String, WriterState> entry : writers.entrySet()) {
            Object lock = endpointLocks.computeIfAbsent(entry.getKey(), ignored -> new Object());
            synchronized (lock) {
                WriterState writer = writers.remove(entry.getKey());
                if (writer == null) {
                    continue;
                }
                try {
                    writer.channel.force(true);
                    writer.channel.close();
                } catch (IOException exception) {
                    System.err.println("Unable to close metric segment " + writer.path + ": "
                            + exception.getMessage());
                }
            }
        }
        writers.clear();
        endpointLocks.clear();
    }

    private WriterState writer(String targetId) throws IOException {
        WriterState existing = writers.get(targetId);
        if (existing != null) {
            return existing;
        }
        synchronized (writers) {
            existing = writers.get(targetId);
            if (existing != null) {
                return existing;
            }
            Path directory = endpointDirectory(targetId);
            Files.createDirectories(directory);
            String name = String.format("%019d-%06d.sbkts", System.currentTimeMillis(),
                    sequence.getAndIncrement() % 1_000_000);
            Path path = directory.resolve(name);
            FileChannel channel = FileChannel.open(path, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
            ByteBuffer header = ByteBuffer.allocate(HEADER_BYTES).putInt(MAGIC).putInt(VERSION);
            header.flip();
            while (header.hasRemaining()) {
                channel.write(header);
            }
            WriterState created = new WriterState(path, channel, HEADER_BYTES);
            writers.put(targetId, created);
            return created;
        }
    }

    private void closeWriter(String targetId, WriterState writer) throws IOException {
        writer.channel.force(true);
        writer.channel.close();
        writers.remove(targetId, writer);
    }

    private void discardFailedWriter(String targetId, WriterState writer, IOException failure) {
        if (writer == null) {
            return;
        }
        writers.remove(targetId, writer);
        try {
            writer.channel.close();
        } catch (IOException closeFailure) {
            failure.addSuppressed(closeFailure);
        }
    }

    private SegmentRecovery recoverSegment(Path segment,
                                            BiConsumer<Long, List<PrometheusTextParser.ParsedMetric>> consumer) {
        int recovered = 0;
        boolean damaged = false;
        try (DataInputStream input = new DataInputStream(new BufferedInputStream(Files.newInputStream(segment)))) {
            if (input.readInt() != MAGIC || input.readInt() != VERSION) {
                warnDataLoss(segment, "invalid header");
                return new SegmentRecovery(0, true);
            }
            while (true) {
                byte[] header = input.readNBytes(FRAME_HEADER_BYTES);
                if (header.length == 0) {
                    break;
                }
                if (header.length != FRAME_HEADER_BYTES) {
                    damaged = true;
                    break;
                }
                ByteBuffer frameHeader = ByteBuffer.wrap(header);
                int length = frameHeader.getInt();
                int expectedCrc = frameHeader.getInt();
                if (length < 1 || length > MAX_FRAME_BYTES) {
                    damaged = true;
                    break;
                }
                byte[] payload = input.readNBytes(length);
                if (payload.length != length) {
                    damaged = true;
                    break;
                }
                CRC32 crc = new CRC32();
                crc.update(payload);
                if ((int) crc.getValue() != expectedCrc) {
                    damaged = true;
                    break;
                }
                DecodedFrame frame = decode(payload);
                consumer.accept(frame.timestamp, frame.metrics);
                recovered++;
            }
        } catch (IOException | IllegalArgumentException exception) {
            damaged = true;
            warnDataLoss(segment, exception.getMessage());
        }
        if (damaged) {
            warnDataLoss(segment, "corrupt or incomplete tail; earlier valid frames were retained");
        }
        return new SegmentRecovery(recovered, damaged);
    }

    private byte[] encode(long timestamp, List<PrometheusTextParser.ParsedMetric> metrics) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            output.writeLong(timestamp);
            output.writeInt(metrics.size());
            for (PrometheusTextParser.ParsedMetric metric : metrics) {
                output.writeUTF(metric.name());
                output.writeInt(metric.labels().size());
                for (Map.Entry<String, String> label : metric.labels().entrySet()) {
                    output.writeUTF(label.getKey());
                    output.writeUTF(label.getValue());
                }
                output.writeDouble(metric.value());
            }
        }
        return bytes.toByteArray();
    }

    private DecodedFrame decode(byte[] payload) throws IOException {
        try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(payload))) {
            long timestamp = input.readLong();
            int count = input.readInt();
            if (count < 0 || count > 1_000_000) {
                throw new IOException("Invalid metric count");
            }
            List<PrometheusTextParser.ParsedMetric> metrics = new ArrayList<>(count);
            for (int index = 0; index < count; index++) {
                String name = input.readUTF();
                int labelCount = input.readInt();
                if (labelCount < 0 || labelCount > 10_000) {
                    throw new IOException("Invalid label count");
                }
                Map<String, String> labels = new java.util.LinkedHashMap<>();
                for (int labelIndex = 0; labelIndex < labelCount; labelIndex++) {
                    labels.put(input.readUTF(), input.readUTF());
                }
                metrics.add(new PrometheusTextParser.ParsedMetric(name, Map.copyOf(labels), input.readDouble()));
            }
            if (input.available() != 0) {
                throw new IOException("Unexpected persistent frame data");
            }
            return new DecodedFrame(timestamp, List.copyOf(metrics));
        }
    }

    private int prune(String targetId, Path activeSegment) throws IOException {
        Path directory = endpointDirectory(targetId);
        if (!Files.isDirectory(directory)) {
            return 0;
        }
        int deleted = 0;
        FileTime cutoff = retentionCutoff();
        for (Path segment : segments(directory)) {
            if (!segment.equals(activeSegment) && Files.getLastModifiedTime(segment).compareTo(cutoff) < 0) {
                if (Files.deleteIfExists(segment)) {
                    deleted++;
                }
            }
        }
        return deleted;
    }

    private FileTime retentionCutoff() {
        return FileTime.from(Instant.ofEpochMilli(System.currentTimeMillis() - retentionMillis));
    }

    private List<Path> segments(Path directory) throws IOException {
        try (var files = Files.list(directory)) {
            return files.filter(path -> path.getFileName().toString().endsWith(".sbkts"))
                    .sorted().toList();
        }
    }

    private Path endpointDirectory(String targetId) {
        return root.resolve(targetId);
    }

    private void validateIdentifier(String targetId) {
        if (targetId == null || !targetId.matches("[0-9a-f]{16}")) {
            throw new IllegalArgumentException("Invalid endpoint identifier");
        }
    }

    private void warnDataLoss(Path segment, String reason) {
        System.err.println("WARNING: Some historical metrics could not be recovered from " + segment + ": "
                + reason + ". Live dashboard operation will continue.");
    }

    /** Summary of a non-fatal endpoint history recovery attempt. */
    public record RecoveryReport(int recoveredFrames, int expiredSegments, int damagedSegments) { }

    /** Summary of one background retention pass. */
    public record MaintenanceReport(int deletedSegments, int failedEndpoints) { }

    private record DecodedFrame(long timestamp, List<PrometheusTextParser.ParsedMetric> metrics) { }

    private record SegmentRecovery(int recoveredFrames, boolean damaged) { }

    private static final class WriterState {
        private final Path path;
        private final FileChannel channel;
        private long size;
        private long lastPruned;
        private final long createdAt;

        private WriterState(Path path, FileChannel channel, long size) {
            this.path = path;
            this.channel = channel;
            this.size = size;
            this.createdAt = System.currentTimeMillis();
            this.lastPruned = createdAt;
        }
    }
}
