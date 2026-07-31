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

import io.sbk.dashboard.model.BenchmarkTarget;
import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/** Atomically publishes dynamic Prometheus file-service-discovery targets. */
public final class PrometheusTargetDiscovery {
    private final Path targetFile;

    /** Creates a discovery writer for the configured watched file. */
    public PrometheusTargetDiscovery(Path targetFile) {
        this.targetFile = targetFile;
    }

    /** Writes a complete immutable target snapshot. */
    public synchronized void write(List<BenchmarkTarget> targets) throws IOException {
        List<TargetGroup> groups = targets.stream()
                .map(target -> new TargetGroup(List.of(target.prometheusAddress()), Map.of(
                        "sbk_endpoint_id", target.id(),
                        "sbk_metrics_path", target.metricsPath())))
                .toList();
        byte[] json = JsonSupport.mapper().writerWithDefaultPrettyPrinter().writeValueAsBytes(groups);
        AtomicFiles.write(targetFile, json);
    }

    private record TargetGroup(List<String> targets, Map<String, String> labels) { }
}
