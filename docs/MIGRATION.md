# Migration from the Java implementation

The Python rewrite preserves the external contract:

- the command name remains `sbk-dashboard`;
- all command-line options and environment-variable precedence remain available;
- default ports and seven-day retention remain unchanged;
- existing `targets.json` field names and endpoint IDs remain readable;
- the monitoring directory, Prometheus TSDB, Grafana data, generated dashboards, and mappings remain in place;
- the exact canonical Grafana dashboard remains packaged.

Build and runtime requirements changed from JDK 25 plus Gradle to Python 3.10+ plus `pip` or Conda. Remove Java launch
scripts from service definitions and point them at the environment's generated `sbk-dashboard` command.

Example systemd command after installing into `/opt/sbk-dashboard-venv`:

```ini
ExecStart=/opt/sbk-dashboard-venv/bin/sbk-dashboard -data /var/lib/sbk-dashboard
```

Stop the Java version before starting the Python version. The default `-continue false` behavior can replace old
Prometheus and Grafana processes safely, but an orderly migration avoids an unnecessary forced service transition.

`SBK_JAVA_HOME` and `JAVA_HOME` no longer affect the dashboard. No data migration is required when the same
`SBK_DASHBOARD_DATA_DIR` is selected.
