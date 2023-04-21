#!/bin/bash

set -e
set -x

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cp $SCRIPT_DIR/perccli64 /usr/bin
cp -r $SCRIPT_DIR/../../disk-exporter /opt/
cp -r $SCRIPT_DIR/disk_exporter.service /lib/systemd/system/
yum install -y smartmontools ipmitool
systemctl enable disk_exporter
systemctl restart disk_exporter
