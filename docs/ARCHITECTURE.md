# Architecture

```text
                                       one Collector per host:port
 Remote SBK/SBM /metrics  <── HTTP ──  virtual scrape thread
                                              │
                                              v
                                  Prometheus text parser (Java)
                                              │
                                              v
                       endpoint-partitioned primitive ring-buffer series
                            │         │                 │
                            v         v                 v
                   append-only    dashboard JSON API   status/inventory API
                 segment journal          │                 │
                            │              └────────┬────────┘
                            │                       v
                            └── startup replay ─> dedicated browser dashboard
```

The complete runtime is one JVM. No operating-system child processes or external monitoring servers participate.

## Registration lifecycle

1. Validate and normalize hostname, port, metrics path, and SBK/SBM kind.
2. Hash normalized `host:port` to produce the stable endpoint identifier.
3. Atomically persist the endpoint registry.
4. Create an independent scheduled collector and run its first scrape immediately.
5. Store each unique metric-name/label combination in that endpoint's repository partition.
6. Append the complete scrape as a checksummed binary frame in the endpoint's active segment.
7. Return `/dashboard.html?id=<id>` as the dedicated dashboard URL.

Deletion cancels the endpoint schedule, releases its retained series, closes its writer, and deletes only its exact
persisted endpoint partition.

## Concurrency

- The management HTTP server uses a virtual thread per request.
- Scrape requests use virtual threads and a shared JDK `HttpClient`.
- Each endpoint has a separate fixed-delay schedule.
- An endpoint-local atomic flag prevents scrape overlap.
- The registry exposes immutable snapshots to readers.
- Endpoint and metric maps are concurrent.
- Each primitive time-series ring synchronizes only its own append/snapshot operations.

A slow or unavailable endpoint therefore cannot block collection from another endpoint.

## Persistence and recovery

Every endpoint has independent append-only segment files. A segment starts with a format magic and version. Each
scrape frame contains its encoded timestamp, metrics, labels and values, preceded by its length and CRC32. Writes are
forced to the filesystem before durability is reported.

At startup, segments are ordered by their timestamp-based names and replayed into the same bounded primitive rings
used for live data. Recovery stops at an invalid length, checksum mismatch, incomplete frame, or corrupt tail while
retaining every earlier valid frame. New writes always begin in a new segment, so recovery never mutates historical
files.

Segments roll at a configurable size or after one hour, whichever occurs first, and expire by last-modified time.
Retention is configured in whole days, defaults to seven days, and is applied independently to every endpoint
partition. A dedicated hourly maintenance task scans every partition independently of scrape activity. It closes an
inactive writer when its segment has expired and then deletes that segment, allowing retention to work even while a
remote endpoint is offline. The next successful scrape opens a new segment.

## Capacity control

Every time series owns fixed `long[]` and `double[]` arrays. Once full, it overwrites its oldest sample. This avoids
per-sample objects and makes the memory ceiling a configuration decision rather than an unbounded consequence of
uptime. Dashboard queries downsample to 10–1,000 points per series.

## Failure states

- `pending`: registered but no scrape has completed.
- `up`: the last HTTP request returned 2xx and contained at least one valid sample.
- `down`: connection failure, timeout, non-2xx response, empty exposition, or a response over 16 MiB.

Previously collected data remains visible when an endpoint goes down, while the status and failure detail change.
Persistent-history initialization and recovery failures are warnings rather than startup failures. The service
continues with live in-memory collection when history is unavailable or partially damaged.
