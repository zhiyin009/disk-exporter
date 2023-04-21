## 启动
```bash
# 绑定 8101 端口启动
SMARTCTL_EXPORTER_PORT=8101 python3 disk_exporter.py

# 关闭部分组件
DISABLE_MEGACLI=1 DISABLE_IPMITOOL=1 DISABLE_SMARTPROM=1 python3 disk_exporter.py
```

## 说明
- 裸磁盘信息
    -  [smartprom.py](smartprom.py)：
        - 借鉴项目：https://github.com/matusnovak/prometheus-smartctl
- raid 控制器信息（依赖于机器上安装 megacli/perccli，两种工具不能同时存在）
	- [megacli.py](megacli.py 目前默认禁止调用，可能导致 H750P raid 卡服务器卡死几分钟)：
        - 采集硬 raid 信息(Mega)
        - 借鉴项目：https://github.com/bojleros/megacli2prom
    - [perccli.py](perccli.py)：
        - 采集硬 raid 信息(Dell)
- 主板日志
    - [ipmitool_sel.py](ipmitool_sel.py)：
        - 采集 BMC event log
