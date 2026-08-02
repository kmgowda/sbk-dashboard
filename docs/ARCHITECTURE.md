# Architecture

## Runtime boundary

`sbk-dashboard` is a Python control plane around two official native servers. Prometheus and Grafana are not Python
modules and are never embedded in the Python interpreter.

```text
                    +---------------- Python process ----------------+
 Browser ---------->| BoundedThreadPoolHttpServer                    |
                    |   |                                             |
                    |   +-- TargetRegistry ---- targets.json          |
                    |   +-- PrometheusTargetDiscovery                 |
                    |   +-- GrafanaDashboardProvisioner               |
                    |   +-- ManagedMonitoringStack facade             |
                    |         +-- single native supervisor            |
                    |         +-- ManagedNativeService x 2            |
                    +---|---------------------|-----------------------+
                        | child process       | child process
                        v                     v
                  Prometheus              Grafana
                  loopback default        public bind default
                  scrape + TSDB           PromQL + dashboards
                        |
                        v
               remote SBK/SBM /metrics endpoints
```

There is one Prometheus process and one Grafana process per `sbk-dashboard` instance. There is not one native process
per endpoint. This is significantly less expensive and lets Prometheus query data across endpoints when required.

## Endpoint isolation

1. The user submits a host, port, optional display name, and metrics path.
2. Input is normalized and SHA-256 of lowercase `host:port` supplies a stable 16-hex-character endpoint ID.
3. `targets.json` is atomically replaced.
4. Prometheus file discovery receives the address, metrics path, and `sbk_endpoint_id` label.
5. The canonical dashboard is deep-copied without changing its panels or visualization settings.
6. Every `SBK_*` PromQL selector receives the endpoint label.
7. Grafana's file provisioner observes `sbk-<endpoint-id>.json` and exposes `/d/sbk-<endpoint-id>/`.
8. `dashboard-mappings.json` records the deterministic relationship.

The host is the same uniqueness component for DNS, IPv4, and IPv6 names; changing only the port creates a distinct
endpoint and dashboard.

Dashboard URLs are resolved at API response time. With the default Grafana URL, the server takes the validated
hostname or IP address from the direct HTTP `Host` header and combines it with the Grafana scheme, port, and dashboard
UID. Consequently a main page opened through a public IP, loopback address, DNS name, or IPv6 literal links to Grafana
through that same address. `-grafana-url` is an authoritative static override for reverse-proxy and TLS deployments.

## Persistence and retention

The Python control plane persists registrations and generated configuration using temporary files, `fsync`, and
atomic replacement. On POSIX it also synchronizes the parent directory after replacement so the renamed directory
entry is crash-durable. It does not store samples itself.

Prometheus stores all samples under `monitoring/prometheus/data`. The `-retention` value is passed as
`--storage.tsdb.retention.time=<days>d`; Prometheus performs block cleanup in the background. Its default is seven
days. A remote endpoint being down does not erase previously ingested samples.

Grafana stores its SQLite database and state under `monitoring/grafana/data`. Generated dashboards are declarative
files, so they are restored deterministically from endpoint registrations.

Corrupt or missing historical TSDB data is handled by Prometheus's own startup and recovery behavior. A target
scrape error becomes a non-fatal `down` state and does not prevent the management server or other dashboards from
operating. Failure of Prometheus itself to start is fatal because the dashboard would have no metrics engine.

## Concurrency

- The HTTP Active Object uses a fixed worker pool (eight by default) and a bounded admission semaphore/queue (64 by
  default). Work beyond that capacity receives HTTP 503 without creating a thread or queued future.
- Accepted client sockets have a 15-second timeout. Request bodies are bounded at 64 KiB and Prometheus target-health
  responses at 4 MiB by default.
- Registry mutations, status snapshots, discovery writes, and provisioning reconciliations are protected by locks.
- Status publication uses immutable tuple/dictionary replacement, so readers never observe partially reconciled
  state. The map is capped by the configured endpoint limit.
- One supervisor thread checks both native services and refreshes target state. It sleeps on a shutdown event, so
  shutdown interrupts the wait without polling delay.
- Target refresh has a bounded configurable timeout. Prometheus and Grafana have separate bounded startup deadlines
  because Grafana initialization can be materially slower on constrained hosts.
- Each owned service has one 64 KiB chunked log-pump thread. Pipes are continuously drained, logs are bounded and
  rotated, and pumps are joined and descriptors closed at shutdown or restart. Transient log open/write/rotation
  failures retry with exponential backoff capped at five minutes without retaining output in memory. A pump that
  remains alive after its source pipe is closed makes lifecycle cleanup fail explicitly, preventing a replacement
  process from racing an old worker for the same log path.
- Prometheus independently schedules and executes endpoint scrapes.
- Grafana independently handles browser sessions and queries.

Python's interpreter lock is not a capacity bottleneck here: the control plane is mostly short filesystem and HTTP
operations, while ingestion, storage, querying, and rendering execute in the native servers.

## Native lifecycle safety

The bootstrap chooses one of six OS/CPU definitions and requires HTTPS plus a pinned SHA-256. It downloads to a
partial file, displays progress, flushes it, atomically promotes it, validates safe archive entries, and installs to
a temporary directory before promotion.

With `-continue false`, listener discovery uses `psutil`. Both requested ports are inspected and all owners are
validated before any process is stopped. Persisted PID, creation time, executable, and port provide a safe fallback
where listener ownership is unavailable. With `-continue true`, health endpoints are checked and compatible
services are attached rather than owned.

Each stack and component follows a validated lifecycle state machine:

```text
new -> starting -> running -> stopping -> stopped
          |           |
          v           v
        failed <--- starting (supervised restart)
```

Illegal transitions fail immediately. Construction has no process side effects; `start()` acquires resources and
`close()` is idempotent. Startup failure performs reverse-order cleanup. Shutdown first stops admission, signals the
supervisor, joins it, and then stops Grafana and Prometheus in reverse dependency order.

Owned POSIX children start in new sessions. Termination addresses the process group and captured descendant tree,
first gracefully and then forcibly after a bounded timeout. Windows children start in new process groups and are
terminated recursively with `psutil`. PIDs, executable paths, creation times, and ports are persisted to defend
against PID reuse.

The supervisor restarts an exited owned process, or one that remains unhealthy for three checks. Failed restarts use
exponential backoff capped at 60 seconds. Attached processes are health-checked but never restarted or terminated.
Only processes launched by the current invocation are owned during normal shutdown.

Prometheus binds to `127.0.0.1` by default because its only application consumer is Grafana. Management and Grafana
default to `0.0.0.0` to preserve remote dashboard access. Each address is independently configurable; bind addresses
control listeners, while `-grafana-url` and validated request hosts control browser-visible URLs. Shared canonical
host parsing applies the same IP/DNS rules to configuration and registration boundaries. Port ownership fallback
probes resolve configured hostnames through `getaddrinfo`, check each resulting address family, and check a bounded
set of local interface addresses for wildcard listeners before requiring a successful bind and listen. POSIX probes
enable address reuse so `TIME_WAIT` does not block restart. Windows first requires exclusive ownership and permits a
reusable fallback only when `psutil` reports exclusively `TIME_WAIT` sockets for the port and family.

## Object-oriented design

The implementation uses patterns where they enforce runtime invariants:

- **Facade:** `ManagedMonitoringStack` exposes reconciliation, status, health, start, and close while hiding two
  service lifecycles and generated configuration.
- **State:** `LifecycleController` validates every stack, HTTP server, and native-service transition.
- **Command:** immutable `NativeServiceSpec` objects supply platform-resolved launch commands and resource policy.
- **Strategy:** `HealthProbe` separates readiness policy from native process ownership and makes supervision
  independently testable.
- **Supervisor:** one bounded control loop observes services, applies thresholds/backoff, and reconciles status.
- **Repository:** `TargetRegistry` and `ManagedProcessRegistry` own validation and atomic persistence.
- **Compensating transaction:** target mutations are serialized across persistence and monitoring reconciliation;
  any reconciliation exception restores the prior registration snapshot before the API reports failure.
- **Active Object / Bulkhead:** the HTTP executor isolates request concurrency with fixed workers and backpressure.
- **RAII-style context ownership:** every response, archive, file, socket, process pipe, thread pool, and child process
  has an explicit close/join path.

These patterns are composition-based. There is no inheritance hierarchy for its own sake; immutable dataclasses
describe state and policies, while lifecycle-owning objects encapsulate mutation behind locks.

## Resource bounds and 24/7 operation

Python-side growth is bounded by the endpoint limit, fixed HTTP workers/queue, maximum request/health payloads, one
status per endpoint, two child descriptors, and fixed log generations. Repeated warning text is emitted only when the
failure changes, avoiding identical five-second log spam. Endpoint reconciliation serializes one dashboard clone at
a time rather than retaining all cloned JSON trees.

Prometheus TSDB memory/disk use and Grafana query memory remain native-service concerns. Time retention constrains
Prometheus history, while operators must size the VM for metric cardinality and dashboard query load. A host service
manager should supervise the Python process; its internal supervisor is responsible for its owned native children.

The Python control plane uses standard logging with timestamps and severity levels. Native child output remains
separately drained and rotated so neither Python logging nor a blocked process pipe can grow without bound.

## Cross-platform strategy

All control-plane code is common across Linux, macOS, and Windows. Platform differences are limited to:

- normalized OS/CPU archive selection;
- TAR.GZ versus ZIP extraction;
- `.exe` executable discovery;
- native process and socket information supplied by `psutil`;
- the platform's venv activation command.

The installed `sbk-dashboard` console entry point is generated by Python packaging on every platform. The same
wheel can be installed in a venv or Conda environment; Prometheus and Grafana archives remain platform-specific.

## Authentication and containers

Authentication is deliberately disabled. `-auth true` is rejected and reserved for future development. The server
must therefore be protected by network controls or a reverse proxy when exposed outside a trusted environment.

No Docker, Podman, Kubernetes, or Compose runtime is used in this phase.
