# Docker deployment

The supported image packages the Python control plane, official Prometheus, and official Grafana in one Linux
container. Prometheus and Grafana remain managed native child processes; they are not embedded Python modules or
independent service containers.

## Quick start

Use the pinned release image:

```bash
docker compose pull
docker compose up --detach
docker compose ps
```

`compose.yaml` contains no `build` section. It pulls
`kmgowda/sbk-dashboard:1.26.8.2` from Docker Hub when missing and reuses the local image afterward. Prometheus,
Grafana, and the Python wheel are already installed in that image; they are never downloaded during container
startup. Override the pinned image only when deliberately testing another published build:

```bash
SBK_DASHBOARD_IMAGE=kmgowda/sbk-dashboard:<version> docker compose up --detach
```

The first image pull still transfers the complete runtime image. Routine `docker compose start` operations perform
no pull, build, native download, or extraction.

Open these host URLs:

- Landing page: `http://localhost:9721/`
- Grafana: `http://localhost:3000/`

The container is headless and cannot open a browser process on the Docker host. Publishing ports 9721 and 3000
makes the UI and every generated dashboard link available to the host browser immediately. Opening the landing page
through a public IP or DNS name produces generated Grafana links with that same hostname and port 3000.

Inspect or stop the deployment with:

```bash
docker compose logs --follow sbk-dashboard
docker compose stop
docker compose start
docker compose down
```

`docker compose down` preserves the named volume. Do not add `--volumes` during routine stop, upgrade, or recovery;
that option deletes endpoint registrations, Prometheus history, Grafana state, and generated mappings.

## Released image

Release tags publish `linux/amd64` and `linux/arm64` images to Docker Hub. Run a pinned release:

```bash
docker run --detach --name sbk-dashboard --restart unless-stopped \
  --publish 9721:9721 --publish 3000:3000 \
  --add-host host.docker.internal:host-gateway \
  --volume sbk-dashboard-data:/var/lib/sbk-dashboard \
  kmgowda/sbk-dashboard:1.26.8.2
```

Use a pinned release in production instead of `latest`. The image runs as UID/GID 10001, uses `tini` as PID 1,
includes a control-plane health check, exposes only ports 9721 and 3000, and keeps Prometheus on container loopback.
Its official Python 3.12/Debian stable base is pinned by complete patch version and immutable multi-architecture
digest so AMD64 and ARM64 builds resolve the same reviewed manifest. Native archive downloads are checksum-verified,
time-bounded, retried, and capped at the same 2 GiB per-download maximum as automatic native installation.

## Register endpoints

The address entered in the landing page is preserved and resolved or routed from inside the container:

| Exporter location | Host field | Example |
|---|---|---|
| Same container | `127.0.0.1` | Only for an exporter deliberately running in this container |
| Docker host | `host.docker.internal` | `host.docker.internal:9718/metrics` |
| Remote system | Routable DNS, IPv4, or IPv6 | `benchmark-01.example.com:9718/metrics` |

The container image sets the form's default host to `host.docker.internal`; a direct Python/Conda installation
defaults it to `127.0.0.1`. Do not use the container's `127.0.0.1` for an exporter on the Docker host, because it
refers to the container network namespace.

The supplied Compose and `docker run` commands install Docker's `host-gateway` mapping so a host SBK process is
reachable on Linux and Docker Desktop. The host exporter must listen on an address reachable from the Docker bridge;
an exporter bound exclusively to host `127.0.0.1` generally cannot be reached by a Linux container.

Compose enables IPv6 on its user-defined bridge. IPv4 and DNS endpoints use Docker's normal outbound masquerading;
literal IPv6 endpoints additionally require IPv6 routing in the Docker daemon, host OS, and upstream network. No
inbound port publication is needed for scraping because Prometheus initiates the connection from the container.

## Configuration

The image supplies container-safe defaults for the data directory, native tool paths, and bind addresses. Existing
CLI options can be appended after the image name:

```bash
docker run --detach --name sbk-dashboard \
  --publish 9721:9721 --publish 3000:3000 \
  --add-host host.docker.internal:host-gateway \
  --volume sbk-dashboard-data:/var/lib/sbk-dashboard \
  kmgowda/sbk-dashboard:1.26.8.2 \
  -retention 14 -status-seconds 30
```

Environment variables documented in the README can be set with `--env` or Compose `environment`. Do not override
the following image defaults unless the replacement paths/addresses are valid inside the container:

- `SBK_DASHBOARD_DATA_DIR=/var/lib/sbk-dashboard`
- `SBK_DASHBOARD_DEFAULT_TARGET_HOST=host.docker.internal`
- `SBK_DASHBOARD_PROMETHEUS_BIN=/opt/prometheus/prometheus`
- `SBK_DASHBOARD_GRAFANA_HOME=/opt/grafana`
- `SBK_DASHBOARD_PROMETHEUS_BIND=127.0.0.1`
- `SBK_DASHBOARD_BIND=0.0.0.0`
- `SBK_DASHBOARD_GRAFANA_BIND=0.0.0.0`

For a reverse proxy, TLS, or a different host-published Grafana port, set `-grafana-url` to the complete URL clients
can reach. If host port 33000 maps to container port 3000, for example, use
`-grafana-url https://dashboard.example.com:33000`.

## Persistence and upgrades

All mutable state lives under `/var/lib/sbk-dashboard`; the image itself is replaceable. Upgrade while retaining the
named volume:

```bash
docker compose pull
docker compose up --detach
```

For a bind mount, create the directory in advance and make it writable by UID/GID 10001. Back up the data directory
or volume while the container is stopped for a consistent Prometheus/Grafana snapshot. Never copy runtime PID state
from a running instance into another simultaneously running instance.

## Security and operations

Authentication is not implemented. Restrict ports 9721 and 3000 with host firewall/security-group rules, a trusted
network, or an authenticated reverse proxy. Prometheus 9090 is intentionally neither exposed nor published.

The Compose definition drops all Linux capabilities and enables `no-new-privileges`. Do not add privileged mode or
mount the Docker socket. Native child logs and control-plane state remain bounded according to normal
sbk-dashboard settings. The image health check reports unhealthy when the management/native stack health endpoint
cannot respond successfully.

Graceful `docker stop` sends `SIGTERM` through `tini`; the Python lifecycle closes Grafana and Prometheus in reverse
dependency order and removes ownership records. The existing guardian processes provide additional cleanup if the
Python control plane dies unexpectedly. Docker `SIGKILL` cannot run in-container cleanup, so use the normal stop
grace period whenever possible.

## Build and validate

Production users should not build locally. For source development, combine the production definition with the
explicit build override:

```bash
docker compose -f compose.yaml -f compose.dev.yaml build --progress=plain
docker compose -f compose.yaml -f compose.dev.yaml up --detach --no-build
```

The override changes only image acquisition. Ports, volume, network, security settings, entry point, and the runtime
topology remain inherited from `compose.yaml`.

Build and smoke-test the local architecture directly:

```bash
docker build --build-arg VCS_REF=local --tag sbk-dashboard:1.26.8.2 .
python tests/container_smoke.py --image sbk-dashboard:1.26.8.2
```

Build both published architectures with Buildx without publishing them:

```bash
docker buildx build --platform linux/amd64,linux/arm64 --tag sbk-dashboard:multiarch .
```

The live smoke test validates host port access, registration, a generated Grafana dashboard, persistent state over
a full restart, graceful exit, and absence of surviving native child PIDs. The real-SBK mode is documented in
`docs/TESTING.md`.

Prometheus and Grafana downloads are separate Docker stages backed by independent checksum-validated BuildKit cache
mounts, allowing cold downloads to run in parallel.
Changing the Python source does not invalidate either native stage; changing only one native version does not
invalidate the other tool's extraction. Cached archives never enter the final runtime image.

## Publish images to Docker Hub

Use the dedicated [Docker Hub build, publishing, and pull guide](DOCKER_HUB.md). It provides complete copy-and-paste
procedures for local Compose and direct builds, smoke validation, secure access-token login, Buildx setup,
AMD64/ARM64 version publishing, manifest verification, customer pulls, upgrades, GitHub Actions, and common errors.

## Troubleshooting

- If `http://localhost:9721/` is unreachable, run `docker compose ps` and `docker compose logs sbk-dashboard`.
- If startup reports an occupied host port, stop the host listener or change the left side of the port mapping and
  set an explicit `-grafana-url` when changing Grafana's host port.
- If a Docker-host target is down, use `host.docker.internal`, confirm the exporter listens beyond host loopback,
  and test it from inside the container network.
- Register the endpoint while SBK is running. PrometheusLogger closes its HTTP endpoint when the benchmark exits;
  stored history remains available, but the registered endpoint correctly changes to `down`.
- Test the exact scrape URL from the Prometheus network namespace:

  ```bash
  docker compose exec -T sbk-dashboard python -c \
    'import urllib.request; r=urllib.request.urlopen("http://host.docker.internal:9718/metrics", timeout=5); print(r.status); print(r.read(300).decode())'
  ```

  For a remote exporter, replace `host.docker.internal` with its routable DNS name or IP. Connection refused means
  the exporter is stopped or the port is wrong; a timeout normally means routing or firewall policy; HTTP 404 means
  the metrics path is wrong. Prometheus' detailed `lastError` is available with:

  ```bash
  docker compose exec -T sbk-dashboard python -c \
    'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:9090/api/v1/targets?state=active", timeout=5).read().decode())'
  ```
- If the generated Grafana hostname is wrong behind a proxy, set the authoritative `-grafana-url`.
- If a bind-mounted data directory is read-only, make it writable by UID/GID 10001 or use the named volume.
