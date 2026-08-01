# Testing

## Automated validation

Create either supported development environment and execute:

```bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m pytest
coverage run -m pytest
coverage report
python -m build
python -m pip install --force-reinstall dist/sbk_dashboard-*.whl
sbk-dashboard -h
```

The standard-library suite is also runnable without pytest:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Socket-based tests need permission to bind loopback ports. Windows and macOS should run their native smoke tests on
those operating systems because native executables cannot be meaningfully launched through Linux simulation.

## Manual Linux end-to-end test

Use a temporary data directory and non-default ports when another monitoring stack is running:

```bash
sbk-dashboard \
  -data /tmp/sbk-dashboard-e2e \
  -port 19721 \
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

Stop with `Ctrl+C`, restart with the same data directory, and confirm registrations, mappings, dashboard files, and
Prometheus history remain. Prometheus and Grafana child PIDs started by that invocation must no longer be alive.

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
