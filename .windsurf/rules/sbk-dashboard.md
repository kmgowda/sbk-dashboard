---
trigger: always_on
---

<!--
Copyright (c) KMG. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
-->

# SBK Dashboard Workspace Rule

Read and follow `/AGENTS.md` and `/docs/AGENT_GUIDE.md` before modifying this repository. Those files are canonical;
do not invent separate Windsurf-only architecture or workflow rules.

Preserve the documented Python 3.10+ control plane and native Prometheus/Grafana process ownership, including the
supported Linux Docker/Compose delivery of that same architecture. Keep bounded concurrency and memory, persistent
seven-day Prometheus retention, deterministic dashboard-per-`host:port` isolation, public/request-host Grafana
links, and cross-platform behavior. Run the required checks described in `AGENTS.md`.
