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

package io.sbk.dashboard.model;

/** A remote SBK or SBM Prometheus endpoint. */
public record BenchmarkTarget(String id, String name, String host, int port, String metricsPath,
                              BenchmarkKind kind, String createdAt) {
    /**
     * Returns the address syntax expected by Prometheus.
     *
     * @return host and port, with IPv6 hosts enclosed in brackets
     */
    public String prometheusAddress() {
        String formattedHost = host.indexOf(':') >= 0 ? '[' + host + ']' : host;
        return formattedHost + ':' + port;
    }
}
