# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Live Docker lifecycle, persistence, routing, and dashboard smoke test."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


def available_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )


def request(url: str, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    operation = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(operation, timeout=5) as response:
        return response.status, response.read()


def wait_for(url: str, timeout: float = 180) -> bytes:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, body = request(url)
            if status == 200:
                return body
        except (OSError, urllib.error.HTTPError) as error:
            last_error = error
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def process_ids(name: str) -> tuple[set[int], str]:
    output = command("docker", "top", name, "-eo", "pid,args").stdout
    identifiers: set[int] = set()
    for line in output.splitlines()[1:]:
        fields = line.strip().split(maxsplit=1)
        if fields and fields[0].isdigit():
            identifiers.add(int(fields[0]))
    return identifiers, output


def pid_exists(identifier: int) -> bool:
    try:
        os.kill(identifier, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ContainerSmoke:
    def __init__(
        self,
        image: str,
        dashboard_port: int,
        grafana_port: int,
        target_host: str,
        target_port: int,
        expect_target_up: bool,
    ):
        suffix = uuid.uuid4().hex[:10]
        self.image = image
        self.dashboard_port = dashboard_port
        self.grafana_port = grafana_port
        self.target_host = target_host
        self.target_port = target_port
        self.expect_target_up = expect_target_up
        self.name = f"sbk-dashboard-smoke-{suffix}"
        self.source_volume = f"sbk-dashboard-smoke-data-{suffix}"
        self.restore_volume = f"sbk-dashboard-smoke-restore-{suffix}"
        self.active_volume = self.source_volume
        self.network = f"sbk-dashboard-smoke-net-{suffix}"
        self.exporters = (
            f"sbk-dashboard-smoke-exporter-v4-{suffix}",
            f"sbk-dashboard-smoke-exporter-v6-{suffix}",
        )
        self.target_endpoints: list[tuple[str, int]] = []
        self.captured_pids: set[int] = set()

    def run(self) -> None:
        command("docker", "volume", "create", self.source_volume)
        try:
            command("docker", "network", "create", "--ipv6", self.network)
            if self.expect_target_up:
                self.target_endpoints = [(self.target_host, self.target_port)]
            else:
                self.target_endpoints = self._start_test_exporters()
            target_ids, comparison = self._first_start()
            self._kill_and_remove()
            self._assert_processes_stopped()
            self._start()
            self._wait_until_healthy()
            self._assert_persisted_state(target_ids, comparison)
            self._capture_processes()
            self._stop_and_remove()
            self._assert_processes_stopped()
            self._restore_backup()
            self.active_volume = self.restore_volume
            self._start()
            self._wait_until_healthy()
            self._assert_persisted_state(target_ids, comparison)
            self._capture_processes()
            self._stop_and_remove()
            self._assert_processes_stopped()
        except BaseException:
            logs = command("docker", "logs", self.name, check=False).stdout
            if logs:
                print("Container logs:\n" + logs)
            raise
        finally:
            command("docker", "rm", "--force", self.name, check=False)
            for exporter in self.exporters:
                command("docker", "rm", "--force", exporter, check=False)
            command("docker", "volume", "rm", "--force", self.source_volume, check=False)
            command("docker", "volume", "rm", "--force", self.restore_volume, check=False)
            command("docker", "network", "rm", self.network, check=False)

    def _assert_persisted_state(
        self, target_ids: list[str], comparison: dict[str, Any] | None
    ) -> None:
        targets = self._targets()
        if {target["id"] for target in targets} != set(target_ids):
            raise AssertionError(f"Persisted target was not restored: {targets}")
        for target in targets:
            self._wait_for_target_up(target["id"])
            self._assert_metrics_and_panels(target["id"])
            self._wait_for_dashboard(target["dashboardUrl"])
        if comparison is not None:
            repeated_comparison = self._comparison_dashboard(list(reversed(target_ids)))
            if repeated_comparison != comparison:
                raise AssertionError(
                    f"Persisted comparison dashboard was not reused: {repeated_comparison}"
                )
            self._assert_comparison_dashboard(comparison, target_ids)

    def _restore_backup(self) -> None:
        command("docker", "volume", "create", self.restore_volume)
        command(
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "sh",
            "--volume",
            f"{self.source_volume}:/source:ro",
            "--volume",
            f"{self.restore_volume}:/restore",
            self.image,
            "-c",
            "set -eu; tar -C /source -cf - . | tar -C /restore -xf -",
        )

    def _first_start(self) -> tuple[list[str], dict[str, Any] | None]:
        self._start()
        self._wait_until_healthy()
        status, landing = request(f"http://127.0.0.1:{self.dashboard_port}/")
        if status != 200 or b"SBK DASHBOARD" not in landing:
            raise AssertionError("Landing page is not reachable from the Docker host")
        if b'value="host.docker.internal"' not in landing:
            raise AssertionError("Container landing page does not default to the Docker host gateway")
        self._assert_runtime_hardening()
        bindings = json.loads(
            command(
                "docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", self.name
            ).stdout
        )
        if set(bindings) != {"3000/tcp", "9721/tcp"}:
            raise AssertionError(f"Unexpected published/exposed ports: {bindings}")
        target_ids: list[str] = []
        for index, (host, port) in enumerate(self.target_endpoints, start=1):
            target = self._register_target(host, port, f"Docker smoke target {index}")
            target_ids.append(target["id"])
            self._wait_for_target_up(target["id"])
            self._assert_metrics_and_panels(target["id"])
            self._wait_for_dashboard(target["dashboardUrl"])
        comparison = self._comparison_dashboard(target_ids) if len(target_ids) >= 2 else None
        if comparison is not None:
            self._assert_comparison_dashboard(comparison, target_ids)
        self._capture_processes()
        return target_ids, comparison

    def _register_target(self, host: str, port: int, name: str) -> dict[str, Any]:
        registration = {"name": name, "host": host, "port": port, "metricsPath": "/metrics"}
        status, payload = request(
            f"http://127.0.0.1:{self.dashboard_port}/api/targets",
            "POST",
            registration,
        )
        if status != 201:
            raise AssertionError(f"Target registration failed with HTTP {status}")
        target: dict[str, Any] = json.loads(payload)
        expected_prefix = f"http://127.0.0.1:{self.grafana_port}/d/sbk-"
        if not target["dashboardUrl"].startswith(expected_prefix):
            raise AssertionError(f"Grafana URL is not host-accessible: {target['dashboardUrl']}")
        repeated_status, repeated_payload = request(
            f"http://127.0.0.1:{self.dashboard_port}/api/targets", "POST", registration
        )
        repeated_target = json.loads(repeated_payload)
        if repeated_status != 200 or repeated_target != target:
            raise AssertionError(
                "Exact repeated registration did not reuse its Docker dashboard: "
                f"status={repeated_status}, target={repeated_target}"
            )
        return target

    def _comparison_dashboard(self, target_ids: list[str]) -> dict[str, Any]:
        status, payload = request(
            f"http://127.0.0.1:{self.dashboard_port}/api/comparison-dashboard",
            "POST",
            {"targetIds": target_ids},
        )
        comparison: dict[str, Any] = json.loads(payload)
        if status != 200:
            raise AssertionError(f"Comparison dashboard request failed with HTTP {status}")
        return comparison

    def _assert_comparison_dashboard(
        self, comparison: dict[str, Any], target_ids: list[str]
    ) -> None:
        dashboard_id = comparison.get("dashboardId")
        expected_prefix = f"http://127.0.0.1:{self.grafana_port}/d/{dashboard_id}/"
        if not isinstance(dashboard_id, str) or not dashboard_id.startswith("sbk-comparison-"):
            raise AssertionError(f"Invalid comparison dashboard ID: {comparison}")
        if not str(comparison.get("dashboardUrl", "")).startswith(expected_prefix):
            raise AssertionError(f"Comparison URL is not host-accessible: {comparison}")
        self._wait_for_dashboard(str(comparison["dashboardUrl"]))
        dashboard = f"/var/lib/sbk-dashboard/monitoring/grafana/dashboards/{dashboard_id}.json"
        verification_script = (
            "import json\n"
            f"root=json.load(open({dashboard!r}, encoding='utf-8'))\n"
            "def panels(node):\n"
            "    if isinstance(node, dict):\n"
            "        return int('type' in node and 'title' in node) + sum(panels(v) for v in node.values())\n"
            "    if isinstance(node, list):\n"
            "        return sum(panels(v) for v in node)\n"
            "    return 0\n"
            "print(json.dumps({'panels': panels(root), "
            "'ids': root.get('sbkDashboardComparisonEndpointIds')}))\n"
        )
        details = json.loads(
            command("docker", "exec", self.name, "python", "-c", verification_script).stdout
        )
        if details != {"panels": 53, "ids": sorted(target_ids)}:
            raise AssertionError(f"Invalid generated comparison dashboard: {details}")

    def _start(self) -> None:
        docker_arguments = [
            "docker",
            "run",
            "--detach",
            "--name",
            self.name,
            "--network",
            self.network,
            "--read-only",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--pids-limit",
            "512",
            "--log-driver",
            "json-file",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "--volume",
            f"{self.active_volume}:/var/lib/sbk-dashboard",
            "--publish",
            f"127.0.0.1:{self.dashboard_port}:9721",
            "--publish",
            f"127.0.0.1:{self.grafana_port}:3000",
            "--add-host",
            "host.docker.internal:host-gateway",
        ]
        if self.grafana_port != 3000:
            docker_arguments.extend(
                ("--env", f"SBK_DASHBOARD_GRAFANA_URL=http://127.0.0.1:{self.grafana_port}")
            )
        command(*docker_arguments, self.image)

    def _wait_until_healthy(self) -> None:
        body = wait_for(f"http://127.0.0.1:{self.dashboard_port}/api/health")
        health = json.loads(body)
        if health["status"] != "ok":
            raise AssertionError(f"Container reported unhealthy state: {health}")
        wait_for(f"http://127.0.0.1:{self.grafana_port}/api/health")

    def _targets(self) -> list[dict[str, Any]]:
        _, body = request(f"http://127.0.0.1:{self.dashboard_port}/api/targets")
        return json.loads(body)

    def _wait_for_dashboard(self, url: str) -> None:
        wait_for(url, 60)

    def _wait_for_target_up(self, target_id: str) -> None:
        deadline = time.monotonic() + 60
        last_state = "missing"
        while time.monotonic() < deadline:
            targets = self._targets()
            selected = next((target for target in targets if target["id"] == target_id), None)
            last_state = selected["status"]["state"] if selected else "missing"
            if last_state == "up":
                return
            time.sleep(1)
        raise AssertionError(f"Target did not become up; last state={last_state}")

    def _assert_metrics_and_panels(self, target_id: str) -> None:
        expression = f'count({{sbk_endpoint_id="{target_id}",__name__=~"SBK_.+"}})'
        query = urllib.parse.urlencode({"query": expression})
        query_script = (
            "import urllib.request; "
            f"print(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/query?{query}', timeout=5)"
            ".read().decode())"
        )
        response = json.loads(command("docker", "exec", self.name, "python", "-c", query_script).stdout)
        result = response["data"]["result"]
        if not result or float(result[0]["value"][1]) < 1:
            raise AssertionError(f"No endpoint-scoped SBK series were ingested: {response}")
        dashboard = f"/var/lib/sbk-dashboard/monitoring/grafana/dashboards/sbk-{target_id}.json"
        panel_script = (
            "import json\n"
            f"root=json.load(open({dashboard!r}, encoding='utf-8'))\n"
            "def panels(node):\n"
            "    if isinstance(node, dict):\n"
            "        return int('type' in node and 'title' in node) + sum(panels(v) for v in node.values())\n"
            "    if isinstance(node, list):\n"
            "        return sum(panels(v) for v in node)\n"
            "    return 0\n"
            "print(panels(root))\n"
        )
        panels = int(command("docker", "exec", self.name, "python", "-c", panel_script).stdout)
        if panels != 53:
            raise AssertionError(f"Generated dashboard contains {panels} panels instead of 53")

    def _start_test_exporters(self) -> list[tuple[str, int]]:
        ipv4_script = self._exporter_script("0.0.0.0", 19718, False)
        ipv6_script = self._exporter_script("::", 19719, True)
        for name, script in zip(self.exporters, (ipv4_script, ipv6_script), strict=True):
            command(
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--network",
                self.network,
                "--entrypoint",
                "python",
                self.image,
                "-c",
                script,
            )
        ipv4_network = self._container_network(self.exporters[0])
        ipv6_network = self._container_network(self.exporters[1])
        ipv4 = str(ipv4_network["IPAddress"])
        ipv6 = str(ipv6_network["GlobalIPv6Address"])
        if not ipv4 or not ipv6:
            raise AssertionError(f"Docker did not assign remote test addresses: IPv4={ipv4!r}, IPv6={ipv6!r}")
        return [(ipv4, 19718), (ipv6, 19719)]

    def _container_network(self, name: str) -> dict[str, Any]:
        details = json.loads(command("docker", "inspect", name).stdout)[0]
        network: dict[str, Any] = details["NetworkSettings"]["Networks"][self.network]
        return network

    @staticmethod
    def _exporter_script(address: str, port: int, ipv6: bool) -> str:
        family = "socket.AF_INET6" if ipv6 else "socket.AF_INET"
        return (
            "import socket\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            " def do_GET(self):\n"
            "  body=b'# TYPE SBK_DockerSmoke gauge\\nSBK_DockerSmoke 1\\n'\n"
            "  self.send_response(200)\n"
            "  self.send_header('Content-Type','text/plain; version=0.0.4')\n"
            "  self.send_header('Content-Length',str(len(body)))\n"
            "  self.end_headers()\n"
            "  self.wfile.write(body)\n"
            " def log_message(self, format, *args): pass\n"
            "class Server(HTTPServer): pass\n"
            f"Server.address_family={family}\n"
            f"Server(({address!r},{port}),Handler).serve_forever()\n"
        )

    def _capture_processes(self) -> None:
        identifiers, output = process_ids(self.name)
        for expected in ("sbk_dashboard.guardian", "/opt/prometheus/prometheus", "/opt/grafana/bin/grafana"):
            if expected not in output:
                raise AssertionError(f"Missing managed process {expected}:\n{output}")
        self.captured_pids.update(identifiers)

    def _assert_runtime_hardening(self) -> None:
        details = json.loads(command("docker", "inspect", self.name).stdout)[0]
        if details["Config"]["User"] != "10001:10001":
            raise AssertionError(f"Container user is not fixed non-root UID/GID 10001: {details['Config']['User']}")
        if details["HostConfig"]["ReadonlyRootfs"] is not True:
            raise AssertionError("Container root filesystem is not read-only")
        if details["HostConfig"]["PidsLimit"] != 512:
            raise AssertionError(f"Container PID limit is not 512: {details['HostConfig']['PidsLimit']}")
        expected_log_config = {
            "Type": "json-file",
            "Config": {"max-file": "3", "max-size": "10m"},
        }
        if details["HostConfig"]["LogConfig"] != expected_log_config:
            raise AssertionError(
                f"Container log rotation is not bounded: {details['HostConfig']['LogConfig']}"
            )
        script = (
            "import json, os\n"
            "paths=['/opt/prometheus/prometheus','/opt/grafana/bin/grafana']\n"
            "print(json.dumps([{'path':p,'uid':os.stat(p).st_uid,'writable':os.access(p,os.W_OK)} "
            "for p in paths]))\n"
        )
        native_files = json.loads(
            command("docker", "exec", self.name, "python", "-c", script).stdout
        )
        if any(item["uid"] != 0 or item["writable"] for item in native_files):
            raise AssertionError(f"Native installations are not immutable root-owned content: {native_files}")

    def _kill_and_remove(self) -> None:
        command("docker", "kill", "--signal", "KILL", self.name)
        exit_code = command(
            "docker", "inspect", "--format", "{{.State.ExitCode}}", self.name
        ).stdout.strip()
        if exit_code != "137":
            raise AssertionError(f"Forced container termination returned unexpected exit={exit_code}")
        command("docker", "rm", self.name)

    def _stop_and_remove(self) -> None:
        command("docker", "stop", "--time", "30", self.name)
        exit_code = command(
            "docker", "inspect", "--format", "{{.State.ExitCode}}", self.name
        ).stdout.strip()
        if exit_code != "0":
            raise AssertionError(f"Container did not stop cleanly: exit={exit_code}")
        command("docker", "rm", self.name)

    def _assert_processes_stopped(self) -> None:
        deadline = time.monotonic() + 10
        remaining = set(self.captured_pids)
        while remaining and time.monotonic() < deadline:
            remaining = {identifier for identifier in remaining if pid_exists(identifier)}
            if remaining:
                time.sleep(0.1)
        if remaining:
            raise AssertionError(f"Container child processes survived shutdown: {sorted(remaining)}")
        self.captured_pids.clear()


def main() -> None:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--image", default="sbk-dashboard:test")
    arguments.add_argument("--dashboard-port", type=int)
    arguments.add_argument("--grafana-port", type=int)
    arguments.add_argument("--target-host", default="host.docker.internal")
    arguments.add_argument("--target-port", type=int, default=19718)
    arguments.add_argument("--expect-target-up", action="store_true")
    selected = arguments.parse_args()
    dashboard_port = selected.dashboard_port or available_host_port()
    grafana_port = selected.grafana_port or available_host_port()
    while grafana_port == dashboard_port:
        grafana_port = available_host_port()
    ContainerSmoke(
        selected.image,
        dashboard_port,
        grafana_port,
        selected.target_host,
        selected.target_port,
        selected.expect_target_up,
    ).run()
    print("Docker smoke validation passed")


if __name__ == "__main__":
    main()
