# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS package-builder
WORKDIR /source
COPY LICENSE README.md pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --disable-pip-version-check --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-slim-bookworm AS native-tools
ARG TARGETARCH
ARG PROMETHEUS_VERSION=3.10.0
ARG PROMETHEUS_AMD64_SHA256=41c50d97bb6a181623fc89d3fe61d0cc68ee69cc93da9091b8bba005f9690122
ARG PROMETHEUS_ARM64_SHA256=f6fc81c7955b6e1ddd532c62b55896f7e7a61d997a3817ac3534114f2dd33ab1
ARG GRAFANA_VERSION=12.4.1
ARG GRAFANA_BUILD=22846628243
ARG GRAFANA_AMD64_SHA256=55d6d71c813dd7426fe0b8d3a237e8d4ee4bf8a806ff90494207e146473ceb41
ARG GRAFANA_ARM64_SHA256=7338a20b4757e5e37a25fb42855828f7627bc830c293d77da1cf6279103044ac
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    target_arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${target_arch}" in \
      amd64) native_arch=amd64; prometheus_sha="${PROMETHEUS_AMD64_SHA256}"; grafana_sha="${GRAFANA_AMD64_SHA256}" ;; \
      arm64) native_arch=arm64; prometheus_sha="${PROMETHEUS_ARM64_SHA256}"; grafana_sha="${GRAFANA_ARM64_SHA256}" ;; \
      *) echo "Unsupported Docker target architecture: ${target_arch}" >&2; exit 1 ;; \
    esac; \
    prometheus_archive="prometheus-${PROMETHEUS_VERSION}.linux-${native_arch}.tar.gz"; \
    grafana_archive="grafana_${GRAFANA_VERSION}_${GRAFANA_BUILD}_linux_${native_arch}.tar.gz"; \
    curl --fail --location --retry 3 --output "/tmp/${prometheus_archive}" \
      "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/${prometheus_archive}"; \
    curl --fail --location --retry 3 --output "/tmp/${grafana_archive}" \
      "https://dl.grafana.com/grafana/release/${GRAFANA_VERSION}/${grafana_archive}"; \
    echo "${prometheus_sha}  /tmp/${prometheus_archive}" | sha256sum --check --strict; \
    echo "${grafana_sha}  /tmp/${grafana_archive}" | sha256sum --check --strict; \
    mkdir -p /opt/prometheus /opt/grafana; \
    tar --extract --gzip --file "/tmp/${prometheus_archive}" --strip-components=1 --directory /opt/prometheus; \
    tar --extract --gzip --file "/tmp/${grafana_archive}" --strip-components=1 --directory /opt/grafana; \
    test -x /opt/prometheus/prometheus; \
    test -x /opt/prometheus/promtool; \
    test -x /opt/grafana/bin/grafana; \
    rm -f "/tmp/${prometheus_archive}" "/tmp/${grafana_archive}"

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG APPLICATION_VERSION=1.26.8.1
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
    && rm -rf /tmp/wheels
COPY --from=native-tools --chown=10001:10001 /opt/prometheus /opt/prometheus
COPY --from=native-tools --chown=10001:10001 /opt/grafana /opt/grafana
ENV HOME=/var/lib/sbk-dashboard \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SBK_DASHBOARD_DATA_DIR=/var/lib/sbk-dashboard \
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
