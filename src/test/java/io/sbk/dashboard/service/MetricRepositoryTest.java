/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Tests bounded in-memory time-series behavior. */
class MetricRepositoryTest {
    /** Retains only capacity samples and returns summary values. */
    @Test
    void retainsBoundedSeries() {
        MetricRepository repository = new MetricRepository(3);
        for (int value = 1; value <= 5; value++) {
            repository.append("target", value,
                    List.of(new PrometheusTextParser.ParsedMetric("SBK_Writing_MBPerSec", Map.of(), value)));
        }

        var series = repository.snapshot("target", 100).series().getFirst();
        assertEquals(3, series.points().size());
        assertEquals(3.0, series.minimum());
        assertEquals(5.0, series.maximum());
        assertEquals(5.0, series.current());
    }
}
