# Portable installation and runtime home

SBK Dashboard supports two ready-to-run host distributions. A source checkout uses Python 3.10+ when available and
otherwise downloads its exact-version standalone GitHub release. The standalone archive includes Python, the
application, `psutil`, and the lifecycle launcher, so Python, pip, venv, and Conda are optional on the destination.
Both paths run the same control plane and keep Prometheus and Grafana as owned native child processes.

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

The thin OS-specific wrappers delegate interpreter selection and minimum-version checks to one shared Unix helper
or one shared PowerShell helper. This keeps active-venv/Conda precedence and bootstrap behavior identical across
foreground, background, stop, and repair commands.

The bootstrap validates Python 3.10+, reuses an active venv or Conda environment, and otherwise creates a private
venv. It installs pip with `ensurepip` if necessary, installs the local application and `psutil`, then executes the
normal cross-platform launcher. If Python is absent or too old, the dependency-free OS wrapper selects its platform,
downloads the archive and adjacent SHA-256 from the release matching `version.py`, validates and atomically installs
it, and executes the same arguments through the frozen lifecycle launcher. It acquires no launcher state or native
process until preparation succeeds.

Bootstrap deliberately does not install Conda or modify the system Python. Each invocation reports the detected
OS/release/architecture, Python implementation/version/executable, selected active/private/standalone environment,
runtime location, portable home, and whether preparation created a fresh environment, reused the saved validated
environment, or repaired it. This report is emitted before launcher ownership or native services are acquired.

On Linux/macOS this fallback requires standard `tar` plus `curl` or `wget`; Windows uses PowerShell 5.1+ and .NET.
Set `SBK_DASHBOARD_PORTABLE_BASE_URL` to an HTTPS release mirror containing the same filenames and checksum files.
The default is the exact `v<version>` GitHub release, never a mutable latest-release URL. An unreleased checkout must
use Python or a mirror populated with matching artifacts.

## Standalone release archive

Select the archive matching the destination:

| Archive suffix | Host |
|---|---|
| `linux-amd64.tar.gz` | Linux x86-64 |
| `macos-arm64.tar.gz` | macOS Apple silicon |
| `windows-amd64.zip` | Windows x86-64 |

Verify the archive using its adjacent `.sha256`, extract the complete directory, and run `sbk-dashboard` or
`sbk-dashboard.exe` from that directory. Do not copy only the executable: PyInstaller's `_internal` directory is
part of the application. The frozen executable supports foreground, background, selective/all-instance stop, and
normal application options. Frozen background children and watchers re-enter internal executable modes instead of
assuming a separately installed Python interpreter.

Source bootstrap remains supported for Linux/macOS/Windows on x86-64 and ARM64 where Python 3.10+ and the official
native Prometheus/Grafana archives are available. The initial standalone build matrix is intentionally limited to
the three native GitHub-hosted runners above; emulated builds are not presented as native validation.

Release CI builds each archive on its target runner, writes a per-file `manifest.json`, creates an external SHA-256
file, uploads workflow artifacts, and attaches them to a published GitHub release. PyInstaller and its contributed
hooks are pinned to reviewed exact versions so release tooling does not drift between runs. The files are
checksummed and the workflow refuses to overwrite an existing release asset. They are not code-signed or notarized
by this repository; operators that require trust-policy signing must
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
|-- cache/releases/                                              # verified standalone archives
|-- distributions/<version>/<platform>/<archive>/                # standalone runtime
|-- current.json                                                 # last promoted private runtime
|-- launcher/                                                    # bootstrap locks, state, background logs
|-- downloads/                                                   # shared verified native archives
|-- tools/                                                       # shared atomically installed native distributions
|-- instances/<management-port>/                                 # non-default instance data
|-- targets.json
`-- monitoring/                                                  # default instance configuration and data
```

`SBK_DASHBOARD_DATA_DIR` remains authoritative for application data, and `SBK_DASHBOARD_LAUNCHER_DIR` remains the
narrow launcher-state override. Setting either does not relocate the package/tool caches. Existing default-port
data under `~/.sbk-dashboard` remains in place.

Private source-runtime identity includes application version, OS/architecture, and a deterministic fingerprint of Python
source and packaging inputs. Installation uses an exclusive bounded lock, a sibling staging directory, and atomic
promotion. A concurrent start waits rather than observing partial files. A stale lock is removed only after its age
bound and owner-PID check both prove it abandoned. Two recent fingerprints are retained per version/platform; pip
and native download caches are shared, so restart and repair avoid downloads whenever cached artifacts suffice.
Standalone identity includes the exact version, platform, archive name, archive SHA-256, and executable SHA-256
marker. The executable hash is rechecked before every cached launch. Installation
uses a per-version/platform lock, bounded download, safe archive inspection, sibling staging, and atomic directory
promotion. A failed checksum, unsafe entry, interruption, or failed promotion cannot select the partial runtime.

## Offline use, repair, and upgrades

The first Python source bootstrap requires access to the configured package index unless dependencies are cached.
Without Python, the first start requires the exact-version standalone release or a configured mirror. The first
native application start separately requires the pinned Prometheus/Grafana downloads unless already cached. Once
the standalone runtime and native tools have been prepared, later starts require neither Python nor network access.
A completely offline first start requires a pre-populated portable home or separately transferred release/native
archives; the Git source tree deliberately does not contain large platform binaries.

`repair` rebuilds only the runtime selected for the current checkout and platform; it does not remove endpoint
registrations, Prometheus history, Grafana data, or unrelated launcher instances. For an upgrade, obtain the new
source/release, verify it, stop the old process, and run the new entry point against the same home. Versioned source
runtimes do not overwrite one another. Back up application data before an upgrade as described in
[`USAGE.md`](USAGE.md).

`repair` is a source-checkout wrapper operation: it rebuilds a Python runtime or redownloads/replaces the standalone
fallback. When running an extracted release executable directly, replace its directory from the verified archive.

If Python is missing or older than 3.10, the source wrapper reports the standalone fallback it selected. If venv,
pip, package-index/release access, archive verification, or extraction fails, fix that cause and rerun `repair`; no
partial runtime is selected and an existing valid runtime remains available. Use `--help`/`--version` to validate a
prepared command without starting native services. Release archives are checksummed but are not yet code-signed or
notarized; environments requiring stronger provenance must apply their organization signing policy.
