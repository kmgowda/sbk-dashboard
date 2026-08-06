# Software Agent Guide

This guide gives software agents enough context to make safe, reviewable changes without rediscovering the project
on every task. The normative rules are in [`AGENTS.md`](../AGENTS.md); this document explains how to apply them.
Use [`USAGE.md`](USAGE.md) for operator workflows, [`ARCHITECTURE.md`](ARCHITECTURE.md) for design decisions, and
[`INTERNALS.md`](INTERNALS.md) for module-level ownership and call paths.

## Mental model

Treat `sbk-dashboard` as a lifecycle and configuration control plane, not as the metrics data plane:

```text
browser
   |
   v
bounded Python HTTP/API server
   |-- persistent endpoint registry
   |-- dynamic Prometheus file discovery
   |-- endpoint-scoped copies of the canonical Grafana dashboard
   `-- lifecycle facade/supervisor
          |-- owned Prometheus guardian --> native process --> remote SBK/SBM /metrics
          |-- owned Grafana guardian    --> native process --> Prometheus datasource
          `-- attached native services are observed without guardians
```

The Python process must stay small and predictable. Prometheus owns scraping/TSDB retention, and Grafana owns query
and rendering behavior. Endpoint isolation is achieved with labels and scoped PromQL, not with one native stack per
endpoint.

`processes.py` owns lifecycle state, process groups, trees, and bounded native logs. The separate `guardian.py`
entry point is the lightweight child-process parent-death monitor; keep it independently runnable because it must
clean native descendants even after the control plane is forcefully terminated.

## Request and persistence flow

### Startup

1. `main.main()` parses configuration and reports every effective value and source.
2. `NativeToolBootstrap` resolves installed tools or downloads verified platform archives.
3. `TargetRegistry` loads registrations with backward-compatible validation and endpoint limits.
4. `ManagedMonitoringStack.start()` writes configuration, reconciles targets/dashboards, applies continuation or
   replacement policy, starts Prometheus then Grafana, and starts one supervisor.
5. `DashboardHttpServer.start()` begins bounded API/UI admission only after the monitoring stack is ready.
6. Signal handling closes HTTP admission first and then the monitoring stack.

### Target registration

1. `POST /api/targets` validates name, host, port, and metrics path.
2. `TargetRegistry.register()` creates or returns the stable endpoint registration and persists it atomically.
3. `ManagedMonitoringStack.reconcile()` atomically rewrites Prometheus discovery, clones/scopes dashboards, writes
   mappings, and publishes pending status.
4. Prometheus file discovery notices the target without a process restart.
5. The supervisor maps Prometheus target health back to the endpoint ID.
6. API rendering constructs a client-reachable Grafana URL. The URL is not part of endpoint identity.

### Restart and recovery

- Registrations reload from the configured data directory.
- Generated discovery and dashboards are reconstructed deterministically.
- Prometheus reopens its TSDB and applies time retention.
- Grafana reopens its state database and reprovisions dashboard files.
- A down exporter changes endpoint status but does not prevent startup or erase historical data.

## Runtime data layout

The default data root is `~/.sbk-dashboard`; tests must override it:

```text
<data>/
|-- targets.json
|-- dashboard-mappings.json
|-- downloads/
|-- tools/
`-- monitoring/
    |-- managed-processes.json
    |-- prometheus/
    |   |-- prometheus.yml
    |   |-- targets.json
    |   `-- data/                 # Prometheus TSDB
    |-- grafana/
    |   |-- grafana.ini
    |   |-- data/                 # Grafana state/database
    |   |-- provisioning/
    |   `-- dashboards/           # generated sbk-<id>.json files
    `-- logs/                      # bounded native console logs
```

These files are runtime state, not fixtures. Never inspect or modify a production data root as part of a coding
task unless the user explicitly requests a recovery operation.

## Public API

| Method/path | Purpose |
|---|---|
| `GET /` | Management UI |
| `GET /api/health` | Control-plane/native health summary |
| `GET /api/targets` | List registrations with live status and request-reachable dashboard URL |
| `POST /api/targets` | Register an endpoint and reconcile monitoring configuration |
| `GET /api/targets/<id>/dashboard` | Resolve the dedicated dashboard URL |
| `DELETE /api/targets/<id>` | Remove registration and generated dashboard/discovery entry |

Request bodies are JSON and capped at 64 KiB. Authentication is absent, so new mutating endpoints must not imply
that the service is safe for untrusted public exposure.

## Configuration precedence

The general precedence is command line, then environment variable, then built-in default. `DashboardConfig.sources`
and `MonitoringConfig.sources` preserve where values came from, and startup output is part of the operational
contract. Do not read environment variables ad hoc in service classes; centralize selection and validation in
`config.py`.

Important defaults:

| Setting | Default |
|---|---|
| Management port | 9721 |
| Management bind | 0.0.0.0 |
| Prometheus port | 9090 |
| Prometheus bind | 127.0.0.1 |
| Grafana port | 3000 |
| Grafana bind | 0.0.0.0 |
| Authentication | false |
| Continue existing processes | false |
| Retention | 7 days |
| Scrape interval | 5 seconds |
| HTTP workers / queue | 8 / 64 |
| Client timeout | 15 seconds |
| Max endpoints | 10,000 |
| Native log generation/backups | 10 MiB / 3 |
| Periodic short status | 60 seconds |
| Target-health timeout | 4 seconds |
| Prometheus/Grafana startup | 45 / 120 seconds |
| Endpoint-form host | `127.0.0.1` natively; `host.docker.internal` in the image |

See `README.md` and `config.py` for the complete environment-variable table and bounds.

## Change recipes

### Add or modify a CLI option

1. Add it to `parser()` in `config.py`.
2. Select CLI/environment/default in `parse_configuration()`.
3. Validate before constructing immutable configuration.
4. Record its source and print it from `main.print_effective()`.
5. Thread the value through composition; do not access global environment elsewhere.
6. Test default, environment fallback, command-line override, invalid input, and startup display.
7. Update README examples/options and migration notes if behavior changes.

Bind settings are independent: do not make Prometheus public merely because the management UI or Grafana is public.
Listener binding and browser-visible Grafana URL resolution are separate contracts.

### Change the release version

Update only `src/sbk_dashboard/version.py`. The `Major.Year.Month.Minor` value flows into setuptools package
metadata through `pyproject.toml`, normal startup output, and `sbk-dashboard -v`. Validate all three surfaces and
build both wheel and source distributions; do not add another version literal to application or packaging code.
Use `network.normalize_host()` for new host or bind boundaries; do not introduce a second DNS/IP parser. Keep API
registration and deletion serialized through reconciliation and preserve compensating rollback on every exception.

### Change endpoint registration or identity

1. Preserve existing JSON compatibility or add an explicit migration/recovery path.
2. Keep identity independent of display name and metrics path unless the product contract changes.
3. Update models, registry validation, API serialization, discovery, mappings, and tests together.
4. Test duplicate registration, same host/different port, DNS, IPv4, IPv6, malformed hosts, and endpoint limits.

### Change dashboard provisioning

1. Treat `resources/grafana/dashboards/sbk-dashboard.json` as an upstream artifact.
2. Apply endpoint-specific modifications only to a deep copy.
3. Recursively scope all `SBK_*` PromQL expressions with `sbk_endpoint_id`.
4. Preserve dashboard visual configuration and 53-panel inventory.
5. Test two endpoints for distinct files/UIDs and verify removal touches only stale generated files.
6. Compare SHA-256 with `/root/projects/SBK/grafana/dashboards/sbk-dashboard.json`.

### Change generated URLs or proxy behavior

1. Separate endpoint identity from browser routing.
2. Treat request headers as untrusted and validate before reflection.
3. Preserve explicit `-grafana-url` semantics.
4. Test localhost, `127.0.0.1`, public IPv4, DNS, bracketed IPv6, base paths, and malformed ports/hosts.
5. Run a real browser-equivalent HTTP request through a non-loopback interface and follow the resulting link.

### Change process startup, shutdown, or supervision

1. Identify whether the process is owned or attached; never blur the distinction.
2. Keep transitions legal in `LifecycleController`.
3. Avoid side effects in constructors and ensure partial `start()` failure unwinds.
4. Keep process identity recording after launch and removal after termination.
5. Drain pipes continuously with bounded reads/log rotation.
6. Keep the guarded native PID/creation-time handshake intact; it closes the hard-parent-death launch race.
7. Test clean stop, repeated stop, startup exit, unhealthy restart, guardian death, hard parent death, descendant
   cleanup, retry backoff, attached non-termination, and unrelated port-owner refusal.
8. Run a live kill/recovery test against native Prometheus and Grafana.

### Change HTTP concurrency

1. Preserve fixed workers and bounded capacity.
2. Ensure overload rejection closes sockets and does not submit work.
3. Ensure cancellation releases capacity exactly once.
4. Never wait for a network/process operation while holding registry/status locks.
5. Test saturation, 503 response, request timeout, shutdown with queued work, and worker-thread cleanup.

### Add a platform archive

1. Extend `RuntimePlatform` normalization and packaged properties.
2. Require official HTTPS URLs and real SHA-256 values.
3. Specify archive directory, executable path, and TAR/ZIP format accurately.
4. Add selection/extraction/checksum/traversal tests.
5. Mark native validation pending until executed on the target OS/architecture.

## Testing layers

Use the least expensive layer that proves the change, then add the higher layers required by risk:

1. **Pure unit:** normalization, validation, PromQL transformation, URL construction, state transitions.
2. **Loopback integration:** HTTP API, bounded server, fake health endpoints, continue mode.
3. **Child-process integration:** restart, process-tree termination, log rotation, leak warnings.
4. **Native stack:** real Prometheus/Grafana readiness, provisioning, public URL, crash recovery, retention config.
5. **Real SBK:** Java 25 SBK `PrometheusLogger`, real series ingestion, 53 panels, restart persistence.
6. **Native cross-platform smoke:** repeat installation/start/stop on Linux, macOS, and Windows rather than simulating
   all OS behavior on Linux.
7. **Container smoke:** build the Linux AMD64 image, validate ports, registration, generated Grafana URL/dashboard,
   restart persistence, and orphan-free shutdown; separately build Linux ARM64 with Buildx/QEMU.

When a test requires external downloads, prefer already installed verified tools. Do not weaken checksum validation
to make a test convenient.

### Change container delivery

1. Preserve the single-container native-child-process architecture and non-root UID/GID 10001.
2. Keep Prometheus internal; publish only management 9721 and Grafana 3000.
3. Keep `/var/lib/sbk-dashboard` on a persistent volume and never bake runtime state into an image layer.
4. Synchronize application/native versions and pinned SHA-256 values with `version.py` and packaged properties.
   Keep the official Python base on a supported Debian stable generation, pin its complete patch tag and
   multi-architecture digest, and update that digest deliberately with full AMD64/ARM64 validation. CI must pass the
   version from `version.py` as `APPLICATION_VERSION`; the Dockerfile default remains a tested local-build fallback.
5. Validate both Dockerfile/Compose contracts and `tests/container_smoke.py`; run the real-SBK mode for lifecycle,
   metrics, 53-panel provisioning, and restart persistence changes.
6. Build both `linux/amd64` and `linux/arm64`; test literal IPv4 and IPv6 scrape addresses on an IPv6-enabled bridge,
   and do not claim native ARM execution from a QEMU build-only check.
7. Update `docs/DOCKER.md`, and remove only the exact disposable containers/volumes/files created by validation.
8. Keep production `compose.yaml` image-only. Put local source builds in `compose.dev.yaml`; neither file may change
   the one-control-plane/two-native-child runtime topology.

## Debugging guide

### Dashboard server does not start

- Inspect effective option sources in startup output.
- Check whether management, Prometheus, or Grafana ports are occupied.
- With replacement mode, confirm the listener executable is identifiable and allowed.
- Inspect bounded `prometheus.log*` and `grafana-console.log*` under the data directory.

### Target remains pending/down

- Fetch the endpoint directly from the Prometheus host.
- Inspect generated `monitoring/prometheus/targets.json` for address, path, and `sbk_endpoint_id`.
- Query Prometheus `/api/v1/targets` and match the endpoint label.
- Remember that the exporter stopping after a benchmark is expected to mark the target down while history remains.

### Dashboard exists but has no data

- Confirm Prometheus has `SBK_*` series with the endpoint label.
- Inspect generated dashboard PromQL for the same endpoint ID.
- Confirm the provisioned datasource UID is `PBFA97CFB590B2093`.
- Compare time range with benchmark timestamps and verify Grafana is querying the managed Prometheus URL.

### Link works locally but not remotely

- Do not set `-grafana-url` to localhost for remote users.
- With no explicit override, call `/api/targets` through the same public host used for the main page.
- Confirm firewall/security-group access to the Grafana port.
- Behind a reverse proxy, configure the externally reachable `-grafana-url` explicitly.

## Review priorities

Review changes in this order:

1. Can the code stop or overwrite an unrelated process or user data?
2. Can any input, queue, thread, read, log, retry, or collection grow without a bound?
3. Can startup/shutdown races leak a process, socket, pipe, lock, or thread?
4. Does a restart preserve registrations, history, mappings, and deterministic dashboard identity?
5. Are multiple endpoints isolated in both discovery labels and every dashboard query?
6. Does behavior remain correct across supported OS/path/executable conventions?
7. Do CLI precedence, startup reporting, documentation, and tests agree?

## Documentation ownership

- `README.md`: end-user install, run, options, examples, operations.
- `docs/ARCHITECTURE.md`: design decisions and invariants.
- `docs/TESTING.md`: executable validation procedures.
- `docs/USAGE.md`: operator installation, environment, endpoint, backup, upgrade, and troubleshooting procedures.
- `docs/INTERNALS.md`: implementation-level call paths, locks, processes, persistence formats, and failure boundaries.
- `docs/MIGRATION.md`: compatibility and upgrade behavior.
- `AGENTS.md`: concise normative instructions for all agents.
- `docs/AGENT_GUIDE.md`: detailed implementation navigation and recipes.
- Tool-specific files: discovery pointers only; do not place unique project rules in them.
