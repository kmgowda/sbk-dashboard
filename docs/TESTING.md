# Testing

## Automated validation

Create either supported development environment and execute:

```bash
python -m pip install -e ".[dev]"
ruff check src tests
mypy src
python -m pytest
coverage run -m pytest
coverage report
python -m build --no-isolation
python -m pip install --force-reinstall dist/sbk_dashboard-*.whl
sbk-dashboard -h
```

The standard-library suite is also runnable without pytest. Promoting `ResourceWarning` to an error checks leaked
files, sockets, subprocess streams, and threads:

```bash
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
```

Socket-based tests need permission to bind loopback ports. Windows and macOS should run their native smoke tests on
those operating systems because native executables cannot be meaningfully launched through Linux simulation.

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
and captured descendants are force-cleaned when their parent disappears during external-process termination.
Configuration and composition-root regressions verify the 60-second status default, CLI-over-environment precedence,
range validation, effective-source output, exact interruptible wait interval, concise endpoint/native summary, and
non-fatal handling of a reporting failure.

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

Startup logs must include a timestamp and level, and must report effective bind addresses, startup deadlines,
target-health timeout, and the source of every CLI-backed setting. Run once with `-log-level DEBUG` and once with
`SBK_DASHBOARD_LOG_LEVEL=WARNING` to verify precedence and filtering.

Run once with `-status-seconds 5` and leave the application active for at least 12 seconds. Confirm at least two
`Status:` records appear, each containing server/stack state, Prometheus and Grafana health, and endpoint totals for
`up`, `down`, `pending`, and `unknown`. Stop the application and confirm no additional status appears after shutdown.

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
