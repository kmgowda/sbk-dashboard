# SBK Dashboard

`sbk-dashboard` is a Python 3 control server for dedicated SBK/SBM Grafana dashboards. It owns one native
Prometheus server and one native Grafana server, dynamically registers remote PrometheusLogger endpoints, and
provisions one isolated copy of the canonical SBK dashboard for every unique `host:port`.

This implementation is non-containerized. Prometheus and Grafana are official native child processes—not Python
libraries—and the Python server manages their verified installation, configuration, readiness, reconciliation,
health, and shutdown.

## Features

- Browser UI and JSON API for adding and removing hostname/IP-address plus port endpoints.
- Stable endpoint IDs and Grafana URLs compatible with the earlier Java implementation.
- Exact 53-panel SBK dashboard from `src/sbk_dashboard/resources/grafana/dashboards/sbk-dashboard.json`.
- A dedicated dashboard clone per endpoint, isolated by the `sbk_endpoint_id` Prometheus label.
- Persistent endpoint registry, URL mappings, Prometheus TSDB, and Grafana state.
- Seven-day Prometheus retention by default; Prometheus removes expired TSDB blocks in the background.
- Verified Prometheus and Grafana downloads with live progress when native installations are absent.
- Safe `-continue false` process replacement and `-continue true` attachment.
- Explicit state-machine lifecycle with automatic restart and bounded exponential backoff for owned native services.
- Fixed HTTP worker pool, bounded admission queue, request timeouts, response-size limits, and endpoint-count limits.
- Least-privilege service binding: Prometheus is loopback-only by default, with independent management and Grafana
  bind controls.
- Timestamped, leveled control-plane logging suitable for journald, launchd, and Windows service wrappers.
- Process-group/descendant shutdown and bounded rotating native console logs.
- Linux, macOS, and Windows support on x86-64 and ARM64.
- Standard Python virtual-environment and Conda installation workflows.

## Architecture

```text
Browser
   |
   +--> Python sbk-dashboard :9721  (registration UI and API)
           |
           +--> targets.json + dashboard-mappings.json
           +--> native Prometheus 127.0.0.1:9090 ---> remote-host:9718/metrics
           |         |
           |         +--> persistent TSDB and background retention
           |
           +--> native Grafana :3000
                     |
                     +--> sbk-<endpoint-id>.json (one per host:port)
```

The Python process is the control plane. Metrics ingestion, storage, PromQL, dashboard provisioning, and rendering
remain in the official Prometheus and Grafana servers. See [the architecture document](docs/ARCHITECTURE.md).

## Requirements

- Python 3.10 or newer
- `pip` for a venv installation, or Conda
- Network access on the first run if Prometheus or Grafana is not installed

The only runtime Python dependency is `psutil`, used for cross-platform process and listener ownership checks.

## Install with venv

Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
sbk-dashboard -h
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
sbk-dashboard -h
```

For an editable development installation, use `python -m pip install -e ".[dev]"`.

## Install with Conda

Create the declared environment:

```bash
conda env create -f environment.yml
conda activate sbk-dashboard
sbk-dashboard -h
```

Or install into an existing Conda environment:

```bash
conda create -n sbk-dashboard python=3.12 pip
conda activate sbk-dashboard
python -m pip install .
```

Using `pip` inside the activated Conda environment installs the console command and package resources in that
environment without requiring a system-wide Python installation.

## Start

```bash
sbk-dashboard
```

Defaults:

- Management UI: `http://localhost:9721/`
- Management bind: `0.0.0.0` (all IPv4 interfaces)
- Prometheus: `http://127.0.0.1:9090/` (loopback only)
- Grafana: `http://localhost:3000/`
- Grafana bind: `0.0.0.0` (all IPv4 interfaces)
- Authentication: disabled
- Data directory: `~/.sbk-dashboard`
- Prometheus retention: 7 days
- Scrape interval: 5 seconds
- Existing-process continuation: disabled

Startup prints the Python version and executable, environment type, supplied arguments, selected native platform,
all effective options and their sources, and dashboard links for `localhost`, `127.0.0.1`, and discovered network
addresses.

### Production example

```bash
sbk-dashboard \
  -port 9721 \
  -bind 0.0.0.0 \
  -data /var/lib/sbk-dashboard \
  -retention 14 \
  -prometheus-bin /opt/prometheus/prometheus \
  -prometheus-port 9090 \
  -prometheus-bind 127.0.0.1 \
  -grafana-home /opt/grafana \
  -grafana-port 3000 \
  -grafana-bind 0.0.0.0 \
  -grafana-url http://dashboard.example.com:3000
```

When `-grafana-url` is not supplied, every generated dashboard link follows the hostname or IP address used to open
the main dashboard. For example, opening `http://203.0.113.25:9721/` produces Grafana links beginning with
`http://203.0.113.25:3000/`; `localhost`, `127.0.0.1`, DNS names, and IPv6 addresses behave the same way. This keeps
the main page and its generated dashboard links reachable from the same client machine.

`-grafana-url` remains an explicit override for reverse proxies, TLS termination, DNS aliases, or a Grafana address
whose hostname cannot be derived from the main dashboard request. An explicit value is authoritative and may differ
from Grafana's local listen address.

## Command options

```text
-h, --help                    Show help and exit
-port <port>                  Management HTTP port (default 9721)
-bind <address>               Management bind address (default 0.0.0.0)
-auth <true|false>            Must be false in this release
-continue <true|false>        Reuse healthy existing services (default false)
-data, --data-dir <path>      Persistent data directory
-retention, --retention-days  Prometheus retention days (default 7)
-prometheus-bin <path>        Prometheus executable (PATH, then download)
-prometheus-port <port>       Prometheus port (default 9090)
-prometheus-bind <address>    Prometheus bind address (default 127.0.0.1)
-grafana-home <path>          Grafana home (system path, then download)
-grafana-port <port>          Grafana port (default 3000)
-grafana-bind <address>       Grafana bind address (default 0.0.0.0)
-grafana-url <url>            Browser-accessible Grafana base URL
-log-level <level>            DEBUG, INFO, WARNING, ERROR, or CRITICAL
-monitoring-properties <file> Download URLs, checksums, and install directories
```

Command-line values override environment variables, which override built-in defaults.

| Environment variable | Purpose |
|---|---|
| `SBK_DASHBOARD_DATA_DIR` | Fallback for `-data` |
| `SBK_DASHBOARD_BIND` | Fallback for `-bind`; default `0.0.0.0` |
| `SBK_DASHBOARD_DISK_RETENTION_DAYS` | Fallback for `-retention` |
| `SBK_DASHBOARD_SCRAPE_SECONDS` | Prometheus scrape interval; default 5 |
| `SBK_DASHBOARD_PROMETHEUS_BIN` | Fallback for `-prometheus-bin` |
| `SBK_DASHBOARD_PROMETHEUS_PORT` | Fallback for `-prometheus-port` |
| `SBK_DASHBOARD_PROMETHEUS_BIND` | Fallback for `-prometheus-bind`; default `127.0.0.1` |
| `SBK_DASHBOARD_GRAFANA_HOME` | Fallback for `-grafana-home` |
| `SBK_DASHBOARD_GRAFANA_PORT` | Fallback for `-grafana-port` |
| `SBK_DASHBOARD_GRAFANA_BIND` | Fallback for `-grafana-bind`; default `0.0.0.0` |
| `SBK_DASHBOARD_GRAFANA_URL` | Fallback for `-grafana-url` |
| `SBK_DASHBOARD_LOG_LEVEL` | Fallback for `-log-level`; default `INFO` |
| `SBK_DASHBOARD_MONITORING_PROPERTIES` | External download properties file |
| `SBK_DASHBOARD_HTTP_WORKERS` | Fixed management HTTP workers; default 8, maximum 128 |
| `SBK_DASHBOARD_HTTP_QUEUE` | Queued HTTP requests beyond active workers; default 64 |
| `SBK_DASHBOARD_REQUEST_TIMEOUT_SECONDS` | Per-client socket timeout; default 15 |
| `SBK_DASHBOARD_HEALTH_RESPONSE_MB` | Maximum Prometheus target-health response; default 4 MiB |
| `SBK_DASHBOARD_SUPERVISOR_SECONDS` | Native health and restart interval; default 5 |
| `SBK_DASHBOARD_PROCESS_LOG_MB` | Maximum bytes per native console log generation; default 10 MiB |
| `SBK_DASHBOARD_PROCESS_LOG_BACKUPS` | Rotated native console log generations; default 3 |
| `SBK_DASHBOARD_MAX_TARGETS` | Persisted endpoint limit; default 10,000 |
| `SBK_DASHBOARD_TARGET_HEALTH_TIMEOUT_SECONDS` | Prometheus target-status request timeout; default 4 |
| `SBK_DASHBOARD_PROMETHEUS_STARTUP_TIMEOUT_SECONDS` | Prometheus readiness deadline; default 45 |
| `SBK_DASHBOARD_GRAFANA_STARTUP_TIMEOUT_SECONDS` | Grafana readiness deadline; default 120 |

Bind values accept IP literals (including IPv6) or conservative DNS names. Keep Prometheus on its loopback default
unless direct remote Prometheus access is an explicit requirement. Authentication is disabled, so expose management
and Grafana only on trusted networks or through a secured reverse proxy.

`SBK_JAVA_HOME`, `JAVA_HOME`, `SBK_DASHBOARD_RETENTION_SAMPLES`, and `SBK_DASHBOARD_SEGMENT_SIZE_MB` are not used by
the Python implementation. Prometheus time-based retention is the only sample-retention mechanism.

## Automatic native installation

The packaged `monitoring-download.properties` contains pinned Prometheus and Grafana URLs, checksums, archive
layouts, and executable names for:

- `linux-x86_64` and `linux-arm64`
- `macos-x86_64` and `macos-arm64`
- `windows-x86_64` and `windows-arm64`

Missing tools are downloaded to `${data.directory}/downloads`, checksum-verified, safely extracted, and installed
under `${data.directory}/tools`. Cached verified archives are reused. TAR.GZ and ZIP traversal, links, and special
entries are rejected.

Override only the required values in an external file:

```properties
download.directory=/srv/sbk-dashboard/downloads
install.directory=/srv/sbk-dashboard/tools
prometheus.download.url=https://mirror.example/prometheus.tar.gz
prometheus.download.file=prometheus.tar.gz
prometheus.download.sha256=<64 lowercase hexadecimal characters>
prometheus.archive.directory=prometheus-version-platform
prometheus.executable=prometheus
prometheus.archive.format=tar.gz
```

Pass it using `-monitoring-properties /path/to/monitoring-download.properties`. Platform-qualified values such as
`prometheus.windows-x86_64.download.url` are also supported. Unspecified values retain packaged defaults.

## Existing-process behavior

By default, `-continue false` verifies the owners of the configured Prometheus and Grafana ports before stopping
anything. It stops only executables named `prometheus`, `grafana`, or `grafana-server`; an unrelated or unidentified
listener fails startup safely.

Use `-continue true` to attach to healthy compatible services already on the configured ports:

```bash
sbk-dashboard -continue true
```

Attached services are not stopped at dashboard shutdown. They must already use configuration compatible with this
data directory's Prometheus discovery and Grafana provisioning paths.

Owned services are supervised every five seconds. An exited child is restarted immediately; a running service that
fails three consecutive health probes is replaced. Repeated launch failures use exponential backoff capped at 60
seconds, preventing a crash loop from consuming CPU or filling logs. Attached `-continue true` services are observed
but never restarted or terminated because sbk-dashboard does not own them.

Prometheus has a 45-second startup deadline and Grafana has a 120-second deadline by default. The separate values
avoid spurious Grafana failures on slower hosts while keeping Prometheus failure detection prompt.

## Production resource and lifecycle controls

- The management server has eight workers and a queue of 64 by default. Excess requests receive HTTP 503 instead of
  allocating more threads or unbounded queued futures.
- Client sockets time out, JSON requests are limited to 64 KiB, and Prometheus health responses default to 4 MiB.
- Registrations and status maps cannot exceed `SBK_DASHBOARD_MAX_TARGETS`.
- Registration/configuration replacements synchronize both file contents and their parent directory on POSIX so an
  atomic rename is durable across a crash. Windows retains atomic replacement with native filesystem semantics.
- Prometheus and Grafana console output is continuously drained in 64 KiB chunks and rotated at 10 MiB with three
  backups by default. No subprocess output pipe is allowed to accumulate in memory.
- Every stack, HTTP server, and native component has validated `new`, `starting`, `running`, `stopping`, `stopped`,
  and `failed` states. Shutdown is idempotent and reports incomplete child termination.
- Owned POSIX services start in dedicated sessions/process groups. Shutdown addresses the group and recorded
  descendants; Windows uses a new process group plus recursive process-tree termination.
- A single supervisor thread manages both native components and target health. HTTP worker threads are fixed and are
  joined at shutdown; subprocess log-pump threads exit at EOF and are joined.

For unattended 24/7 use, run `sbk-dashboard` under the host service manager—such as systemd, launchd, or Windows
Service Control Manager—with automatic restart enabled for failure of the Python control process itself. The internal
supervisor covers Prometheus and Grafana failures; the host service manager covers VM events and control-plane
failure.

## Run SBK and register it

Start SBK with `PrometheusLogger`:

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

Open `http://localhost:9721/` and add host `127.0.0.1`, port `9718`, path `/metrics`. The returned dashboard URL is
similar to `http://localhost:3000/d/sbk-f9720cad2e38eec6/`. Registering the same host on port `9719` produces a
different endpoint ID, target label, dashboard JSON, mapping, and URL.

### API

```bash
curl -fsS -X POST http://localhost:9721/api/targets \
  -H 'Content-Type: application/json' \
  --data '{"name":"NVMe benchmark","host":"benchmark-01.example","port":9718,"metricsPath":"/metrics"}'

curl -fsS http://localhost:9721/api/targets
curl -i -X DELETE http://localhost:9721/api/targets/<endpoint-id>
```

## Persistent files

```text
~/.sbk-dashboard/
├── downloads/                     # verified native archives
├── tools/                         # installed native distributions
├── targets.json                   # endpoint registry
├── dashboard-mappings.json        # stable target-to-URL mappings
└── monitoring/
    ├── managed-processes.json     # validated managed-child identity
    ├── prometheus/
    │   ├── prometheus.yml
    │   ├── targets.json
    │   └── data/                  # persistent Prometheus TSDB
    ├── grafana/
    │   ├── grafana.ini
    │   ├── data/
    │   ├── provisioning/
    │   └── dashboards/            # sbk-<endpoint-id>.json
    └── logs/
        ├── prometheus.log[.1-.3]
        └── grafana-console.log[.1-.3]
```

Existing Java-created `targets.json`, monitoring data, and dashboard mappings remain compatible, so the same data
directory can be reused after upgrading.

## Build and test

Software agents and automated coding tools should begin with [`AGENTS.md`](AGENTS.md). The detailed code map,
runtime flows, change recipes, debugging guidance, validation layers, and review priorities are in
[`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md). Tool-specific discovery files delegate to those canonical documents so
Codex, Devin, Windsurf, Cursor, Copilot, Claude, Gemini, and other agents follow the same engineering contract.

```bash
python -m pip install -e ".[dev]"
ruff check src tests
mypy src
python -m pytest
coverage run -m pytest
coverage report
python -m build --no-isolation
```

Tests cover lifecycle transitions, bounded HTTP admission, process restart/tree shutdown, resource-leak warnings,
log rotation, configuration precedence, all six native platform definitions, safe TAR.GZ and ZIP extraction,
endpoint persistence and compatibility, dashboard cloning and complete PromQL scoping, discovery generation,
management APIs, health attachment, process ownership, and package resources. Native Linux validation is described
in [testing documentation](docs/TESTING.md).
