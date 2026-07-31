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

import io.sbk.dashboard.model.EndpointSnapshot;
import io.sbk.dashboard.model.MetricPoint;
import io.sbk.dashboard.model.MetricSeries;
import io.sbk.dashboard.model.TargetStatus;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/** Bounded, allocation-conscious in-JVM time-series repository partitioned by endpoint. */
public final class MetricRepository {
    private final int capacity;
    private final Map<String, EndpointData> endpoints = new ConcurrentHashMap<>();

    /**
     * Creates a repository.
     *
     * @param capacity maximum samples retained per unique metric series
     */
    public MetricRepository(int capacity) {
        this.capacity = capacity;
    }

    /**
     * Appends one complete scrape.
     *
     * @param targetId endpoint identifier
     * @param timestamp scrape completion epoch milliseconds
     * @param metrics parsed samples
     */
    public void append(String targetId, long timestamp, List<PrometheusTextParser.ParsedMetric> metrics) {
        EndpointData endpoint = endpoints.computeIfAbsent(targetId, ignored -> new EndpointData());
        for (PrometheusTextParser.ParsedMetric metric : metrics) {
            String key = key(metric.name(), metric.labels());
            endpoint.series.computeIfAbsent(key,
                    ignored -> new StoredSeries(metric.name(), metric.labels(), capacity))
                    .append(timestamp, metric.value());
        }
        endpoint.collectedAt = timestamp;
    }

    /**
     * Updates endpoint collection status.
     *
     * @param targetId endpoint identifier
     * @param status latest status
     */
    public void status(String targetId, TargetStatus status) {
        endpoints.computeIfAbsent(targetId, ignored -> new EndpointData()).status = status;
    }

    /**
     * Returns dashboard data for one endpoint.
     *
     * @param targetId endpoint identifier
     * @param maxPoints maximum points returned per series
     * @return immutable endpoint snapshot
     */
    public EndpointSnapshot snapshot(String targetId, int maxPoints) {
        EndpointData endpoint = endpoints.get(targetId);
        if (endpoint == null) {
            return new EndpointSnapshot(targetId, 0, TargetStatus.pending(), List.of());
        }
        List<MetricSeries> series = endpoint.series.entrySet().stream()
                .map(entry -> entry.getValue().snapshot(entry.getKey(), maxPoints))
                .sorted(Comparator.comparingInt((MetricSeries item) -> priority(item.name()))
                        .thenComparing(MetricSeries::key))
                .toList();
        return new EndpointSnapshot(targetId, endpoint.collectedAt, endpoint.status, series);
    }

    /**
     * Returns the current endpoint state.
     *
     * @param targetId endpoint identifier
     * @return latest status
     */
    public TargetStatus status(String targetId) {
        EndpointData endpoint = endpoints.get(targetId);
        return endpoint == null ? TargetStatus.pending() : endpoint.status;
    }

    /**
     * Removes all retained data for an endpoint.
     *
     * @param targetId endpoint identifier
     */
    public void remove(String targetId) {
        endpoints.remove(targetId);
    }

    private String key(String name, Map<String, String> labels) {
        if (labels.isEmpty()) {
            return name;
        }
        return name + labels.entrySet().stream().sorted(Map.Entry.comparingByKey())
                .map(entry -> entry.getKey() + '=' + entry.getValue())
                .collect(Collectors.joining(",", "{", "}"));
    }

    private int priority(String name) {
        if (name.contains("MBPerSec")) {
            return 0;
        }
        if (name.contains("RecordsPerSec")) {
            return 1;
        }
        if (name.contains("AvgLatency") || name.contains("MaxLatency") || name.matches(".*_(99|99_9)$")) {
            return 2;
        }
        if (name.endsWith("Writers") || name.endsWith("Readers") || name.endsWith("Connections")) {
            return 3;
        }
        return 4;
    }

    private static final class EndpointData {
        private final Map<String, StoredSeries> series = new ConcurrentHashMap<>();
        private volatile long collectedAt;
        private volatile TargetStatus status = TargetStatus.pending();
    }

    private static final class StoredSeries {
        private final String name;
        private final Map<String, String> labels;
        private final long[] timestamps;
        private final double[] values;
        private int start;
        private int size;

        private StoredSeries(String name, Map<String, String> labels, int capacity) {
            this.name = name;
            this.labels = Map.copyOf(labels);
            this.timestamps = new long[capacity];
            this.values = new double[capacity];
        }

        private synchronized void append(long timestamp, double value) {
            int index = (start + size) % values.length;
            if (size == values.length) {
                index = start;
                start = (start + 1) % values.length;
            } else {
                size++;
            }
            timestamps[index] = timestamp;
            values[index] = value;
        }

        private synchronized MetricSeries snapshot(String key, int maxPoints) {
            int stride = Math.max(1, (int) Math.ceil((double) size / maxPoints));
            List<MetricPoint> points = new ArrayList<>(Math.min(size, maxPoints));
            double minimum = Double.POSITIVE_INFINITY;
            double maximum = Double.NEGATIVE_INFINITY;
            for (int offset = 0; offset < size; offset++) {
                int index = (start + offset) % values.length;
                double value = values[index];
                if (Double.isFinite(value)) {
                    minimum = Math.min(minimum, value);
                    maximum = Math.max(maximum, value);
                }
                if (offset % stride == 0 || offset == size - 1) {
                    points.add(new MetricPoint(timestamps[index], value));
                }
            }
            double current = size == 0 ? Double.NaN : values[(start + size - 1) % values.length];
            if (minimum == Double.POSITIVE_INFINITY) {
                minimum = Double.NaN;
                maximum = Double.NaN;
            }
            return new MetricSeries(key, name, labels, current, minimum, maximum, List.copyOf(points));
        }
    }
}
