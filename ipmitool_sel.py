#!/usr/bin/env python3
import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Tuple

import prometheus_client

EVENT_LOG = None
IPMITOOL_EXIT_CODE = None


def run_smartctl_cmd(args: list) -> Tuple[str, int]:
    """
    Runs the smartctl command on the system
    """
    out = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()

    # exit code can be != 0 even if the command returned valid data
    # see EXIT STATUS in
    # https://www.smartmontools.org/browser/trunk/smartmontools/smartctl.8.in
    if out.returncode != 0:
        stdout_msg = stdout.decode("utf-8") if stdout is not None else ""
        stderr_msg = stderr.decode("utf-8") if stderr is not None else ""
        print(f"WARNING: Command returned exit code {out.returncode}. Stdout: '{stdout_msg}' Stderr: '{stderr_msg}'")

    return stdout.decode("utf-8"), out.returncode


def ipmitool_sel():
    """
    Runs the ipmitool command to list all bmc system event log
    """
    lines, exit_code = run_smartctl_cmd(["ipmitool", "sel", "list"])
    IPMITOOL_EXIT_CODE.set(exit_code)
    if exit_code != 0:
        return

    EVENT_LOG.clear()
    for line in lines.split("\n"):
        if len(line) < 5:
            continue

        try:
            index, date, time, title, content, _ = [i.strip() for i in line.split("|")]
            EVENT_LOG.labels(
                int(index, 16),
                int((datetime.strptime(f"{date} {time}", r"%m/%d/%Y %H:%M:%S") - datetime(1970, 1, 1)).total_seconds()),
                title,
                content,
            ).set(1)
        except Exception as e:
            logging.error(e)


def collect(registry: prometheus_client.CollectorRegistry):
    """
    Collect all drive metrics and save them as Gauge type
    """
    global EVENT_LOG, IPMITOOL_EXIT_CODE
    if EVENT_LOG is None:
        EVENT_LOG = prometheus_client.Gauge(
            "ipmitool_event_log",
            documentation="BMC Event log",
            labelnames=("index", "timestamp", "title", "content"),
            registry=registry,
        )
    if IPMITOOL_EXIT_CODE is None:
        IPMITOOL_EXIT_CODE = prometheus_client.Gauge("ipmitool_exit_code", documentation="", registry=registry)

    ipmitool_sel()


def main():
    """
    Starts a server and exposes the metrics
    """

    # Validate configuration
    exporter_address = os.environ.get("SMARTCTL_EXPORTER_ADDRESS", "0.0.0.0")
    exporter_port = int(os.environ.get("SMARTCTL_EXPORTER_PORT", 9902))
    refresh_interval = int(os.environ.get("SMARTCTL_REFRESH_INTERVAL", 60))

    # Start Prometheus server
    prometheus_client.start_http_server(exporter_port, exporter_address)
    print(f"Server listening in http://{exporter_address}:{exporter_port}/metrics")

    while True:
        collect(prometheus_client.REGISTRY)
        time.sleep(refresh_interval)


if __name__ == "__main__":
    main()
