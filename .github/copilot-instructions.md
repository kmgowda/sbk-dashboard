# Copilot Repository Instructions

Before proposing or editing code, read and follow `/AGENTS.md` and `/docs/AGENT_GUIDE.md`. They are the canonical
repository instructions and override this discovery file.

Core constraints: this is a Python 3.10+ non-containerized control plane for native Prometheus and Grafana; preserve
bounded concurrency, explicit owned-versus-attached process lifecycle, seven-day Prometheus retention, deterministic
per-`host:port` dashboard isolation, request-reachable public Grafana URLs, cross-platform behavior, and exact
canonical SBK dashboard content. Add focused tests and run the validation commands in `AGENTS.md`.

