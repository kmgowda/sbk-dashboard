# syntax=docker/dockerfile:1

# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

ARG PYTHON_BASE_NAME=python:3.12.13-slim-trixie
ARG PYTHON_BASE_DIGEST=sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ARG PYTHON_BASE=${PYTHON_BASE_NAME}@${PYTHON_BASE_DIGEST}
ARG NATIVE_DOWNLOAD_RETRIES=3
ARG NATIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS=15
ARG NATIVE_DOWNLOAD_TIMEOUT_SECONDS=600

FROM ${PYTHON_BASE} AS package-builder
WORKDIR /source
COPY requirements/container-build.txt requirements/container-runtime.txt /requirements/
COPY LICENSE README.md pyproject.toml ./
COPY src ./src
RUN python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes \
      --requirement /requirements/container-build.txt \
    && python -m pip wheel --disable-pip-version-check --no-cache-dir --require-hashes --no-deps \
      --wheel-dir /wheels --requirement /requirements/container-runtime.txt \
    && python -m pip wheel --disable-pip-version-check --no-cache-dir --no-build-isolation --no-deps \
      --wheel-dir /wheels .

FROM ${PYTHON_BASE} AS native-download-base
COPY scripts/docker_safe_extract.py /usr/local/bin/docker-safe-extract
COPY scripts/resolve_native_artifact.py /usr/local/bin/resolve-native-artifact
COPY src/sbk_dashboard/resources/native-artifacts.json /usr/local/share/sbk-dashboard/native-artifacts.json
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

FROM native-download-base AS prometheus-tools
ARG TARGETARCH
ARG NATIVE_DOWNLOAD_RETRIES
ARG NATIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS
ARG NATIVE_DOWNLOAD_TIMEOUT_SECONDS
RUN --mount=type=cache,id=sbk-dashboard-prometheus-downloads,target=/var/cache/sbk-downloads,sharing=locked \
    set -eux; \
    target_arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${target_arch}" in \
      amd64) platform_id=linux-x86_64 ;; \
      arm64) platform_id=linux-arm64 ;; \
      *) echo "Unsupported Docker target architecture: ${target_arch}" >&2; exit 1 ;; \
    esac; \
    manifest=/usr/local/share/sbk-dashboard/native-artifacts.json; \
    prometheus_url="$(python /usr/local/bin/resolve-native-artifact "${manifest}" prometheus "${platform_id}" url)"; \
    prometheus_archive="$(python /usr/local/bin/resolve-native-artifact "${manifest}" prometheus "${platform_id}" fileName)"; \
    prometheus_sha="$(python /usr/local/bin/resolve-native-artifact "${manifest}" prometheus "${platform_id}" sha256)"; \
    max_download_bytes="$(python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["maxDownloadBytes"])' "${manifest}")"; \
    cached_archive="/var/cache/sbk-downloads/${prometheus_archive}"; \
    if ! echo "${prometheus_sha}  ${cached_archive}" | sha256sum --check --strict --status; then \
      rm -f "${cached_archive}" "${cached_archive}.part"; \
      curl --fail --location --retry "${NATIVE_DOWNLOAD_RETRIES}" --retry-all-errors \
        --connect-timeout "${NATIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS}" \
        --max-time "${NATIVE_DOWNLOAD_TIMEOUT_SECONDS}" \
        --max-filesize "${max_download_bytes}" \
        --output "${cached_archive}.part" \
        "${prometheus_url}"; \
      echo "${prometheus_sha}  ${cached_archive}.part" | sha256sum --check --strict; \
      mv "${cached_archive}.part" "${cached_archive}"; \
    fi; \
    mkdir -p /opt/prometheus; \
    python /usr/local/bin/docker-safe-extract "${cached_archive}" /opt/prometheus; \
    test -x /opt/prometheus/prometheus; \
    test -x /opt/prometheus/promtool

FROM native-download-base AS grafana-tools
ARG TARGETARCH
ARG NATIVE_DOWNLOAD_RETRIES
ARG NATIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS
ARG NATIVE_DOWNLOAD_TIMEOUT_SECONDS
RUN --mount=type=cache,id=sbk-dashboard-grafana-downloads,target=/var/cache/sbk-downloads,sharing=locked \
    set -eux; \
    target_arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${target_arch}" in \
      amd64) platform_id=linux-x86_64 ;; \
      arm64) platform_id=linux-arm64 ;; \
      *) echo "Unsupported Docker target architecture: ${target_arch}" >&2; exit 1 ;; \
    esac; \
    manifest=/usr/local/share/sbk-dashboard/native-artifacts.json; \
    grafana_url="$(python /usr/local/bin/resolve-native-artifact "${manifest}" grafana "${platform_id}" url)"; \
    grafana_archive="$(python /usr/local/bin/resolve-native-artifact "${manifest}" grafana "${platform_id}" fileName)"; \
    grafana_sha="$(python /usr/local/bin/resolve-native-artifact "${manifest}" grafana "${platform_id}" sha256)"; \
    max_download_bytes="$(python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["maxDownloadBytes"])' "${manifest}")"; \
    cached_archive="/var/cache/sbk-downloads/${grafana_archive}"; \
    if ! echo "${grafana_sha}  ${cached_archive}" | sha256sum --check --strict --status; then \
      rm -f "${cached_archive}" "${cached_archive}.part"; \
      curl --fail --location --retry "${NATIVE_DOWNLOAD_RETRIES}" --retry-all-errors \
        --connect-timeout "${NATIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS}" \
        --max-time "${NATIVE_DOWNLOAD_TIMEOUT_SECONDS}" \
        --max-filesize "${max_download_bytes}" \
        --output "${cached_archive}.part" \
        "${grafana_url}"; \
      echo "${grafana_sha}  ${cached_archive}.part" | sha256sum --check --strict; \
      mv "${cached_archive}.part" "${cached_archive}"; \
    fi; \
    mkdir -p /opt/grafana; \
    python /usr/local/bin/docker-safe-extract "${cached_archive}" /opt/grafana; \
    test -x /opt/grafana/bin/grafana

FROM ${PYTHON_BASE} AS runtime
ARG APPLICATION_VERSION=1.26.8.3
ARG VCS_REF=unknown
ARG BUILD_DATE=1970-01-01T00:00:00Z
ARG PYTHON_BASE_NAME
ARG PYTHON_BASE_DIGEST
LABEL org.opencontainers.image.title="SBK Dashboard" \
      org.opencontainers.image.description="SBK/SBM performance dashboard with managed Prometheus and Grafana" \
      org.opencontainers.image.source="https://github.com/kmgowda/sbk-dashboard" \
      org.opencontainers.image.documentation="https://github.com/kmgowda/sbk-dashboard/blob/main/docs/DOCKER.md" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${APPLICATION_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="${PYTHON_BASE_NAME}" \
      org.opencontainers.image.base.digest="${PYTHON_BASE_DIGEST}"
COPY requirements/container-os.txt /usr/local/share/sbk-dashboard/container-os-packages.txt
RUN apt-get update \
    && locked_packages="$(sed -e '/^#/d' -e '/^[[:space:]]*$/d' \
      /usr/local/share/sbk-dashboard/container-os-packages.txt)" \
    && apt-get install --yes --no-install-recommends ${locked_packages} \
    && while IFS= read -r requirement; do \
         case "${requirement}" in ''|'#'*) continue ;; esac; \
         package="${requirement%%=*}"; \
         expected="${requirement#*=}"; \
         actual="$(dpkg-query --show --showformat='${Version}' "${package}")"; \
         test "${actual}" = "${expected}"; \
       done < /usr/local/share/sbk-dashboard/container-os-packages.txt \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sbk-dashboard \
    && useradd --uid 10001 --gid 10001 --home-dir /var/lib/sbk-dashboard \
      --shell /usr/sbin/nologin sbk-dashboard \
    && install --directory --owner=10001 --group=10001 /var/lib/sbk-dashboard
COPY --from=package-builder /wheels /tmp/wheels
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index \
      --find-links /tmp/wheels /tmp/wheels/sbk_dashboard-*.whl \
    && installed_version="$(python -c 'from sbk_dashboard.version import VERSION; print(VERSION)')" \
    && test "${installed_version}" = "${APPLICATION_VERSION}" \
    && rm -rf /tmp/wheels
# Native installations are executable by the application user but remain immutable root-owned image content.
COPY --from=prometheus-tools /opt/prometheus /opt/prometheus
COPY --from=grafana-tools /opt/grafana /opt/grafana
ENV HOME=/var/lib/sbk-dashboard \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SBK_DASHBOARD_DATA_DIR=/var/lib/sbk-dashboard \
    SBK_DASHBOARD_DEFAULT_TARGET_HOST=host.docker.internal \
    SBK_DASHBOARD_BIND=0.0.0.0 \
    SBK_DASHBOARD_PROMETHEUS_BIN=/opt/prometheus/prometheus \
    SBK_DASHBOARD_PROMETHEUS_BIND=127.0.0.1 \
    SBK_DASHBOARD_GRAFANA_HOME=/opt/grafana \
    SBK_DASHBOARD_GRAFANA_BIND=0.0.0.0 \
    SBK_DASHBOARD_CONTAINER_HEALTH_TIMEOUT_SECONDS=3
USER 10001:10001
WORKDIR /var/lib/sbk-dashboard
VOLUME ["/var/lib/sbk-dashboard"]
EXPOSE 9721 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=4 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:9721/api/health', timeout=float(os.environ['SBK_DASHBOARD_CONTAINER_HEALTH_TIMEOUT_SECONDS'])).close()"]
ENTRYPOINT ["/usr/bin/tini", "--", "sbk-dashboard"]
