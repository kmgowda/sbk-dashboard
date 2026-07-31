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

import io.sbk.dashboard.config.DashboardConfig;
import io.sbk.dashboard.config.MonitoringConfig;
import io.sbk.dashboard.service.ManagedMonitoringStack;
import io.sbk.dashboard.service.TargetRegistry;
import io.sbk.dashboard.web.DashboardHttpServer;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import org.apache.commons.cli.CommandLine;
import org.apache.commons.cli.DefaultParser;
import org.apache.commons.cli.Option;
import org.apache.commons.cli.Options;
import org.apache.commons.cli.ParseException;
import org.apache.commons.cli.help.HelpFormatter;

/** Command-line entry point for the SBK dashboard server. */
public final class SbkDashboardMain {
    private SbkDashboardMain() {
    }

    /**
     * Parses options and runs until the process receives a shutdown signal.
     *
     * @param args command-line arguments
     */
    public static void main(String[] args) {
        printRuntime(args);
        Options options = options();
        try {
            CommandLine command = new DefaultParser().parse(options, args);
            if (command.hasOption("h")) {
                printHelp(options);
                return;
            }
            int port = parsePort(command.getOptionValue("port", Integer.toString(DashboardConfig.DEFAULT_PORT)));
            boolean authentication = parseBoolean(command.getOptionValue("auth", "false"));
            if (authentication) {
                throw new IllegalArgumentException("Authentication is reserved for a future release; use -auth false");
            }
            DashboardConfig dashboardConfig = DashboardConfig.fromOptions(port, false,
                    command.getOptionValue("data"), command.getOptionValue("retention"));
            MonitoringConfig monitoringConfig = MonitoringConfig.fromOptions(
                    command.getOptionValue("prometheus-bin"), command.getOptionValue("grafana-home"),
                    command.getOptionValue("prometheus-port"), command.getOptionValue("grafana-port"),
                    command.getOptionValue("grafana-url"));
            run(dashboardConfig, monitoringConfig, command);
        } catch (ParseException | IllegalArgumentException exception) {
            System.err.println("Error: " + exception.getMessage());
            printHelp(options);
            System.exit(2);
        } catch (IOException exception) {
            System.err.println("Unable to start sbk-dashboard: " + exception.getMessage());
            System.exit(1);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }

    private static void run(DashboardConfig config, MonitoringConfig monitoringConfig,
                            CommandLine command) throws IOException, InterruptedException {
        TargetRegistry registry = new TargetRegistry(config);
        ManagedMonitoringStack monitoring = new ManagedMonitoringStack(config, monitoringConfig, registry.list());
        DashboardHttpServer server;
        try {
            server = new DashboardHttpServer(config, registry, monitoring);
        } catch (IOException | RuntimeException exception) {
            monitoring.close();
            throw exception;
        }
        CountDownLatch stopped = new CountDownLatch(1);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.close();
            monitoring.close();
            stopped.countDown();
        }, "sbk-dashboard-shutdown"));
        server.start();
        System.out.println("SBK Dashboard listening on all interfaces, port " + config.port());
        System.out.println("Dashboard links:");
        DashboardLinks.discover(config.port()).forEach(link -> System.out.println("  " + link));
        System.out.println("Authentication: disabled");
        System.out.println("Metrics engine: managed Prometheus at http://127.0.0.1:"
                + monitoringConfig.prometheusPort());
        System.out.println("Grafana: " + monitoringConfig.grafanaPublicUrl());
        System.out.println("Data directory: " + config.dataDirectory());
        System.out.println("Persistent history retention: " + config.diskRetentionDays() + " day(s) per endpoint");
        printEffectiveOptions(config, monitoringConfig, command);
        stopped.await();
    }

    private static void printRuntime(String[] args) {
        System.out.println("Java version: " + System.getProperty("java.version") + " ("
                + System.getProperty("java.vendor") + ")");
        System.out.println("Java home: " + System.getProperty("java.home"));
        System.out.println("Supplied arguments: " + (args.length == 0 ? "(none)" : String.join(" ", args)));
    }

    private static void printEffectiveOptions(DashboardConfig config, MonitoringConfig monitoringConfig,
                                              CommandLine command) {
        System.out.println("Effective configuration:");
        printOption("port", config.port(), command.hasOption("port") ? "command line" : "default");
        printOption("auth", config.authenticationEnabled(), command.hasOption("auth") ? "command line" : "default");
        printOption("data", config.dataDirectory(), source(command, "data", "SBK_DASHBOARD_DATA_DIR"));
        printOption("retention-days", config.diskRetentionDays(),
                source(command, "retention", "SBK_DASHBOARD_DISK_RETENTION_DAYS"));
        printOption("scrape-seconds", config.scrapeIntervalSeconds(),
                environmentSource("SBK_DASHBOARD_SCRAPE_SECONDS"));
        printOption("prometheus-bin", monitoringConfig.prometheusBinary(),
                source(command, "prometheus-bin", "SBK_DASHBOARD_PROMETHEUS_BIN"));
        printOption("prometheus-port", monitoringConfig.prometheusPort(),
                source(command, "prometheus-port", "SBK_DASHBOARD_PROMETHEUS_PORT"));
        printOption("grafana-home", monitoringConfig.grafanaHome(),
                source(command, "grafana-home", "SBK_DASHBOARD_GRAFANA_HOME"));
        printOption("grafana-port", monitoringConfig.grafanaPort(),
                source(command, "grafana-port", "SBK_DASHBOARD_GRAFANA_PORT"));
        printOption("grafana-url", monitoringConfig.grafanaPublicUrl(),
                source(command, "grafana-url", "SBK_DASHBOARD_GRAFANA_URL"));
    }

    private static String source(CommandLine command, String option, String environment) {
        return command.hasOption(option) ? "command line" : environmentSource(environment);
    }

    private static String environmentSource(String name) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? "default" : "environment " + name;
    }

    private static void printOption(String name, Object value, String source) {
        System.out.println("  " + name + "=" + value + " [" + source + "]");
    }

    private static Options options() {
        Options options = new Options();
        options.addOption(Option.builder("h").longOpt("help").desc("Show this help and exit").get());
        options.addOption(Option.builder("port").hasArg().argName("port")
                .desc("Dashboard HTTP port (default: " + DashboardConfig.DEFAULT_PORT + ')').get());
        options.addOption(Option.builder("auth").hasArg().argName("true|false")
                .desc("Authentication switch; false only (default: false, true reserved for future development)")
                .get());
        options.addOption(Option.builder("data").longOpt("data-dir").hasArg().argName("directory")
                .desc("Persistent data directory (environment: SBK_DASHBOARD_DATA_DIR; default: ~/.sbk-dashboard)")
                .get());
        options.addOption(Option.builder("retention").longOpt("retention-days").hasArg().argName("days")
                .desc("Persistent retention per endpoint (environment: SBK_DASHBOARD_DISK_RETENTION_DAYS; "
                        + "default: " + DashboardConfig.DEFAULT_DISK_RETENTION_DAYS + " days)")
                .get());
        options.addOption(Option.builder("prometheus-bin").hasArg().argName("path")
                .desc("Prometheus executable (environment: SBK_DASHBOARD_PROMETHEUS_BIN; default: PATH)").get());
        options.addOption(Option.builder("grafana-home").hasArg().argName("directory")
                .desc("Grafana installation home (environment: SBK_DASHBOARD_GRAFANA_HOME; "
                        + "default: /usr/share/grafana)").get());
        options.addOption(Option.builder("prometheus-port").hasArg().argName("port")
                .desc("Managed Prometheus port (default: " + MonitoringConfig.DEFAULT_PROMETHEUS_PORT + ')').get());
        options.addOption(Option.builder("grafana-port").hasArg().argName("port")
                .desc("Managed Grafana port (default: " + MonitoringConfig.DEFAULT_GRAFANA_PORT + ')').get());
        options.addOption(Option.builder("grafana-url").hasArg().argName("url")
                .desc("Public Grafana base URL (environment: SBK_DASHBOARD_GRAFANA_URL)").get());
        return options;
    }

    private static int parsePort(String value) {
        try {
            int port = Integer.parseInt(value);
            if (port < 1 || port > 65_535) {
                throw new IllegalArgumentException("Port must be between 1 and 65535");
            }
            return port;
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("Port must be a number", exception);
        }
    }

    private static boolean parseBoolean(String value) {
        if (value.equalsIgnoreCase("true")) {
            return true;
        }
        if (value.equalsIgnoreCase("false")) {
            return false;
        }
        throw new IllegalArgumentException("-auth must be true or false");
    }

    private static void printHelp(Options options) {
        try {
            HelpFormatter.builder().get().printHelp("sbk-dashboard",
                    "SBK/SBM dashboard control server with managed Prometheus and Grafana", options,
                    "Prometheus and Grafana run as child processes; Docker is not required.", true);
        } catch (IOException exception) {
            throw new IllegalStateException("Unable to print command help", exception);
        }
    }
}
