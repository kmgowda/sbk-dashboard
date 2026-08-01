---
trigger: always_on
---

# SBK Dashboard Workspace Rule

Read and follow `/AGENTS.md` and `/docs/AGENT_GUIDE.md` before modifying this repository. Those files are canonical;
do not invent separate Windsurf-only architecture or workflow rules.

Preserve the Python 3.10+ non-containerized architecture, native Prometheus/Grafana process ownership, bounded
concurrency and memory, persistent seven-day Prometheus retention, deterministic dashboard-per-`host:port`
isolation, public/request-host Grafana links, and cross-platform behavior. Run the required lint, leak-sensitive,
coverage, and integration checks described in `AGENTS.md`.

