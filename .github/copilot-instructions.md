<!--
Copyright (c) KMG. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
-->

# Copilot Repository Instructions

Before proposing or editing code, read and follow `/AGENTS.md` and `/docs/AGENT_GUIDE.md`. They are the canonical
repository instructions and override this discovery file.

Core constraints: this is a Python 3.10+ control plane for native Prometheus and Grafana, with direct and supported
single-container delivery; preserve bounded concurrency, explicit owned-versus-attached process lifecycle, seven-day
Prometheus retention, deterministic per-`host:port` dashboard isolation, request-reachable public Grafana URLs,
cross-platform behavior, and exact canonical SBK dashboard content. Container packaging must retain the same native
child-process ownership architecture. Add focused tests and run the validation commands in `AGENTS.md`.
