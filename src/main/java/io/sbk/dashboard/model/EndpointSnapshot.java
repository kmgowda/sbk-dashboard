/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.model;

import java.util.List;

/** Complete dashboard data for one registered endpoint. */
public record EndpointSnapshot(String targetId, long collectedAt, TargetStatus status,
                               List<MetricSeries> series) { }
