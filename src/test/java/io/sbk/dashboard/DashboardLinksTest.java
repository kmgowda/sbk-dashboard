/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.InetAddress;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Tests local, IPv4, and IPv6 dashboard link formatting. */
class DashboardLinksTest {
    /** Always prints loopback URLs and includes only usable interface addresses. */
    @Test
    void formatsDashboardLinks() throws Exception {
        List<String> links = DashboardLinks.forAddresses(9721, List.of(
                InetAddress.getByName("127.0.0.1"),
                InetAddress.getByName("192.0.2.10"),
                InetAddress.getByName("2001:db8::10"),
                InetAddress.getByName("224.0.0.1")));

        assertEquals("http://localhost:9721/", links.get(0));
        assertEquals("http://127.0.0.1:9721/", links.get(1));
        assertTrue(links.contains("http://192.0.2.10:9721/"));
        assertTrue(links.stream().anyMatch(link -> link.startsWith("http://[") && link.endsWith("]:9721/")));
        assertFalse(links.stream().anyMatch(link -> link.contains("224.0.0.1")));
    }
}
