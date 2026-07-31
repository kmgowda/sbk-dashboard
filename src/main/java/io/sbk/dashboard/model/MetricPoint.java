/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.model;

/** One timestamped metric value. */
public record MetricPoint(long timestamp, double value) { }
