"""Live Docker lifecycle, persistence, routing, and dashboard smoke test."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


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
        self.volume = f"sbk-dashboard-smoke-data-{suffix}"
        self.captured_pids: set[int] = set()

    def run(self) -> None:
        command("docker", "volume", "create", self.volume)
        try:
            target_id = self._first_start()
            self._stop_and_remove()
            self._assert_processes_stopped()
            self._start()
            self._wait_until_healthy()
            targets = self._targets()
            if [target["id"] for target in targets] != [target_id]:
                raise AssertionError(f"Persisted target was not restored: {targets}")
            if self.expect_target_up:
                self._wait_for_target_up(target_id)
                self._assert_real_metrics_and_panels(target_id)
            self._wait_for_dashboard(targets[0]["dashboardUrl"])
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
            command("docker", "volume", "rm", "--force", self.volume, check=False)

    def _first_start(self) -> str:
        self._start()
        self._wait_until_healthy()
        status, landing = request(f"http://127.0.0.1:{self.dashboard_port}/")
        if status != 200 or b"SBK DASHBOARD" not in landing:
            raise AssertionError("Landing page is not reachable from the Docker host")
        bindings = json.loads(
            command(
                "docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", self.name
            ).stdout
        )
        if set(bindings) != {"3000/tcp", "9721/tcp"}:
            raise AssertionError(f"Unexpected published/exposed ports: {bindings}")
        status, payload = request(
            f"http://127.0.0.1:{self.dashboard_port}/api/targets",
            "POST",
            {
                "name": "Docker smoke target",
                "host": self.target_host,
                "port": self.target_port,
                "metricsPath": "/metrics",
            },
        )
        if status != 201:
            raise AssertionError(f"Target registration failed with HTTP {status}")
        target = json.loads(payload)
        expected_prefix = f"http://127.0.0.1:{self.grafana_port}/d/sbk-"
        if not target["dashboardUrl"].startswith(expected_prefix):
            raise AssertionError(f"Grafana URL is not host-accessible: {target['dashboardUrl']}")
        if self.expect_target_up:
            self._wait_for_target_up(target["id"])
            self._assert_real_metrics_and_panels(target["id"])
        self._wait_for_dashboard(target["dashboardUrl"])
        self._capture_processes()
        return target["id"]

    def _start(self) -> None:
        command(
            "docker",
            "run",
            "--detach",
            "--name",
            self.name,
            "--volume",
            f"{self.volume}:/var/lib/sbk-dashboard",
            "--publish",
            f"127.0.0.1:{self.dashboard_port}:9721",
            "--publish",
            f"127.0.0.1:{self.grafana_port}:3000",
            "--add-host",
            "host.docker.internal:host-gateway",
            self.image,
        )

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
        raise AssertionError(f"Real SBK target did not become up; last state={last_state}")

    def _assert_real_metrics_and_panels(self, target_id: str) -> None:
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

    def _capture_processes(self) -> None:
        identifiers, output = process_ids(self.name)
        for expected in ("sbk_dashboard.guardian", "/opt/prometheus/prometheus", "/opt/grafana/bin/grafana"):
            if expected not in output:
                raise AssertionError(f"Missing managed process {expected}:\n{output}")
        self.captured_pids.update(identifiers)

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
    arguments.add_argument("--dashboard-port", type=int, default=9721)
    arguments.add_argument("--grafana-port", type=int, default=3000)
    arguments.add_argument("--target-host", default="host.docker.internal")
    arguments.add_argument("--target-port", type=int, default=19718)
    arguments.add_argument("--expect-target-up", action="store_true")
    selected = arguments.parse_args()
    ContainerSmoke(
        selected.image,
        selected.dashboard_port,
        selected.grafana_port,
        selected.target_host,
        selected.target_port,
        selected.expect_target_up,
    ).run()
    print("Docker smoke validation passed")


if __name__ == "__main__":
    main()
