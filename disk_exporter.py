import time
import os
import logging
from pathlib import Path

import prometheus_client

from megacli import collect as megacli_collect
from smartprom import collect as smartctl_collect
from ipmitool_sel import collect as ipmitool_sel_collect
from perccli import collect as perccli_collect

METRICS_PATH = Path("/tmp/metrics/disk_exporter.prom")

if __name__ == "__main__":
    METRICS_PATH.parent.mkdir(exist_ok=True)

    # Validate configuration
    exporter_address = os.environ.get("SMARTCTL_EXPORTER_ADDRESS", "0.0.0.0")
    exporter_port = int(os.environ.get("SMARTCTL_EXPORTER_PORT", 8101))
    refresh_interval = int(os.environ.get("SMARTCTL_REFRESH_INTERVAL", 60))

    # disable component
    collectors = [
        (os.environ.get("DISABLE_SMARTPROM", False), smartctl_collect),
        # 由于该命令会导致服务器卡死几分钟，代码默认禁用。
        # (os.environ.get("DISABLE_MEGACLI", False), megacli_collect),
        (os.environ.get("DISABLE_IPMITOOL", False), ipmitool_sel_collect),
        (os.environ.get("DISABLE_PERCCLI", False), perccli_collect),
    ]

    # Start Prometheus server
    prometheus_client.start_http_server(exporter_port, exporter_address)
    registry = prometheus_client.CollectorRegistry()
    print(f"Server listening in http://{exporter_address}:{exporter_port}/metrics")

    def try_collect(collect_fun):
        try:
            collect_fun(registry)
        except Exception as e:
            logging.warning(e)

    last_update_timestamp = prometheus_client.Gauge("disk_exporter_last_update_time_seconds", "", registry=registry)
    while True:
        for diable, collector in collectors:
            if not diable:
                try_collect(collector)

        last_update_timestamp.set(time.time())
        prometheus_client.write_to_textfile(METRICS_PATH, registry)
        time.sleep(refresh_interval)
