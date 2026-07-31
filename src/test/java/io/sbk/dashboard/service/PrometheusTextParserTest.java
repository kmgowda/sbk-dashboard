/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.service;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/** Tests Prometheus exposition parsing using SBK-shaped metrics. */
class PrometheusTextParserTest {
    /** Parses labels, counters, gauges, timestamps, escapes, and special values. */
    @Test
    void parsesPrometheusText() {
        String text = "# HELP SBK_Writing_MBPerSec throughput\n"
                + "SBK_Writing_MBPerSec{class=\"File\",action=\"Writing\"} 128.5\n"
                + "SBK_Writers 8 123456\n"
                + "escaped{value=\"a\\\\b\\\"c\"} +Inf\n"
                + "malformed line nope\n";

        var metrics = new PrometheusTextParser().parse(text);

        assertEquals(3, metrics.size());
        assertEquals("SBK_Writing_MBPerSec", metrics.getFirst().name());
        assertEquals("File", metrics.getFirst().labels().get("class"));
        assertEquals(128.5, metrics.getFirst().value());
        assertEquals(8.0, metrics.get(1).value());
        assertEquals(Double.POSITIVE_INFINITY, metrics.get(2).value());
    }
}
