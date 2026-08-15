# syntax=docker/dockerfile:1

ARG PYTHON_BASE=python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

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
      curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 --max-time 600 \
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
      curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 --max-time 600 \
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
ARG APPLICATION_VERSION=1.26.8.2
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="SBK Dashboard" \
      org.opencontainers.image.description="SBK/SBM performance dashboard with managed Prometheus and Grafana" \
      org.opencontainers.image.source="https://github.com/kmgowda/sbk-dashboard" \
      org.opencontainers.image.version="${APPLICATION_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini \
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
    SBK_DASHBOARD_GRAFANA_BIND=0.0.0.0
USER 10001:10001
WORKDIR /var/lib/sbk-dashboard
VOLUME ["/var/lib/sbk-dashboard"]
EXPOSE 9721 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=4 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9721/api/health', timeout=3).close()"]
ENTRYPOINT ["/usr/bin/tini", "--", "sbk-dashboard"]
