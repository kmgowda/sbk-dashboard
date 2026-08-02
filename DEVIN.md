# Devin Project Entry Point

The complete repository instructions are in [`AGENTS.md`](AGENTS.md), with implementation navigation and change
recipes in [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md). Read both before planning or editing.

Do not replace the native Prometheus/Grafana architecture with containers or embedded libraries. Preserve bounded
resources, lifecycle ownership, persistent retention, endpoint isolation, public Grafana URL behavior, and
cross-platform support. Use temporary data/ports for tests, clean every child process, and follow the definition of
done in `AGENTS.md`.

