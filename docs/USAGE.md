# Usage and operations

This guide covers normal installation, shell-environment handling, endpoint registration, shutdown, persistence,
upgrades, and common operational checks. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for design decisions and
[`INTERNALS.md`](INTERNALS.md) for implementation details.

## Choose a deployment

| Deployment | Best fit | Prometheus and Grafana |
|---|---|---|
| Python venv | Direct host installation with isolated Python packages | Native child processes on that host |
| Conda | Existing Conda-based operations or development workflow | Native child processes on that host |
| Docker/Compose | Reproducible Linux image and named-volume persistence | Native child processes in the same container |

All three modes run the same control plane and one Prometheus/Grafana pair. Docker is packaging, not a distributed
service topology.

## Python venv lifecycle

Create, activate, install, and run on Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
sbk-dashboard
```

PowerShell uses a platform-specific activation script:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
sbk-dashboard
```

Command Prompt uses:

```batch
py -3 -m venv .venv
.venv\Scripts\activate.bat
sbk-dashboard
```

For interactive use, leave the server in the foreground. Stop it cleanly with `Ctrl+C`, wait for Grafana and
Prometheus shutdown messages, and then leave the environment:

```text
deactivate
```

The same `deactivate` command works in bash, zsh, Command Prompt, and PowerShell after their activation scripts have
been run. It restores the shell's previous Python/PATH selection. It does not:

- stop a dashboard running in another terminal, service, or container;
- delete `.venv`;
- uninstall downloaded Prometheus/Grafana tools; or
- delete endpoint registrations, Grafana state, or Prometheus history.

Reactivate later with the matching activation command. To rebuild a disposable venv, first stop the application and
deactivate it, then remove only that known `.venv` directory and create it again. Persistent data normally remains
under `~/.sbk-dashboard`, outside the venv.

## Conda lifecycle

Create from the repository definition:

```bash
conda env create -f environment.yml
conda activate sbk-dashboard
sbk-dashboard
```

`environment.yml` installs the project in editable development mode with its development tools.

For a smaller runtime-only environment:

```bash
conda create --name sbk-dashboard python=3.12 pip
conda activate sbk-dashboard
python -m pip install .
sbk-dashboard
```

Stop the foreground server with `Ctrl+C`, then leave the environment:

```bash
conda deactivate
```

Conda environments can be nested. Repeat `conda deactivate` if the prompt still shows an environment you intend to
leave. Inspect available environments with `conda env list`, and return with `conda activate sbk-dashboard`.

Environment removal is separate from deactivation:

```bash
conda deactivate
conda env remove --name sbk-dashboard
```

Do not remove the environment while `sbk-dashboard`, its guardians, Prometheus, or Grafana are still using its
Python executable. Removing the Conda environment does not remove an external `-data` directory.

## First start

Run:

```bash
sbk-dashboard
```

On first start the control plane:

1. prints its version, Python executable/environment, supplied arguments, and effective configuration sources;
2. resolves installed Prometheus and Grafana or downloads the platform-specific pinned archives;
3. verifies SHA-256, safely extracts, and atomically installs missing tools;
4. loads endpoint registrations and regenerates monitoring configuration;
5. starts Prometheus, then Grafana, then the management server; and
6. opens the landing page only when a local graphical desktop is detected.

SSH, service, CI, and headless sessions intentionally skip browser launch. Open the printed URL manually, normally
`http://localhost:9721/`.

## Add an endpoint

The landing-page form accepts:

| Field | Meaning |
|---|---|
| Display name | Operator label; defaults to the host and port when blank |
| Host or IP | DNS name, IPv4 literal, or IPv6 literal as reachable from Prometheus |
| Port | SBK/SBM PrometheusLogger HTTP port, from 1 through 65535 |
| Metrics path | Absolute HTTP path, normally `/metrics` |

Identity is based only on normalized `host:port`. A different display name or metrics path does not create another
identity (and a duplicate registration is rejected), while the same host on a second port produces an independent
dashboard.

Endpoint states mean:

- `pending`: registration was reconciled and awaits a successful Prometheus target refresh;
- `up`: Prometheus reports a successful scrape;
- `down`: Prometheus reports failure or no longer reports that registered target; and
- `unknown`: a defensive state for unrecognized status data.

A down endpoint is non-fatal. Existing Prometheus history remains queryable until retention removes it.

## Use public and remote addresses

Opening the landing page through `http://server.example:9721/` causes default generated dashboard links to use
`http://server.example:3000/`. Public IPv4, DNS, loopback, and bracketed IPv6 follow the same rule. Use an explicit
`-grafana-url` when a reverse proxy, TLS terminator, base path, or different published port makes request-host
derivation insufficient.

The endpoint address is resolved from the Prometheus process's network namespace:

- a direct host installation can use `127.0.0.1` for SBK on the same host;
- the supplied container uses `host.docker.internal` for an exporter on the Docker host; and
- a remote exporter uses its routable DNS, IPv4, or IPv6 address.

Authentication is not implemented. Restrict management port 9721 and Grafana port 3000 to trusted networks or put
them behind an authenticated reverse proxy.

## Stop, restart, and deactivate

For a foreground native installation, press `Ctrl+C`. A normal shutdown:

1. stops accepting management requests;
2. stops and joins HTTP workers;
3. signals the monitoring supervisor;
4. stops owned Grafana and then owned Prometheus; and
5. closes log pumps and removes owned-process records.

Wait for completion before running `deactivate` or `conda deactivate`. A later `sbk-dashboard` invocation reloads
registrations, mappings, Grafana state, and Prometheus TSDB from the same data directory.

For Docker:

```bash
docker compose stop
docker compose start
docker compose down
```

Normal `docker compose down` keeps the named volume. Do not add `--volumes` unless permanent deletion of all
container-managed history and registrations is intended.

## Existing native services

The default `-continue false` mode verifies both configured listener owners before replacing either one. It refuses
to stop unrelated or unidentified processes.

`-continue true` attaches only when the configured Prometheus and Grafana health endpoints are already compatible:

```bash
sbk-dashboard -continue true
```

Attached services are observed but are not restarted or stopped by sbk-dashboard. Their configuration must already
use this data directory's discovery and provisioning files.

## Data, backup, and retention

The default data directory is `~/.sbk-dashboard`. Set `-data` for a service installation:

```bash
sbk-dashboard -data /var/lib/sbk-dashboard -retention 14
```

Stop the service before taking a filesystem-level copy of the complete data directory. This produces a consistent
snapshot across `targets.json`, mappings, Grafana SQLite state, and Prometheus TSDB. Restoring only selected PID or
generated configuration files is unsafe; restore the complete stopped snapshot or let generated files be rebuilt
from registrations.

Prometheus applies `-retention` as time-based TSDB retention and removes expired blocks in the background. Removing
an endpoint deletes its generated dashboard/discovery entry but does not synchronously rewrite historical TSDB
blocks.

## Upgrade and uninstall

For a source/venv installation, stop the server, activate the environment, install the new package, run
`sbk-dashboard -v`, and restart with the same data directory. For Conda, activate the existing environment before
upgrading with pip. See [`MIGRATION.md`](MIGRATION.md) before a version change.

`python -m pip uninstall sbk-dashboard` removes the Python package from the active environment. It intentionally
does not delete the data directory or downloaded native tools. Back up and remove persistent data only as a separate
operator decision.

## Operational checks

```bash
curl -fsS http://127.0.0.1:9721/api/health
curl -fsS http://127.0.0.1:9721/api/targets
```

The periodic status log reports control-plane/native health, endpoint counts, and bounded recent landing-page
activity. It does not count direct Grafana bookmarks or direct Prometheus API traffic.

For diagnosis:

- inspect startup's effective configuration and source annotations;
- inspect bounded logs under `<data>/monitoring/logs/`;
- query Prometheus target state through the management endpoint inventory;
- verify the exporter from the Prometheus host/network namespace; and
- use [`TESTING.md`](TESTING.md) for a complete native-stack or real-SBK validation.
