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

package io.sbk.dashboard;

import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Discovers local and externally reachable dashboard URLs. */
final class DashboardLinks {
    private DashboardLinks() {
    }

    static List<String> discover(int port) {
        List<InetAddress> addresses = new ArrayList<>();
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces != null && interfaces.hasMoreElements()) {
                NetworkInterface network = interfaces.nextElement();
                if (!network.isUp()) {
                    continue;
                }
                Enumeration<InetAddress> interfaceAddresses = network.getInetAddresses();
                while (interfaceAddresses.hasMoreElements()) {
                    addresses.add(interfaceAddresses.nextElement());
                }
            }
        } catch (SocketException exception) {
            System.err.println("WARNING: Unable to discover network dashboard addresses: "
                    + exception.getMessage() + ". Loopback links remain available.");
        }
        return forAddresses(port, addresses);
    }

    static List<String> forAddresses(int port, List<InetAddress> addresses) {
        Set<String> links = new LinkedHashSet<>();
        links.add("http://localhost:" + port + '/');
        links.add("http://127.0.0.1:" + port + '/');
        addresses.stream()
                .filter(DashboardLinks::isUsableNetworkAddress)
                .map(DashboardLinks::urlHost)
                .sorted(Comparator.naturalOrder())
                .map(host -> "http://" + host + ':' + port + '/')
                .forEach(links::add);
        return List.copyOf(links);
    }

    private static boolean isUsableNetworkAddress(InetAddress address) {
        return !address.isAnyLocalAddress()
                && !address.isLoopbackAddress()
                && !address.isLinkLocalAddress()
                && !address.isMulticastAddress();
    }

    private static String urlHost(InetAddress address) {
        String host = address.getHostAddress().replace("%", "%25");
        return address instanceof Inet6Address ? '[' + host + ']' : host;
    }
}
