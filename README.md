# SBK Dashboard

SBK Dashboard is a high-capacity, single-JVM server for observing remote Storage Benchmark Kit (SBK) and Storage
Benchmark Monitor (SBM) processes. It requires no monitoring executables, child processes, containers, or external
services.

The application implements the required monitoring path in Java:

- one independently scheduled collector per registered `host:port`;
- virtual-thread HTTP scraping of SBK/SBM Prometheus exposition endpoints;
- an in-JVM Prometheus text-format parser;
- bounded, primitive-array time-series storage partitioned by endpoint;
- checksummed append-only disk segments with restart recovery and retention;
- live endpoint status based on scrape results;
- JSON query APIs; and
- a dedicated, interactive dashboard for every unique hostname and port combination.

Prometheus Server and Grafana Server themselves are Go applications and cannot be started as Java threads from
Maven libraries. Prometheus Java libraries instrument Java applications, while Grafana clients call an external
Grafana server. This project therefore uses a Java-specific implementation of the required scraping, storage, query,
and visualization behavior rather than disguising operating-system processes as embedded components.

## Requirements

- JDK 25
- Gradle 9.x (the Gradle 9.4 wrapper is included)

## Build and start

```bash
export SBK_JAVA_HOME=/path/to/jdk-25

./gradlew check installDist
./build/install/sbk-dashboard/bin/sbk-dashboard -port 9721 -auth false
```

Both the Gradle wrapper and packaged `sbk-dashboard` launcher select Java in this order:

1. `SBK_JAVA_HOME`
2. `JAVA_HOME`
3. the `java` executable available on `PATH`

JDK 25 is still required. Therefore, a fallback Java older than 25 is detected and rejected with a clear version
error. Every `./gradlew` invocation prints the selected Java source, Java version, and Gradle version before running:

```text
Java source: SBK_JAVA_HOME
Java version: java version "25.0.2" ...
Gradle version: 9.4.0
```

Wrapper versions are not duplicated as literals in the scripts. The required Java version is read from
`javaVersion` in `gradle.properties`, and the Gradle version is derived from `distributionUrl` in
`gradle/wrapper/gradle-wrapper.properties`. The Gradle build uses the same `javaVersion` property for its toolchain,
source compatibility, and target compatibility.

Open <http://localhost:9721>. No other program needs to be installed or started.

Command-line options:

```text
-h                  Show help and exit
-port <port>         Dashboard HTTP port (default: 9721)
-auth <true|false>   Authentication switch (default: false)
-data <directory>    Persistent data directory (default: ~/.sbk-dashboard)
-retention <days>    Persistent retention per endpoint (default: 7)
```

`--data-dir` and `--retention-days` are long aliases for `-data` and `-retention`.

`-auth true` is rejected because authentication is reserved for future development. This avoids falsely indicating
that the service is protected.

At startup, `sbk-dashboard` prints its Java version and home, the supplied command line, and every effective setting
with its source. It also prints complete dashboard links for `localhost`, `127.0.0.1`, and every usable non-loopback
IPv4 or IPv6 address assigned to an active network interface. This makes command-line overrides, environment
settings, defaults, and browser entry points directly visible:

```text
Java version: 25.0.2 (Oracle Corporation)
Java home: /path/to/jdk-25
Supplied arguments: -port 9721 -data /var/lib/sbk-dashboard -retention 7
Dashboard links:
  http://localhost:9721/
  http://127.0.0.1:9721/
  http://192.0.2.10:9721/
Effective configuration:
  port=9721 [command line]
  auth=false [default]
  data=/var/lib/sbk-dashboard [command line]
  retention-days=7 [command line]
  scrape-seconds=5 [default]
  retention-samples=2160 [default]
  segment-size-mb=32 [default]
```

## Add an SBK endpoint

Run SBK with `PrometheusLogger`. Its default exporter is `9718/metrics`:

```bash
/path/to/SBK/build/install/sbk/bin/sbk \
  -class file -file /tmp/sbk.bin \
  -writers 1 -size 4096 -seconds 60 \
  -out PrometheusLogger
```

Register `hostname:9718` in the web UI. For a custom exporter port:

```bash
/path/to/SBK/build/install/sbk/bin/sbk \
  -class file -file /tmp/sbk.bin \
  -writers 1 -size 4096 -seconds 60 \
  -out PrometheusLogger -context 19818/metrics
```

The dedicated dashboard URL returned for a registration is:

```text
http://dashboard-host:9721/dashboard.html?id=<endpoint-id>
```

The endpoint ID is a stable hash of the normalized hostname and port. Consequently, the same host can have several
independent dashboards when different ports are registered, but the same `host:port` cannot be registered twice.

## API

Register an endpoint:

```bash
curl -X POST http://localhost:9721/api/targets \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "NVMe write run",
    "host": "benchmark-01.internal",
    "port": 9718,
    "metricsPath": "/metrics",
    "kind": "SBK"
  }'
```

Other endpoints:

```text
GET    /api/health
GET    /api/targets
DELETE /api/targets/<id>
GET    /api/targets/<id>/dashboard?points=240
```

The dashboard API returns status, collection time, every discovered metric series, labels, current/minimum/maximum
values, and bounded timestamp/value points.

## Thread and memory model

Each registered endpoint owns an independent scheduled collector. A small scheduled executor triggers collectors,
while HTTP operations execute on JDK virtual threads. An atomic running flag prevents overlapping scrapes when a
remote endpoint responds slowly. Connect and request timeouts prevent unavailable systems from consuming threads
indefinitely.

Each unique metric name and label set is stored in a fixed-size primitive `long[]`/`double[]` ring. This avoids an
object allocation for every sample and gives a deterministic memory ceiling:

```text
approximately endpoints × metric-series × retention-samples × 16 bytes
```

The default memory retention is 2,160 samples per series, or three hours at the default five-second interval. API
responses downsample long series to the requested point limit.

Runtime settings:

| Command option | Environment variable | Default | Purpose |
|---|---|---|---|
| `-data`, `--data-dir` | `SBK_DASHBOARD_DATA_DIR` | `~/.sbk-dashboard` | Endpoint registry and time-series directory |
| `-retention`, `--retention-days` | `SBK_DASHBOARD_DISK_RETENTION_DAYS` | `7` | Persistent history retained independently per endpoint |
| — | `SBK_DASHBOARD_SCRAPE_SECONDS` | `5` | Per-endpoint scrape interval |
| — | `SBK_DASHBOARD_RETENTION_SAMPLES` | `2160` | Ring capacity per unique metric series |
| — | `SBK_DASHBOARD_SEGMENT_SIZE_MB` | `32` | Per-endpoint segment rollover size |

For data directory and disk retention, precedence is **command option → environment variable → built-in default**.

The endpoint registry and metric history persist across restarts. Each successful scrape is encoded as one binary
frame with a length and CRC32 checksum, appended to that endpoint's active segment, and synchronized before the
scrape is reported as durable. Segment files live under `$SBK_DASHBOARD_DATA_DIR/timeseries/<endpoint-id>/`.

Startup replays valid retained frames into the bounded memory rings before live scraping begins. An incomplete or
corrupt tail is ignored without losing earlier complete frames. Segments roll at the configured size or after one
hour; old closed segments are deleted according to the per-endpoint retention period during recovery and periodic
maintenance. Removing an endpoint also removes its exact persisted partition.

An independent Java background task runs retention maintenance every hour. It scans every endpoint partition and
deletes expired segments even when that SBK/SBM endpoint is stopped, unreachable, or no longer producing new
samples. If an inactive writer still owns an expired segment, maintenance safely closes it before deletion; a later
scrape automatically creates a fresh segment. Cleanup for one endpoint cannot delete another endpoint's history.

Historical-data problems are non-fatal. An unreadable, truncated, or checksum-damaged segment produces a
`WARNING` on standard error and, when applicable, in the endpoint status detail. Earlier valid frames are retained,
and live scraping continues. If the complete time-series directory cannot be opened, the dashboard still starts and
collects into memory while reporting that persistence is unavailable.

### Retention examples

No retention setting is required for the normal seven-day policy:

```bash
./build/install/sbk-dashboard/bin/sbk-dashboard -port 9721 -auth false
```

Keep 30 days of persistent history for every registered endpoint:

```bash
./build/install/sbk-dashboard/bin/sbk-dashboard \
  -port 9721 -auth false -retention 30
```

Use a dedicated data directory and retain one day, which is useful for short test runs:

```bash
export SBK_DASHBOARD_SEGMENT_SIZE_MB=16
./build/install/sbk-dashboard/bin/sbk-dashboard \
  -port 9721 -auth false \
  -data /var/lib/sbk-dashboard \
  -retention 1
```

Environment-only configuration remains supported:

```bash
export SBK_DASHBOARD_DATA_DIR=/srv/sbk-dashboard
export SBK_DASHBOARD_DISK_RETENTION_DAYS=14
./build/install/sbk-dashboard/bin/sbk-dashboard -port 9721
```

This command overrides both environment values:

```bash
./build/install/sbk-dashboard/bin/sbk-dashboard \
  -port 9721 -data /srv/sbk-dashboard-fast -retention 30
```

Each endpoint has its own directory. For example, two registered addresses, `host-a:9718` and `host-a:9719`, produce
two independently retained partitions:

```text
$SBK_DASHBOARD_DATA_DIR/timeseries/<host-a-9718-endpoint-id>/
$SBK_DASHBOARD_DATA_DIR/timeseries/<host-a-9719-endpoint-id>/
```

After changing the retention value, restart `sbk-dashboard`. Segments older than the new policy are removed during
startup recovery; this is expected retention cleanup and does not prevent the server from starting.

## Dashboard behavior

The dedicated endpoint page includes:

- throughput and records-per-second summaries;
- average, maximum, and tail latency signals;
- active reader/writer/connection state;
- up to twelve live time-series charts selected from performance metrics; and
- a searchable-style complete metric table with labels and observed ranges.

The browser fetches only the registered endpoint's repository partition. Metrics from different hosts or ports
cannot be combined accidentally.

## Verification

```bash
./gradlew check
./gradlew installDist
./build/install/sbk-dashboard/bin/sbk-dashboard -h
```

Tests cover Prometheus exposition parsing, escaped labels, bounded ring retention, endpoint uniqueness, scheduled
scraping from a live HTTP exporter, segment rollover, checksum recovery, truncated tails, startup replay, and
partition deletion. Retention tests also verify background deletion of an expired active segment while the store is
running, preservation of another endpoint's current segment, and successful writing after cleanup.

The implementation has also been tested against the real SBK project at `/root/projects/SBK` with:

```bash
/root/projects/SBK/build/install/sbk/bin/sbk \
  -class file -file /tmp/sbk-dashboard-real-e2e.bin \
  -writers 1 -size 4096 -seconds 15 -throughput 10 \
  -out PrometheusLogger -context 19818/metrics
```

During that run the embedded Java collector discovered 90 metric series and displayed approximately 10 MB/s and
2,560 records/s on the dedicated dashboard.

## Security boundary

Authentication is disabled in phase one. Run the server on a trusted management network or behind an authenticated
reverse proxy. The registration API accepts internal DNS names and IP addresses because polling arbitrary benchmark
hosts is its primary function.
