# Migration from the Java implementation

The Python rewrite preserves the external contract:

- the command name remains `sbk-dashboard`;
- all command-line options and environment-variable precedence remain available;
- default ports and seven-day retention remain unchanged;
- existing `targets.json` field names and endpoint IDs remain readable;
- the monitoring directory, Prometheus TSDB, Grafana data, generated dashboards, and mappings remain in place;
- the exact canonical Grafana dashboard remains packaged.

Build and runtime requirements changed from JDK 25 plus Gradle to Python 3.10+ plus `pip` or Conda. Remove Java launch
scripts from service definitions and point them at the environment's generated `sbk-dashboard` command.

Example systemd command after installing into `/opt/sbk-dashboard-venv`:

```ini
ExecStart=/opt/sbk-dashboard-venv/bin/sbk-dashboard -data /var/lib/sbk-dashboard
```

Stop the Java version before starting the Python version. The default `-continue false` behavior can replace old
Prometheus and Grafana processes safely, but an orderly migration avoids an unnecessary forced service transition.

`SBK_JAVA_HOME` and `JAVA_HOME` no longer affect the dashboard. No data migration is required when the same
`SBK_DASHBOARD_DATA_DIR` is selected.

## Network and logging changes in the production-hardening release

Prometheus now binds to `127.0.0.1` by default instead of every interface. This is compatible with the managed
Grafana datasource and health checks. Deployments that intentionally query Prometheus remotely must explicitly set
`-prometheus-bind` or `SBK_DASHBOARD_PROMETHEUS_BIND` and provide their own network controls.

Management and Grafana continue to bind publicly by default. They can now be restricted independently with `-bind`
and `-grafana-bind`. Listener addresses do not change generated public URLs; continue using `-grafana-url` for proxy,
TLS, or external DNS routing.

Control-plane messages now use timestamped standard Python logging on stderr. Service definitions that previously
redirected stdout should capture stderr as well, or rely on the host service manager's combined journal. Native
Prometheus and Grafana console logs remain in bounded rotating files under the monitoring data directory.

Grafana's default startup deadline is now 120 seconds; Prometheus remains 45 seconds. Persistent JSON remains
backward compatible and needs no data migration.

The Python server emits one concise INFO-level runtime status every 60 seconds by default. Use
`-status-seconds <1-86400>` or `SBK_DASHBOARD_STATUS_SECONDS` to change the interval. This is operational output only;
it adds no persisted field and requires no data migration.

Owned Prometheus and Grafana processes now run beneath lightweight lifecycle guardians. This adds no service option
or persisted-data migration. Service managers may retain their existing stop timeout and process-group policy; normal
shutdown remains graceful, while hard termination of only the main PID now triggers guardian cleanup. Attached
`-continue true` services remain externally owned and are never guarded or terminated by sbk-dashboard.

The landing page now displays total, up, and down endpoint counters derived from the existing target inventory API.
This is a browser-only presentation change with no new endpoint, option, or persisted-data migration.

Registered endpoints missing from a successful Prometheus target response now transition from initial `pending` to
`down`. Earlier versions left this case pending indefinitely, causing the landing-page Down counter to omit stale
session endpoints. This status correction requires no configuration or data migration.

Landing-page JavaScript and CSS URLs now include a content fingerprint, and all assets require browser revalidation.
Operators do not need to ask users to clear their browser cache after upgrading; the next page load fetches a
compatible control script and stylesheet automatically.

Periodic status now reports bounded recent browser activity: `clients_recent`, `landing_clients_2m`, and
`grafana_opens_5m`. This adds only an in-memory control-plane heartbeat endpoint and opaque per-tab browser IDs; it
does not change persisted data, command options, native Prometheus/Grafana configuration, or direct-server routing.
Direct Grafana bookmarks and Prometheus API users remain outside these counts.

Interactive local startup now makes one best-effort request to open the management landing page in a new browser tab.
SSH, CI, non-interactive Windows service, and headless Unix sessions are detected and skipped automatically. Browser
startup failure is non-fatal and adds no command-line option or persisted state.

Endpoint registry loading now revalidates normalized host names, metrics paths, field bounds, and the stable
host-and-port-derived endpoint ID before generating discovery or dashboard files. Registries produced by supported
sbk-dashboard releases remain compatible. Manually modified entries with inconsistent IDs or invalid fields must be
corrected before startup. Native archive downloads are now capped by the `download.max.bytes` monitoring property;
the packaged default is 2 GiB.

## Optional container deployment

Release 1.26.8.1 adds an optional Linux AMD64/ARM64 container without changing endpoint IDs, persisted JSON,
dashboard UIDs, retention, or the native child-process design. To move an existing direct installation, stop it
cleanly and copy its data root into a Docker volume or a UID/GID 10001-writable bind mount at
`/var/lib/sbk-dashboard`. Publish host ports 9721 and 3000; do not publish internal Prometheus port 9090.

For a new deployment, `docker compose up --build --detach` creates and retains the named data volume. Recreating or
upgrading the container against that same volume preserves registrations, generated dashboards, Grafana state, and
Prometheus history. `docker compose down` preserves the volume; `docker compose down --volumes` permanently removes
it and must not be used during a normal upgrade.

Inside a container, `127.0.0.1` identifies the container itself. Register an exporter running on the Docker host as
`host.docker.internal` (the supplied Compose file adds the host-gateway mapping), or use a routable DNS/IP address
for a remote endpoint. See `docs/DOCKER.md` for the complete procedure.

The production container base is now pinned to Python 3.12.13 on Debian Trixie by immutable multi-architecture
digest, and container CI is pinned to Ubuntu 24.04. This changes only the packaged Linux userspace and build runner;
application behavior, persisted data, endpoint identity, and native deployment support are unchanged. Compose now
enables IPv6 on its bridge so literal IPv6 targets can be scraped when the Docker host has working IPv6 routing.
