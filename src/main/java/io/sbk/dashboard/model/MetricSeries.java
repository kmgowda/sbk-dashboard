/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.model;

import java.util.List;
import java.util.Map;

/** Dashboard-ready metric series and summary statistics. */
public record MetricSeries(String key, String name, Map<String, String> labels, double current,
                           double minimum, double maximum, List<MetricPoint> points) { }
