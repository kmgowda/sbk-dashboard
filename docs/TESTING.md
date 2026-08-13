# Testing

## Automated validation

Create either supported development environment and execute:

```bash
python -m pip install -e ".[dev]"
ruff check src tests scripts
mypy src scripts/sbk_dashboard_launcher.py
python -m pytest
coverage erase
COVERAGE_PROCESS_START=pyproject.toml coverage run -m pytest
coverage combine
coverage report
python -m build --no-isolation
python -m pip install --force-reinstall dist/sbk_dashboard-*.whl
sbk-dashboard -h
sbk-dashboard -v
git diff --check
```

The standard-library suite is also runnable without pytest. Promoting `ResourceWarning` to an error checks leaked
files, sockets, subprocess streams, and threads:

```bash
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
```

Socket-based tests need permission to bind loopback ports. Windows and macOS should run their native smoke tests on
those operating systems because native executables cannot be meaningfully launched through Linux simulation.
The cross-platform unit workflow uses the explicit Apple Silicon `macos-15` runner instead of the moving
`macos-latest` label so runner OS and architecture changes cannot silently alter the required check.
The job asserts that macOS reports `arm64` and invokes the installed CLI before running the unit suite.
Platform-resolution tests still cover macOS x86-64 and ARM64; full native Apple Silicon operation remains a
separate smoke-test requirement.

## Container validation

The static contract tests verify non-root execution, persistent data, public ports 9721/3000, internal Prometheus,
the container-specific `host.docker.internal` endpoint-form default, native version/checksum synchronization,
Compose hardening, and build-context exclusions:

```bash
PYTHONPATH=src python -m unittest tests.test_container -v
docker compose config --quiet
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
python tests/compose_contract.py
```

The first resolved configuration must contain the pinned Docker Hub image and no build definition. The merged
development configuration must retain the same ports, volume, network, security, and lifecycle settings while
adding only the local image/build policy.

Build and run the live Linux smoke test:

```bash
docker build --tag sbk-dashboard:test .
python tests/container_smoke.py --image sbk-dashboard:test
```

It starts a uniquely named IPv6-enabled bridge, two single-threaded synthetic remote exporters, a disposable
dashboard container on randomly selected non-default loopback host ports, and a volume. The exporters are registered
by their literal container IPv4 and IPv6 addresses, proving that
Prometheus receives both unchanged addresses and scrapes both successfully. The test also validates landing/Grafana
host access, endpoint-scoped metrics, both generated 53-panel dashboards, publication of only ports 9721 and 3000,
exact-registration reuse with HTTP 200, deterministic comparison ID/URL reuse in reversed order, its generated
53-panel comparison file, comparison persistence across restart, clean shutdown, absence of recorded native PIDs,
and registration/dashboard persistence across a full restart. Its `finally` cleanup removes only those uniquely
named containers, network, and volume.

For real SBK integration, expose a non-default host exporter port for at least 120 seconds:

```bash
/root/projects/SBK/build/install/sbk/bin/sbk \
  -class file -file /tmp/sbk-dashboard-container-test.bin \
  -writers 1 -size 4096 -seconds 120 -records 100000 \
  -out PrometheusLogger -context 19718/metrics
```

In another terminal run:

```bash
python tests/container_smoke.py \
  --image sbk-dashboard:test \
  --target-host host.docker.internal \
  --target-port 19718 \
  --expect-target-up
```

This mode additionally requires the target to become `up`, verifies endpoint-scoped real `SBK_*` series in the
container's internal Prometheus, counts exactly 53 panels in the generated dashboard, and repeats those assertions
after a full container restart. Stop SBK and remove only `/tmp/sbk-dashboard-container-test.bin` afterward.

CI uses the stable `ubuntu-24.04` runner, builds/runs Linux AMD64, and builds Linux ARM64 under QEMU. A successful
QEMU build is not a native ARM runtime claim. Docker Desktop behavior on macOS/Windows and native ARM execution still
require their respective smoke tests.

Container contract tests also keep the Dockerfile Grafana build number, archive checksums, download-size cap,
application version build arguments, stable Linux runner labels, and best-effort pull-request cache export aligned
with packaged configuration.

Regression coverage includes malformed IP-like target and bind values, configured-family port probes, IPv4/IPv6
wildcard link filtering, and persistent create/delete rollback when monitoring reconciliation raises an exception.
It also sends a live oversized request with a negative `Content-Length`, rejects negative native-download lengths,
rejects non-numeric download lengths as I/O failures, rejects boolean API ports, verifies POSIX probes enable address
reuse for `TIME_WAIT` while requiring a successful `listen()`, and ensures corrupted persisted boolean or out-of-range
endpoint ports cannot be loaded.
An active TCP listener is rejected by bounded connect preflights over local wildcard interfaces before
platform-specific reusable bind semantics. Port tests use distinct socket doubles for the connect and bind phases,
cover AAAA-only DNS bind names, and permit Windows reuse only for confirmed `TIME_WAIT` sockets. Native lifecycle
regressions also verify that an unavailable log destination still drains output beyond pipe capacity, a transient
write failure recovers after bounded backoff, a stuck log pump is reported while ownership cleanup still executes,
captured descendants are force-cleaned when their parent disappears during external-process termination, startup
failure removes guardian state, a killed guardian cannot orphan its native child, and hard parent death terminates
both the guarded native process and guardian. Guardian-handshake tests also reproduce a transient Windows
`PermissionError`, verify that a subsequent read succeeds, and ensure persistent access denial times out with useful
diagnostics.
Windows Job Object unit tests verify the kill-on-close limit, process assignment, handle closure, primary-thread
resume, and suspended creation flags on every platform. A native Windows smoke test must additionally kill the
guardian and main dashboard independently and confirm Prometheus/Grafana plus descendants disappear within the
bounded cleanup period; Linux simulation is not a native Job Object claim.
Native-port tests verify startup reporting for available defaults, automatic fallbacks, CLI values, and environment
values; occupied operator-supplied ports report identifiable owners and stop no process. A second acquisition-time
test verifies that even an expected Prometheus/Grafana executable cannot be replaced on an operator-supplied port
if it appears after initial selection. Continue-mode tests retain compatible attachment behavior.
Configuration and composition-root regressions verify the 60-second status default, CLI-over-environment precedence,
range validation, effective-source output, exact interruptible wait interval, concise endpoint/native summary, and
non-fatal handling of a reporting failure. Browser-launch regressions verify new-tab requests on graphical Linux,
macOS, and Windows environments; SSH, CI, Windows service, and headless suppression; startup URL selection; and
non-fatal launcher errors.

Web asset regressions verify that the landing page exposes total, up, and down counters and that each inventory
refresh derives the health counts from exact `up` and `down` states. Pending and unknown states count only toward the
total. The counters remain visible in the responsive single-column layout. They also verify that native
configuration renders `127.0.0.1`, container configuration renders `host.docker.internal`, and neither HTML template
placeholder reaches the browser.

Target-health regressions also start with a registered endpoint absent from a successful Prometheus target response,
verify it transitions from initial `pending` to `down`, and then publish an active healthy target to verify recovery
to `up` and exact summary counts in both states.

Registration regressions submit the same normalized host, port, metrics path, display name, and kind repeatedly and
verify that the first request creates one endpoint with HTTP 201 while every exact repeat returns that same endpoint
and dashboard with HTTP 200. Conflicting metadata for the same `host:port` remains rejected. Comparison regressions
likewise verify that the same endpoint set in any order returns the same deterministic comparison dashboard ID and
URL.

Reconciliation-generation regressions block a Prometheus status response while replacing the target set and verify
that the obsolete response cannot remove the new endpoint's `pending` state or restore a deleted endpoint. Command
tests assert the configured `--storage.tsdb.retention.time=<days>d` value. When `promtool` is installed beside
Prometheus, startup runs `promtool check config` before either native service is started.

The HTTP asset test requires one matching 12-hex content fingerprint in the JavaScript and CSS URLs and
`Cache-Control: no-cache` on both resources. This protects upgrades from the regression where new counter markup was
rendered while a cached older script updated only the Total value.

## Manual Linux end-to-end test

Use a temporary data directory and non-default ports when another monitoring stack is running:

```bash
sbk-dashboard \
  -data /tmp/sbk-dashboard-e2e \
  -port 19721 \
  -bind 127.0.0.1 \
  -prometheus-port 19090 \
  -grafana-port 13000 \
  -grafana-url http://localhost:13000
```

Confirm:

```bash
curl -fsS http://127.0.0.1:19721/api/health
curl -fsS http://127.0.0.1:19090/-/ready
curl -fsS http://127.0.0.1:13000/api/health
```

Confirm Prometheus is not publicly listening. On Linux, `ss -ltnp` should show `127.0.0.1:19090`, while management
and Grafana reflect their configured bind addresses. Repeat once with default public management/Grafana binds when
validating non-loopback dashboard links.

Start SBK using the command in the README, register its exporter, and verify:

```bash
curl -fsS -X POST http://127.0.0.1:19721/api/targets \
  -H 'Content-Type: application/json' \
  --data '{"name":"SBK test","host":"127.0.0.1","port":9718,"metricsPath":"/metrics"}'

curl -fsS 'http://127.0.0.1:19090/api/v1/targets?state=active'
curl -fsS 'http://127.0.0.1:19090/api/v1/query?query=up%7Bjob%3D%22sbk-dashboard%22%7D'
```

Open the returned Grafana URL and confirm all 53 panels load. Register the same host with another exporter port and
confirm it receives a different URL and shows only its endpoint-labelled series.

Open the management page through a non-loopback address such as `http://<server-public-ip>:19721/`. The generated
dashboard link must use `<server-public-ip>:13000`, not `localhost` or `127.0.0.1`. Repeat through loopback and, when
available, a DNS name or IPv6 literal; the Grafana hostname must follow the hostname used for the management page.

Kill the managed Prometheus PID without stopping sbk-dashboard. Within the supervisor interval, confirm a new PID is
recorded, `/-/ready` recovers, and the existing TSDB still returns the endpoint's historical series. Repeat for
Grafana and verify the dedicated URL recovers. Attached `-continue true` processes must not be killed or restarted.

Stop with `Ctrl+C`, restart with the same data directory, and confirm registrations, mappings, dashboard files, and
Prometheus history remain. Prometheus and Grafana child PIDs started by that invocation must no longer be alive.

Repeat with a unique temporary data directory, note the main, guardian, Prometheus, and Grafana PIDs, and force-kill
only the main sbk-dashboard PID (`kill -9` on Linux/macOS or direct process termination on Windows). Within the bounded
guardian cleanup period, both native process trees and both guardians must exit and both ports must stop listening.
Do not run this check against an attached `-continue true` stack, whose external services must remain running.

Startup logs must include a timestamp and level, and must report effective bind addresses, startup deadlines,
target-health timeout, and the source of every CLI-backed setting. Run once with `-log-level DEBUG` and once with
`SBK_DASHBOARD_LOG_LEVEL=WARNING` to verify precedence and filtering.

Run once with `-status-seconds 5` and leave the application active for at least 12 seconds. Confirm at least two
`Status:` records appear, each containing server/stack state, Prometheus and Grafana health, and endpoint totals for
`up`, `down`, `pending`, and `unknown`, plus `clients_recent`, `landing_clients_2m`, and `grafana_opens_5m`. Stop the
application and confirm no additional status appears after shutdown.

Client-activity regressions validate URL-safe opaque IDs, invalid surface/method rejection, same-browser de-duplication
across landing and Grafana categories, per-category capacity eviction, exact two-/five-minute expiry, and the
30-second browser heartbeat/dashboard-click hooks. Native Prometheus and Grafana configuration and routing remain
unchanged; direct native-server clients are deliberately not asserted as observable.

Comparison regressions verify SBK/SBM kind compatibility, readable name/kind scrape labels, all 53 generated panels,
complete regex scoping of every `SBK_*` selector, name/kind/endpoint-ID legends, deterministic order-independent
comparison UIDs, bounded 2–8-ID API validation and comparison cache, request-host URL behavior, and removal of cached
comparisons when an endpoint is removed. A native smoke test should run two concurrent exporters, select both on the landing
page, and confirm both named series remain live in representative throughput, latency, connection, and stat panels.

## venv and Conda checks

Validate both installers rather than assuming their activation semantics are equivalent:

```bash
python3 -m venv /tmp/sbk-dashboard-venv
/tmp/sbk-dashboard-venv/bin/python -m pip install .
/tmp/sbk-dashboard-venv/bin/sbk-dashboard -h

conda create -y -p /tmp/sbk-dashboard-conda python=3.12 pip
conda run -p /tmp/sbk-dashboard-conda python -m pip install .
conda run -p /tmp/sbk-dashboard-conda sbk-dashboard -h
```

On Windows, substitute `Scripts\\python.exe` and `Scripts\\sbk-dashboard.exe` for the venv paths.

The automated launcher tests use a disposable fake application to verify foreground console logging, background
file logging, help while an instance is running, independent per-port ownership, automatic native-port fallback,
selective and default stop behavior, active-environment precedence, duplicate-start behavior, PID creation-time
validation, bounded log rotation, and forceful descendant cleanup. For a native launcher
smoke test, activate the venv or Conda environment, use unique non-default ports and a temporary data directory, and
run the matching pair:

```bash
./scripts/start-sbk-dashboard-background.sh -port 19721 -prometheus-port 19090 -grafana-port 13000 \
  -data /tmp/sbk-dashboard-launcher-test
./scripts/stop-sbk-dashboard.sh -port 19721
```

```powershell
.\scripts\Start-SbkDashboardBackground.ps1 -port 19721 -prometheus-port 19090 -grafana-port 13000 `
  -data $env:TEMP\sbk-dashboard-launcher-test
.\scripts\Stop-SbkDashboard.ps1 -port 19721
```

Confirm the recorded launcher PID and all owned native descendants exit before removing only that disposable data
directory. Run the PowerShell pair on native Windows; a Linux-only run does not validate Windows process-group and
`Ctrl+Break` behavior.

Repeat with the default foreground start script and confirm logs remain on its console and `Ctrl+C` cleans up all
owned children. The same stop script must also stop that foreground instance when invoked from a second terminal.

## Native Windows extraction smoke test

The Linux suite simulates Windows archive names and drive-letter traversal, but it does not prove native Windows
archive or filesystem semantics. On a Windows runner or VM, create a disposable venv and data directory, then run:

```powershell
py -3 -m venv $env:TEMP\sbk-dashboard-win-venv
& $env:TEMP\sbk-dashboard-win-venv\Scripts\python.exe -m pip install -e ".[dev]"
& $env:TEMP\sbk-dashboard-win-venv\Scripts\python.exe -m unittest tests.test_bootstrap -v
& $env:TEMP\sbk-dashboard-win-venv\Scripts\sbk-dashboard.exe -data $env:TEMP\sbk-dashboard-win-data -h
```

For a full bootstrap smoke test, start without installed native tools using a disposable data directory, verify the
pinned Windows archives install beneath that directory (Prometheus ZIP and Grafana TAR.GZ), and confirm
`prometheus.exe`, `promtool.exe`, and Grafana start.
Stop the dashboard and verify all child processes exit before deleting only those two disposable directories. This
native Windows validation remains required before claiming Windows runtime certification.
