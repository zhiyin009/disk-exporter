#!/usr/bin/env python3
"""
Script to parse StorCLI's JSON output and expose
MegaRAID health as Prometheus metrics.
Tested against StorCLI 'Ver 1.14.12 Nov 25, 2014'.
StorCLI reference manual:
http://docs.avagotech.com/docs/12352476
Advanced Software Options (ASO) not exposed as metrics currently.
JSON key abbreviations used by StorCLI are documented in the standard command
output, i.e.  when you omit the trailing 'J' from the command.
Formatting done with YAPF:
$ yapf -i --style '{COLUMN_LIMIT: 99}' storcli.py
"""

from __future__ import print_function
from datetime import datetime
import collections
import json
import os
import shlex
import subprocess
import re
import time
import logging

import prometheus_client

DESCRIPTION = """Parses StorCLI's JSON output and exposes MegaRAID health as
    Prometheus metrics."""
VERSION = "0.0.3"
REGISTRY = prometheus_client.REGISTRY

storcli_path = "/usr/bin/perccli64"
metric_prefix = "megacli_"
metric_list = {}


def collect(registry: prometheus_client.CollectorRegistry):
    """main"""
    global storcli_path, REGISTRY
    REGISTRY = registry
    data = get_storcli_json("/cALL show all J")

    for timeseries in metric_list.values():
        timeseries.clear()

    try:
        # All the information is collected underneath the Controllers key
        data = data["Controllers"]

        for controller in data:
            response = controller["Response Data"]

            handle_common_controller(response)
            if response["Version"]["Driver Name"] == "megaraid_sas":
                handle_megaraid_controller(response)
            elif response["Version"]["Driver Name"] == "mpt3sas":
                handle_sas_controller(response)
    except KeyError:
        pass


def handle_common_controller(response):
    (controller_index, baselabel) = get_basic_controller_info(response)
    controller_bios_version = f'{baselabel}, "bios_version":"{str(response["Version"]["Bios Version"]).strip()}", "serial_number":"","firmware_version":"","package_build":"", "product_name":""'
    controller_serial_number = f'{baselabel},"bios_version":"", "serial_number":"{str(response["Basics"]["Serial Number"]).strip()}","firmware_version":"","package_build":"", "product_name":""'
    controller_firmware_version = f'{baselabel}, "bios_version":"", "serial_number":"","firmware_version":"{str(response["Version"]["Firmware Version"]).strip()}","package_build":"", "product_name":""'
    controller_package_build = f'{baselabel}, "bios_version":"", "serial_number":"","firmware_version":"","package_build":"{str(response["Version"]["Firmware Package Build"]).strip()}", "product_name":""'
    controller_product_name = f'{baselabel},"bios_version":"", "serial_number":"","firmware_version":"","package_build":"", "product_name":"{str(response["Basics"]["Model"]).strip()}"'

    add_metric("controller", controller_serial_number, 1)
    add_metric("controller", controller_product_name, 1)
    add_metric("controller", controller_bios_version, 1)
    add_metric("controller", controller_firmware_version, 1)
    add_metric("controller", controller_package_build, 1)

    # Split up string to not trigger CodeSpell issues
    if "ROC temperature(Degree Celc" + "ius)" in response["HwCfg"].keys():
        response["HwCfg"]["ROC temperature(Degree Celsius)"] = response["HwCfg"].pop("ROC temperature(Degree Celc" + "ius)")
    add_metric(
        "controller_temperature_celsius",
        baselabel,
        int(response["HwCfg"]["ROC temperature(Degree Celsius)"]),
    )


def handle_sas_controller(response):
    (controller_index, baselabel) = get_basic_controller_info(response)
    add_metric("healthy", baselabel, int(response["Status"]["Controller Status"] == "OK"))
    add_metric("ports", baselabel, response["HwCfg"]["Backend Port Count"])
    try:
        # The number of physical disks is half of the number of items in this dict
        # Every disk is listed twice - once for basic info, again for detailed info
        add_metric(
            "drives",
            f'{baselabel}, state="DisksTotal", type="physical"',
            len(response["Physical Device Information"].keys()) / 2,
        )
    except AttributeError:
        pass

    for key, basic_disk_info in response["Physical Device Information"].items():
        if "Detailed Information" in key:
            continue
        create_metrics_of_physical_drive(
            basic_disk_info[0],
            response["Physical Device Information"],
            controller_index,
        )


def handle_megaraid_controller(response):
    (controller_index, baselabel) = get_basic_controller_info(response)

    # BBU Status Optimal value is 0 for cachevault and 32 for BBU
    add_metric(
        "battery_backup_healthy",
        baselabel,
        int(response["Status"]["BBU Status"] in [0, 32]),
    )
    add_metric(
        "degraded",
        baselabel,
        int(response["Status"]["Controller Status"] == "Degraded"),
    )
    add_metric("failed", baselabel, int(response["Status"]["Controller Status"] == "Failed"))
    add_metric("healthy", baselabel, int(response["Status"]["Controller Status"] == "Optimal"))
    add_metric("ports", baselabel, response["HwCfg"]["Backend Port Count"])
    add_metric(
        "scheduled_patrol_read",
        baselabel,
        int("hrs" in response["Scheduled Tasks"]["Patrol Read Reoccurrence"]),
    )
    for cvidx, cvinfo in enumerate(response.get("Cachevault_Info", [])):
        add_metric(
            "cv_temperature",
            baselabel + ',cvidx="' + str(cvidx) + '"',
            int(cvinfo["Temp"].replace("C", "")),
        )

    time_difference_seconds = -1
    system_time = datetime.strptime(response["Basics"].get("Current System Date/time"), "%m/%d/%Y, %H:%M:%S")
    controller_time = datetime.strptime(response["Basics"].get("Current Controller Date/Time"), "%m/%d/%Y, %H:%M:%S")
    if system_time and controller_time:
        time_difference_seconds = abs(system_time - controller_time).seconds
        add_metric("time_difference", baselabel, time_difference_seconds)

    # Make sure it doesn't crash if it's a JBOD setup
    if "Drive Groups" in response.keys():
        add_metric("drive_groups", baselabel, response["Drive Groups"])
        add_metric(
            "drives",
            f'{baselabel}, "state":"Total", "type":"virtual"',
            response["Virtual Drives"],
        )
        add_metric(
            "memory_errors",
            f'"type": "correctable"',
            float(response["Status"]["Memory Correctable Errors"]),
        )
        add_metric(
            "memory_errors",
            f'"type": "uncorrectable"',
            float(response["Status"]["Memory Uncorrectable Errors"]),
        )
        number_regex = re.compile(r"([0-9]+)\s*MB")
        add_metric(
            "memory_size_bytes",
            f'"type":"Total memory"',
            float(number_regex.findall(response["HwCfg"]["On Board Memory Size"])[0]),
        )
        add_metric(
            "memory_size_bytes",
            f'"type":"Write cache"',
            float(response["HwCfg"]["Current Size of FW Cache (MB)"]) * 1024**2,
        )

        virtual_offline = 0
        virtual_degraded = 0
        for virtual_drive in response["VD LIST"]:
            if virtual_drive["State"] == "OfLn":
                virtual_offline += 1
            if virtual_drive["State"] == "Dgrd":
                virtual_degraded += 1

            vd_position = virtual_drive.get("DG/VD")
            drive_group, volume_group = -1, -1
            if vd_position:
                drive_group = vd_position.split("/")[0]
                volume_group = vd_position.split("/")[1]
            vd_baselabel = '"controller":"{0}","DG":"{1}","VG":"{2}"'.format(controller_index, drive_group, volume_group)
            vd_info_label = (vd_baselabel + ',"name":"{0}","cache":"{1}","type":"{2}","state":"{3}"'.format(
                str(virtual_drive.get("Name")).strip(),
                str(virtual_drive.get("Cache")).strip(),
                str(virtual_drive.get("TYPE")).strip(),
                str(virtual_drive.get("State")).strip(),
            ))
            add_metric("vd_info", vd_info_label, 1)

        add_metric(
            "drives",
            f'{baselabel}, "state":"Offline", "type":"virtual"',
            virtual_offline,
        )
        add_metric(
            "drives",
            f'{baselabel}, "state":"Degraded", "type":"virtual"',
            virtual_degraded,
        )

    add_metric(
        "drives",
        f'{baselabel}, "state":"DisksTotal", "type":"physical"',
        response["Physical Drives"],
    )

    if response["Physical Drives"] > 0:
        data = get_storcli_json("/cALL/eALL/sALL show all J")
        drive_info = data["Controllers"][controller_index]["Response Data"]

    for physical_drive in response["PD LIST"]:
        create_metrics_of_physical_drive(physical_drive, drive_info, controller_index)


def get_basic_controller_info(response):
    controller_index = response["Basics"]["Controller"]
    baselabel = '"adapter":"{0}"'.format(controller_index)
    return (controller_index, baselabel)


def create_metrics_of_physical_drive(physical_drive, detailed_info_array, controller_index):
    enclosure = physical_drive.get("EID:Slt").split(":")[0]
    slot = physical_drive.get("EID:Slt").split(":")[1]

    pd_baselabel = '"adapter":"{0}","enclosure":"{1}","slot":"{2}"'.format(controller_index, enclosure, slot)
    pd_info_label = (pd_baselabel + ',"disk_id":"{0}","interface":"{1}","media":"{2}","model":"{3}","DG":"{4}","state":"{5}"'.format(
        str(physical_drive.get("DID")).strip(),
        str(physical_drive.get("Intf")).strip(),
        str(physical_drive.get("Med")).strip(),
        str(physical_drive.get("Model")).strip(),
        str(physical_drive.get("DG")).strip(),
        str(physical_drive.get("State")).strip(),
    ))
    add_metric("pd_info", f'{pd_baselabel}, "type":"span"', enclosure)
    add_metric("pd_info", f'{pd_baselabel}, "type":"arm"', slot)
    add_metric(
        "pd_info",
        f'{pd_baselabel}, "type":"device_id"',
        str(physical_drive.get("DID")).strip(),
    )
    add_metric(
        "pd_info",
        f'{pd_baselabel}, "type":"disk_group"',
        str(physical_drive.get("DG")).strip(),
    )
    add_metric(
        "pd_info",
        f'{pd_baselabel}, "type":"state"',
        255 if str(physical_drive.get("State")).strip() == "Onln" else 0,
    )

    drive_identifier = ("Drive /c" + str(controller_index) + "/e" + str(enclosure) + "/s" + str(slot))
    if enclosure == " ":
        drive_identifier = "Drive /c" + str(controller_index) + "/s" + str(slot)
    try:
        info = detailed_info_array[drive_identifier + " - Detailed Information"]
        state = info[drive_identifier + " State"]
        attributes = info[drive_identifier + " Device attributes"]
        settings = info[drive_identifier + " Policies/Settings"]

        # state
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"smart_alert"',
            int(state["S.M.A.R.T alert flagged by drive"] == "Yes"),
        )
        add_metric(
            "pd_errors",
            f'{pd_baselabel}, "type":"predictive"',
            state["Predictive Failure Count"],
        )
        add_metric("pd_errors", f'{pd_baselabel}, "type":"media"', state["Media Error Count"])
        add_metric("pd_errors", f'{pd_baselabel}, "type":"other"', state["Other Error Count"])
        celsius_regex = re.compile(r"([0-9\.]+)\s*C")
        barbarians_regex = re.compile(r"([0-9\.]+)\s*F")
        add_metric(
            "pd_temperature",
            f'{pd_baselabel}, "type":"celsius"',
            float(celsius_regex.findall(state["Drive Temperature"])[0].strip()),
        )
        add_metric(
            "pd_temperature",
            f'{pd_baselabel}, "type":"barbarians"',
            float(barbarians_regex.findall(state["Drive Temperature"])[0].strip()),
        )

        # attributes
        hex_number_regex = re.compile(r"\[([0-9a-fx]+)")
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"size_coerced"',
            int(hex_number_regex.findall(attributes["Coerced size"])[0], 16) * 512,
        )
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"size_non_coerced"',
            int(hex_number_regex.findall(attributes["Non Coerced size"])[0], 16) * 512,
        )
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"size_raw"',
            int(hex_number_regex.findall(attributes["Raw size"])[0], 16) * 512,
        )
        add_metric("pd_info", f'{pd_baselabel}, "type":"wwn"', int(attributes["WWN"], 16))
        add_metric(
            "pd_speed_bits",
            f'{pd_baselabel}, "type":"drive"',
            float(attributes["Device Speed"].split("Gb")[0]) * pow(1024, 3),
        )
        add_metric(
            "pd_speed_bits",
            f'{pd_baselabel}, "type":"link"',
            float(attributes["Link Speed"].split("Gb")[0]) * pow(1024, 3),
        )

        # setting
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"ekm_attention_needed"',
            int(settings["Needs EKM Attention"] == "Yes"),
        )
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"sequence_number"',
            int(settings["Sequence Number"]),
        )
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"port"',
            int(settings["Connected Port Number"].split("(")[0].strip()),
        )
        add_metric(
            "pd_info",
            f'{pd_baselabel}, "type":"path"',
            int(settings["Connected Port Number"].split("path")[1].split(")")[0].strip()),
        )

        # add_metric(
        #     "pd_predictive_errors", pd_baselabel, state["Predictive Failure Count"]
        # )
        # add_metric(
        #     "pd_smart_alerted",
        #     pd_baselabel,
        #     int(state["S.M.A.R.T alert flagged by drive"] == "Yes"),
        # )
        # add_metric(
        #     "pd_link_speed_gbps", pd_baselabel, attributes["Link Speed"].split(".")[0]
        # )
        # add_metric(
        #     "pd_device_speed_gbps",
        #     pd_baselabel,
        #     attributes["Device Speed"].split(".")[0],
        # )
        # add_metric(
        #     "pd_commissioned_spare",
        #     pd_baselabel,
        #     int(settings["Commissioned Spare"] == "Yes"),
        # )
        # add_metric(
        #     "pd_emergency_spare",
        #     pd_baselabel,
        #     int(settings["Emergency Spare"] == "Yes"),
        # )

        pd_info_label += ',"firmware":"{0}"'.format(attributes["Firmware Revision"].strip())
        if "SN" in attributes:
            pd_info_label += ',"serial":"{0}"'.format(attributes["SN"].strip())
    except KeyError:
        pass
    # add_metric("pd_info", pd_info_label, 1)


def add_metric(name, labels, value):
    global metric_list
    try:
        name = f"{metric_prefix}{name}"
        labels = eval("{" + labels + "}")
        if name not in metric_list:
            metric_list[name] = prometheus_client.Gauge(name, documentation="", labelnames=labels.keys(), registry=REGISTRY)

        metric_list[name].labels(**labels).set(value)
    except ValueError as e:
        logging.warn(f"{name}{labels}: {e}")
        pass


def get_storcli_json(storcli_args):
    """Get storcli output in JSON format."""
    # Check if storcli is installed and executable
    if not (os.path.isfile(storcli_path) and os.access(storcli_path, os.X_OK)):
        SystemExit(1)
    storcli_cmd = shlex.split(storcli_path + " " + storcli_args)
    proc = subprocess.Popen(storcli_cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output_json = proc.communicate()[0]

    data = json.loads(output_json.decode("utf-8"))

    if data["Controllers"][0]["Command Status"]["Status"] != "Success":
        SystemExit(1)
    return data


if __name__ == "__main__":
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

    # print_all_metrics(metric_list)
