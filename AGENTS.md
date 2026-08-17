<!--
Copyright (c) KMG. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
-->

# SBK Dashboard Agent Instructions

These instructions apply to the entire repository. They are the canonical operating contract for Codex, Devin,
Windsurf, Cursor, Copilot, Claude, Gemini, and other software agents. Tool-specific instruction files must supplement
this file, never contradict or duplicate it. Read [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) before making a
non-trivial change.

## Mission and boundaries

`sbk-dashboard` is a Python 3 control plane for official native Prometheus and Grafana servers. It
accepts remote SBK/SBM Prometheus endpoints and creates one isolated Grafana dashboard per unique `host:port`.

Preserve these product boundaries:

- Python 3.10 or newer; the package must work in a standard environment, venv, and Conda.
- One Python control plane, one managed Prometheus server, and one managed Grafana server per application instance.
- Prometheus and Grafana are native child processes, not Python libraries and not embedded runtimes.
- Direct Python/Conda deployment and the supported Linux Docker/Compose package use the same one-control-plane,
  two-native-child-process architecture. Container packaging must not replace, embed, or split those services.
- Authentication remains disabled. `-auth true` must fail until authentication is deliberately implemented.
- The exact canonical SBK dashboard is packaged from
  `/root/projects/SBK/grafana/dashboards/sbk-dashboard.json` and then cloned/scoped per endpoint.
- Production is a continuously running single host/VM deployment. Favor bounded resources, explicit ownership,
  recoverable persistence, and deterministic lifecycle behavior.

Do not silently redesign these boundaries. A change to them needs explicit user approval and corresponding
architecture, migration, and operations documentation.

## Read before editing

Read `docs/AGENT_GUIDE.md` first, then use this order to build detailed context without scanning generated artifacts:

1. `docs/AGENT_GUIDE.md` for the code map, change recipes, and completion checklist.
2. `README.md` for user behavior, installation, CLI, and operations.
3. `docs/ARCHITECTURE.md` for invariants, concurrency, persistence, and process ownership.
4. `docs/INTERNALS.md` for module-level call paths, locks, generated files, and failure boundaries.
5. `docs/TESTING.md` for automated and real-SBK validation.
6. `docs/USAGE.md` when changing installation, environment, endpoint, upgrade, or operator behavior.
7. `docs/DOCKER.md` when changing container packaging, networking, security, or delivery.
8. The relevant source module and its matching test module.

Do not use `.coverage`, `.pytest_cache/`, `.ruff_cache/`, `build/`, `dist/`, `*.egg-info/`, downloaded tools, or
runtime data as source material.

## Non-negotiable invariants

### Endpoint identity and isolation

- Endpoint identity is the stable 16-character SHA-256 prefix of normalized lowercase `host:port`.
- The same host on a different port is a different endpoint and must receive a different dashboard UID and URL.
- Prometheus discovery attaches `sbk_endpoint_id`; every generated `SBK_*` PromQL selector must include that label.
- Generated UIDs use `sbk-<endpoint-id>` and dashboard files use the same stable identity.
- Reconciliation is deterministic and removes only generated dashboards that no longer correspond to registrations.
- Registration state and mappings must survive restart.

### Dashboard URL behavior

- With the default Grafana URL, API responses derive the Grafana hostname from the validated direct HTTP `Host`
  header. Public IP, DNS, loopback, and bracketed IPv6 access must produce matching clickable Grafana links.
- Preserve Grafana's configured scheme, port, and base path; never copy the management server port into Grafana URLs.
- An explicit `-grafana-url` or `SBK_DASHBOARD_GRAFANA_URL` is authoritative for proxies, TLS, or dedicated DNS.
- Never persist a request-specific public hostname into shared endpoint registration data.
- Validate untrusted host input before reflecting it into a URL.

### Persistence and retention

- The Python process stores registrations/configuration atomically; it does not implement its own sample database.
- Prometheus TSDB is the persistent time-series store. Default retention is seven days and is passed as
  `--storage.tsdb.retention.time=<days>d`, allowing Prometheus to clean expired blocks in the background.
- Missing/corrupt old history should produce a warning where recovery is possible; a remote target being down is
  non-fatal. Failure to start the metrics engine is fatal.
- Never delete a real user data directory during development or tests. Use a unique temporary directory.

### Concurrency and memory

- HTTP concurrency must remain a fixed worker pool with bounded admission and HTTP 503 backpressure.
- Do not introduce an unbounded thread-per-request server, executor queue, response read, request body, log file,
  endpoint collection, retry loop, or in-memory sample history.
- Publish target/status snapshots atomically. Keep lock scopes short and never perform slow network/process waits
  while holding data locks.
- Every acquired file, socket, response, process pipe, executor, and thread needs a deterministic close/join path.
- Supervisor waits must be interruptible by the shutdown event. Retries need a cap/backoff and must not log-spam.

### Native process lifecycle

- Constructors have no native-process side effects. `start()` acquires resources; `close()` is idempotent.
- Validate both configured port owners before stopping either existing service.
- CLI/environment Prometheus and Grafana ports are authoritative and non-replaceable: if occupied, report the
  identifiable owner and fail without stopping it. Only unspecified built-in defaults may receive automatic fallback.
- With `-continue false`, replace only verified Prometheus/Grafana listeners. Never kill unrelated or unidentified
  processes.
- With `-continue true`, attach only to healthy compatible services; attached services are observed but never
  restarted or terminated by this application.
- Owned services start in isolated process groups/sessions, are supervised, and stop in reverse dependency order.
- Startup failure must clean up partially acquired resources. Graceful termination is followed by bounded forceful
  cleanup of the full process tree.
- Persisted PID ownership must continue to defend against PID reuse using executable, creation time, and port.

### Cross-platform behavior

- Keep common logic OS-neutral. Isolate platform differences to runtime detection, archives, executable names, and
  process control.
- Preserve Linux, macOS, and Windows definitions for x86-64 and ARM64.
- Never add a shell-only runtime dependency to Python application behavior. Documentation may show platform-specific
  activation commands when alternatives are also documented.
- Downloaded archives require HTTPS, pinned SHA-256, traversal-safe extraction, partial-file cleanup, and atomic
  installation.
- Container bases require an official supported distribution, complete language patch version, and reviewed
  multi-architecture digest. Container CI uses an explicit stable runner label rather than a moving `-latest` label.
- Preserve outbound DNS, IPv4, and IPv6 endpoint scraping through the Compose bridge; IPv6 operation still depends
  on Docker-host and upstream routing.

## Source map

| Path | Responsibility |
|---|---|
| `src/sbk_dashboard/main.py` | Composition root, bootstrap, signals, startup output, shutdown |
| `src/sbk_dashboard/version.py` | Single source of truth for package and runtime version |
| `src/sbk_dashboard/config.py` | CLI/environment/default precedence, validation, platform/download definitions |
| `src/sbk_dashboard/contracts.py` | Shared operational defaults and bounded environment settings |
| `src/sbk_dashboard/endpoint_policy.py` | Endpoint identity, kind, path, name, and port policy |
| `src/sbk_dashboard/platforms.py` | Runtime and portable platform normalization |
| `src/sbk_dashboard/layout.py` | Portable-home and persistent runtime path construction |
| `src/sbk_dashboard/network.py` | Shared canonical IP literal and conservative DNS-name validation |
| `src/sbk_dashboard/web.py` | Bounded HTTP server, REST API, assets, request-host URL resolution |
| `src/sbk_dashboard/registry.py` | Endpoint validation, identity, limits, atomic registration persistence |
| `src/sbk_dashboard/monitoring.py` | Monitoring facade, configuration, reconciliation, supervision, target status |
| `src/sbk_dashboard/processes.py` | Lifecycle state machine, ownership registry, health strategy, process trees/logs |
| `src/sbk_dashboard/guardian.py` | Parent-death monitoring and orphaned native-process tree cleanup |
| `src/sbk_dashboard/windows_job.py` | Windows kill-on-close Job Object containment and suspended launch |
| `src/sbk_dashboard/provisioning.py` | Prometheus discovery and endpoint-scoped Grafana dashboard generation |
| `src/sbk_dashboard/bootstrap.py` | Native download, verification, extraction, and installation |
| `src/sbk_dashboard/files.py` | Atomic file/JSON primitives |
| `src/sbk_dashboard/models.py` | Immutable endpoint/status values |
| `src/sbk_dashboard/resources/` | Packaged dashboard, web assets, and download defaults |
| `scripts/sbk_dashboard_bootstrap.py` | Private-home runtime locking, caching, repair, and environment preparation |
| `scripts/install-portable.sh`, `scripts/Install-SbkDashboardPortable.ps1` | Python-free verified standalone fallback |
| `scripts/portable-bootstrap.properties` | Shared dependency-free release URL and bootstrap bounds |
| `scripts/sbk_dashboard_launcher.py` | Cross-platform foreground/background ownership, logs, and selective stop |
| `scripts/build_portable.py`, `scripts/sbk_dashboard_portable_entry.py` | Frozen archive construction and entry dispatch |
| `sbk-dashboard`, `sbk-dashboard.ps1`, `sbk-dashboard.cmd` | Root source-checkout command dispatchers |
| `Dockerfile`, `compose.yaml`, `compose.dev.yaml`, `compose.resources.yaml` | Non-root release image, production deployment, development build and optional resource overrides |
| `requirements/container-*.txt` | Exact Python build/runtime hashes and direct final-image OS package inventory |
| `docs/USAGE.md` | Operator environments, daily use, endpoints, backup, upgrades, and troubleshooting |
| `docs/INTERNALS.md` | Module ownership, call paths, locks, persisted formats, and failure boundaries |
| `tests/container_smoke.py` | Live container, dashboard, persistence, and lifecycle validation |
| `tests/compose_contract.py` | Resolved production/development Compose runtime-equivalence validation |
| `tests/` | Unit, integration, platform, lifecycle, backpressure, and resource tests |

## Development commands

Create either environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

```bash
conda env create -f environment.yml
conda activate sbk-dashboard
python -m pip install -e ".[dev]"
```

Run the fast required checks after every code change:

```bash
ruff check src tests scripts
mypy
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -q
git diff --check
```

Run the complete pre-commit validation for non-trivial changes:

```bash
coverage erase
COVERAGE_PROCESS_START=pyproject.toml coverage run -m pytest -q
coverage combine
coverage report
python -m build --no-isolation
```

Coverage must remain at or above the configured 60% floor. New branches and failure paths should receive focused
tests rather than relying only on the global percentage.

Socket tests require permission to bind temporary loopback ports. Use non-default ports and a temporary data
directory for live tests. Always stop children and remove only the exact temporary artifacts created by the test.

## Change discipline

- Keep the working tree's unrelated changes intact. Inspect `git status` before and after edits.
- Prefer small composition-oriented objects, immutable dataclasses, protocols for replaceable behavior, and explicit
  lifecycle methods. Do not add inheritance without a genuine substitutability requirement.
- Preserve command-line compatibility unless the user explicitly requests a breaking change.
- For a new option, update parsing, precedence/source reporting, validation, startup output, help, README, and tests.
- For a new persisted field, define backward-compatible loading and recovery behavior.
- For process/concurrency changes, test normal start/stop, startup failure, crash/restart, partial cleanup, repeated
  close, and attached-process behavior as applicable.
- For web/API changes, test methods, validation, size limits, error status, public/loopback host behavior, and XSS/URL
  safety as applicable. Use `textContent`, not `innerHTML`, for user-controlled browser values.
- For dashboard changes, keep the packaged canonical JSON byte-identical to the SBK source unless synchronizing an
  intentional upstream change. Verify its SHA-256 and all 53 panels, then test selector scoping.
- Update `README.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, and `docs/MIGRATION.md` when their contracts change.
- Do not claim macOS/Windows native validation from a Linux-only run. Unit-test platform resolution on Linux and
  clearly record which native operating systems still require smoke testing.

## Real SBK integration

The reference project is `/root/projects/SBK`. Its installed executable is normally
`/root/projects/SBK/build/install/sbk/bin/sbk`. A representative real test is:

```bash
/root/projects/SBK/build/install/sbk/bin/sbk \
  -class file \
  -file /tmp/sbk-dashboard-agent-test.bin \
  -writers 1 \
  -size 4096 \
  -seconds 45 \
  -records 1000 \
  -out PrometheusLogger \
  -context 9718/metrics
```

Register `127.0.0.1:9718/metrics`, then verify the target is `up`, real `SBK_*` series are ingested, all 53 panels
exist, the generated dashboard returns HTTP 200, and history/mappings survive a full restart. See
`docs/TESTING.md` for the complete procedure. Do not commit benchmark files, TSDB blocks, downloaded binaries, logs,
or generated runtime configuration.

## Completion checklist

Before declaring work complete:

- The requested behavior is implemented at the correct architectural boundary.
- Relevant success, failure, concurrency, lifecycle, and compatibility cases are tested.
- Ruff, leak-sensitive unittest, pytest/coverage, and `git diff --check` pass.
- Packaging succeeds when package contents or dependencies changed.
- Real native-stack/SBK validation is run when monitoring, provisioning, process, or API integration changed.
- No test child processes, listeners, large temporary files, or test data directories remain.
- User and agent documentation reflects the resulting behavior.
- The branch is based on the requested base, the working tree is clean, and commit/PR details accurately report what
  was actually tested.
