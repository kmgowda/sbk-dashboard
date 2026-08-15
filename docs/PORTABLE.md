# Portable installation and runtime home

SBK Dashboard supports two ready-to-run host distributions. A source checkout or source archive bootstraps itself
with Python 3.10 or newer. A standalone GitHub release archive includes the Python runtime and application, so it
does not require Python, pip, venv, or Conda on the destination host. Both paths run the same control plane and keep
Prometheus and Grafana as owned native child processes.

## Source checkout or source archive

Run one root command after clone or extraction:

```bash
./sbk-dashboard                         # foreground, console logs
./sbk-dashboard background              # detached, bounded file logs
./sbk-dashboard stop                    # stop all launcher-owned instances
./sbk-dashboard stop -port 19721        # stop one management-port instance
./sbk-dashboard repair                  # rebuild this checkout's runtime
```

On PowerShell replace `./sbk-dashboard` with `.\sbk-dashboard.ps1`. Command Prompt uses `sbk-dashboard.cmd`.
Application options after the command are preserved exactly.

The bootstrap validates Python 3.10+, reuses an active venv or Conda environment, and otherwise creates a private
venv. It installs pip with `ensurepip` if necessary, installs the local application and `psutil`, then executes the
normal cross-platform launcher. It acquires no launcher state or native process until preparation succeeds.

## Standalone release archive

Select the archive matching the destination:

| Archive suffix | Host |
|---|---|
| `linux-amd64.tar.gz` | Linux x86-64 |
| `macos-arm64.tar.gz` | macOS Apple silicon |
| `windows-amd64.zip` | Windows x86-64 |

Verify the archive using its adjacent `.sha256`, extract the complete directory, and run `sbk-dashboard` or
`sbk-dashboard.exe` from that directory. Do not copy only the executable: PyInstaller's `_internal` directory is
part of the application. The frozen executable starts in the foreground and accepts the normal CLI options.

Source bootstrap remains supported for Linux/macOS/Windows on x86-64 and ARM64 where Python 3.10+ and the official
native Prometheus/Grafana archives are available. The initial standalone build matrix is intentionally limited to
the three native GitHub-hosted runners above; emulated builds are not presented as native validation.

Release CI builds each archive on its target runner, writes a per-file `manifest.json`, creates an external SHA-256
file, uploads workflow artifacts, and attaches them to a published GitHub release. PyInstaller and its contributed
hooks are pinned to reviewed exact versions so release tooling does not drift between runs. The files are
checksummed but not code-signed or notarized by this repository; operators that require trust-policy signing must
apply and verify their organization's signing process before distribution.

## Persistent home and cache layout

The default portable home is `~/.sbk-dashboard` on every platform. Set `SBK_DASHBOARD_HOME` to an absolute or
user-relative dedicated directory before starting to relocate the complete layout. Filesystem roots and the user
home itself are rejected to prevent broad repair or cleanup operations.

```text
<home>/
|-- app/
|   |-- <version>/<os-architecture>/<source-fingerprint>/venv/  # source bootstrap only
|   `-- active/<interpreter-id>/<source-fingerprint>.json       # prepared active environments
|-- cache/pip/                                                   # shared package downloads/wheels
|-- current.json                                                 # last promoted private runtime
|-- launcher/                                                    # locks, state, background logs
|-- downloads/                                                   # shared verified native archives
|-- tools/                                                       # shared atomically installed native distributions
|-- instances/<management-port>/                                 # non-default instance data
|-- targets.json
`-- monitoring/                                                  # default instance configuration and data
```

`SBK_DASHBOARD_DATA_DIR` remains authoritative for application data, and `SBK_DASHBOARD_LAUNCHER_DIR` remains the
narrow launcher-state override. Setting either does not relocate the package/tool caches. Existing default-port
data under `~/.sbk-dashboard` remains in place.

Private runtime identity includes application version, OS/architecture, and a deterministic fingerprint of Python
source and packaging inputs. Installation uses an exclusive bounded lock, a sibling staging directory, and atomic
promotion. A concurrent start waits rather than observing partial files. A stale lock is removed only after its age
bound and owner-PID check both prove it abandoned. Two recent fingerprints are retained per version/platform; pip
and native download caches are shared, so restart and repair avoid downloads whenever cached artifacts suffice.

## Offline use, repair, and upgrades

The first source bootstrap requires access to the configured Python package index unless its required packages are
already cached. The first native application start requires access to the pinned Prometheus/Grafana download URLs
unless the archives or tools are already present. Later starts are local. A standalone bundle already includes
Python and application packages, but its first native-tool start has the same Prometheus/Grafana requirement.

`repair` rebuilds only the runtime selected for the current checkout and platform; it does not remove endpoint
registrations, Prometheus history, Grafana data, or unrelated launcher instances. For an upgrade, obtain the new
source/release, verify it, stop the old process, and run the new entry point against the same home. Versioned source
runtimes do not overwrite one another. Back up application data before an upgrade as described in
[`USAGE.md`](USAGE.md).

If Python is missing or older than 3.10, the source wrapper exits with an actionable installation message. If venv,
pip, package-index access, archive verification, or extraction fails, fix that cause and rerun `repair`; no partial
runtime is selected. Use `--help`/`--version` to validate a prepared command without starting native services.
