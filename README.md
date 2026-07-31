# SBK Dashboard

`sbk-dashboard` is a single Java control server for dedicated SBK/SBM Grafana dashboards. It starts and owns one
native Prometheus server and one native Grafana server, dynamically registers remote PrometheusLogger endpoints,
and provisions one isolated copy of the canonical SBK dashboard for every unique `host:port`.

This phase is deliberately non-containerized. Docker, Podman, Kubernetes, and Compose are not required or used.

## What it does

- Presents a web page where an operator adds a hostname/IP address, port, and metrics path.
- Stores endpoint registrations and deterministic Grafana URL mappings on disk.
- Atomically rewrites Prometheus file-based service discovery whenever an endpoint is added or removed.
- Scrapes each remote SBK/SBM endpoint through managed Prometheus.
- Copies the exact dashboard from
  [`/root/projects/SBK/grafana/dashboards/sbk-dashboard.json`](grafana/dashboards/sbk-dashboard.json).
- Generates one Grafana-provisioned clone per endpoint. Panel layout, visualization settings, datasource UID, and all
  53 canonical panels remain intact; PromQL selectors receive only an endpoint-isolation label.
- Retains time-series data in Prometheus TSDB for 7 days by default. Prometheus performs background retention and
  automatically deletes expired blocks while the server is running.
- Stops managed Prometheus and Grafana processes when `sbk-dashboard` shuts down.

## Architecture

```text
Browser
   |
   +--> sbk-dashboard :9721  (registration UI and API)
           |
           +--> targets.json + dashboard-mappings.json
           +--> managed Prometheus :9090 ---> remote-host:9718/metrics
           |         |
           |         +--> persistent TSDB with time-based retention
           |
           +--> managed Grafana :3000
                     |
                     +--> sbk-<endpoint-id>.json (one per host:port)
```

Prometheus and Grafana are native child processes, not Java threads or Maven libraries. Their server engines do not
exist as embeddable Java APIs. Java owns their configuration, startup, readiness, reconciliation, health, and
shutdown. This preserves the exact Grafana dashboard behavior without containers.

## Requirements

- JDK 25.x
- Gradle 9.x (the wrapper provides Gradle 9.4.0)
- Prometheus 3.x native executable; tested with 3.10.0
- Grafana OSS native installation; tested with 12.4.1

The Gradle wrapper and generated application script select Java in this order:

1. `SBK_JAVA_HOME`
2. `JAVA_HOME`
3. `java` on `PATH`

The wrapper prints the selected Java source, Java version, and Gradle version before every build.

## Install native monitoring servers

Example for Linux x86-64, using the versions validated by this project:

```bash
mkdir -p /opt/sbk-monitoring
cd /opt/sbk-monitoring

curl -fLO https://github.com/prometheus/prometheus/releases/download/v3.10.0/prometheus-3.10.0.linux-amd64.tar.gz
tar -xzf prometheus-3.10.0.linux-amd64.tar.gz

curl -fLO https://dl.grafana.com/grafana/release/12.4.1/grafana_12.4.1_22846628243_linux_amd64.tar.gz
tar -xzf grafana_12.4.1_22846628243_linux_amd64.tar.gz
```

Grafana publishes the SHA-256 checksum
`55d6d71c813dd7426fe0b8d3a237e8d4ee4bf8a806ff90494207e146473ceb41` for that standalone archive.
Use the checksums published with the Prometheus release to verify its archive before installation.

## Build

```bash
export SBK_JAVA_HOME=/path/to/jdk-25
./gradlew clean check installDist
```

The installed command is:

```text
build/install/sbk-dashboard/bin/sbk-dashboard
```

## Start

```bash
build/install/sbk-dashboard/bin/sbk-dashboard \
  -prometheus-bin /opt/sbk-monitoring/prometheus-3.10.0.linux-amd64/prometheus \
  -grafana-home /opt/sbk-monitoring/grafana-12.4.1
```

Defaults:

- SBK Dashboard: `http://localhost:9721/`
- Prometheus: `http://localhost:9090/`
- Grafana: `http://localhost:3000/`
- Authentication: disabled
- Data directory: `~/.sbk-dashboard`
- Prometheus retention: 7 days
- Scrape interval: 5 seconds

At startup, the application prints the Java version, supplied arguments, every effective option and its source,
and full dashboard links for `localhost`, `127.0.0.1`, and discovered non-loopback addresses.

### Production-style example

Use `-grafana-url` for the URL that users' browsers can reach. It may differ from Grafana's local listen address.

```bash
build/install/sbk-dashboard/bin/sbk-dashboard \
  -port 9721 \
  -data /var/lib/sbk-dashboard \
  -retention 14 \
  -prometheus-bin /opt/prometheus/prometheus \
  -prometheus-port 9090 \
  -grafana-home /opt/grafana \
  -grafana-port 3000 \
  -grafana-url http://dashboard.example.com:3000
```

`-auth false` is the only supported authentication setting. Authentication is reserved for future development.

## Command options

```text
-h, --help                    Show help and exit
-port <port>                  Registration server port (default 9721)
-auth <true|false>            Must be false in this release
-data, --data-dir <path>      Persistent data directory
-retention, --retention-days  Prometheus TSDB retention days (default 7)
-prometheus-bin <path>        Prometheus executable (default: prometheus on PATH)
-prometheus-port <port>       Managed Prometheus port (default 9090)
-grafana-home <path>          Grafana installation home (default /usr/share/grafana)
-grafana-port <port>          Managed Grafana port (default 3000)
-grafana-url <url>            Browser-accessible Grafana base URL
```

Command-line values take precedence over environment variables, and environment variables take precedence over
built-in defaults.

| Environment variable | Purpose |
|---|---|
| `SBK_JAVA_HOME` | Preferred JDK home for Gradle and the installed application |
| `JAVA_HOME` | Java fallback when `SBK_JAVA_HOME` is unset |
| `SBK_DASHBOARD_DATA_DIR` | Fallback for `-data` |
| `SBK_DASHBOARD_DISK_RETENTION_DAYS` | Fallback for `-retention` |
| `SBK_DASHBOARD_SCRAPE_SECONDS` | Prometheus scrape interval; default 5 |
| `SBK_DASHBOARD_PROMETHEUS_BIN` | Fallback for `-prometheus-bin` |
| `SBK_DASHBOARD_PROMETHEUS_PORT` | Fallback for `-prometheus-port` |
| `SBK_DASHBOARD_GRAFANA_HOME` | Fallback for `-grafana-home` |
| `SBK_DASHBOARD_GRAFANA_PORT` | Fallback for `-grafana-port` |
| `SBK_DASHBOARD_GRAFANA_URL` | Fallback for `-grafana-url` |

`SBK_DASHBOARD_RETENTION_SAMPLES` and `SBK_DASHBOARD_SEGMENT_SIZE_MB` are not used. Prometheus's disk retention is
the single time-series retention mechanism.

## Run SBK and register it

Start the existing SBK build with `PrometheusLogger`:

```bash
cd /root/projects/SBK
./build/install/sbk/bin/sbk \
  -class file \
  -file /tmp/sbk-dashboard-example.dat \
  -writers 1 \
  -size 4096 \
  -seconds 120 \
  -records 1000 \
  -out PrometheusLogger \
  -context 9718/metrics
```

Open `http://localhost:9721/`, enter:

```text
Host:         127.0.0.1
Port:         9718
Metrics path: /metrics
```

The returned target contains a stable URL such as:

```text
http://localhost:3000/d/sbk-f9720cad2e38eec6/
```

The same host on port `9719` gets a different endpoint ID, dashboard JSON, Prometheus label, mapping, and URL.

### API example

```bash
curl -fsS -X POST http://localhost:9721/api/targets \
  -H 'Content-Type: application/json' \
  --data '{
    "name": "NVMe benchmark",
    "host": "benchmark-01.example",
    "port": 9718,
    "metricsPath": "/metrics"
  }'
```

List targets and their scrape status/dashboard URL:

```bash
curl -fsS http://localhost:9721/api/targets
```

Remove a target:

```bash
curl -i -X DELETE http://localhost:9721/api/targets/<endpoint-id>
```

Removal updates discovery and mappings immediately. Grafana's file provisioner removes the dashboard shortly
afterward.

## Persistent files

For a data directory `/var/lib/sbk-dashboard`:

```text
/var/lib/sbk-dashboard/
├── targets.json
├── dashboard-mappings.json
└── monitoring/
    ├── prometheus/
    │   ├── prometheus.yml
    │   ├── targets.json
    │   └── data/                 # Prometheus TSDB
    ├── grafana/
    │   ├── grafana.ini
    │   ├── data/                 # Grafana SQLite database
    │   ├── provisioning/
    │   └── dashboards/           # sbk-<endpoint-id>.json
    └── logs/
        ├── prometheus.log
        └── grafana.log
```

Registrations and dashboard URLs are deterministic and recover after restart. Prometheus retains historical samples
in its TSDB independently of whether a remote exporter is currently reachable. Missing/corrupt historical blocks
are handled by Prometheus's own recovery behavior; target scrape failures are reported as non-fatal `down` status and
do not prevent `sbk-dashboard` from serving other dashboards.

## Verification

```bash
./gradlew clean check installDist
```

The automated suite covers option precedence, endpoint uniqueness/persistence, canonical dashboard packaging,
endpoint scoping of all PromQL expressions, dynamic Prometheus discovery, dashboard reconciliation, UI inputs, and
runtime link reporting.

The end-to-end validation used:

- JDK 25.0.2 and Gradle 9.4.0
- the existing `/root/projects/SBK` build (SBK 10.4) with `PrometheusLogger`
- Prometheus 3.10.0
- Grafana OSS 12.4.1
- two live SBK endpoints on the same host with different ports

It verified live endpoint-labelled samples in Prometheus, two distinct HTTP-200 Grafana URLs, all 53 panels per
dashboard, endpoint scoping on every SBK PromQL expression, restart recovery, dynamic target updates, mapping
persistence, and dashboard removal.
