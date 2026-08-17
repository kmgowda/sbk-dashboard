<!--
Copyright (c) KMG. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
-->

# Devin Project Entry Point

The complete repository instructions are in [`AGENTS.md`](AGENTS.md), with implementation navigation and change
recipes in [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md). Read both before planning or editing.

Container packaging is supported, but it must preserve the native Prometheus/Grafana child-process architecture;
do not replace those servers with embedded libraries or separate service containers. Preserve bounded resources,
lifecycle ownership, persistent retention, endpoint isolation, public Grafana URL behavior, and cross-platform
native support. Use temporary data/ports for tests, clean every child process/container/volume created by tests, and
follow the definition of done in `AGENTS.md`.
