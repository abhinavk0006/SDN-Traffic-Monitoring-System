# SDN-Traffic-Monitoring-System
# SDN Traffic Monitoring & Statistics Collector
## UE24CS252B — Computer Networks | Orange Level Project | Topic 3

---

## Problem Statement
This project implements an SDN-based Traffic Monitoring system using Mininet and the Ryu OpenFlow controller. The system manages L2 forwarding through a Learning Switch logic and independently polls network statistics. It tracks packet counts and byte counts for every flow and port across a multi-switch topology, saving the data into CSV reports for network performance analysis.

## Features
* **L2 Learning Switch**: Handles `packet_in` events and installs flow rules with match-action logic for MAC addresses.
* **Asynchronous Monitoring**: Implements a background thread using Ryu's `hub.spawn` to poll switches every 10 seconds.
* **Traffic Statistics**: Collects `OFPFlowStatsRequest` and `OFPPortStatsRequest` data using OpenFlow 1.3.
* **Data Persistence**: Automatically generates time-stamped CSV reports in the `reports/` directory.
* **Multi-Switch Support**: Handles complex topologies with multiple connected datapaths and unique DPIDs.

## Topology
The network consists of 5 hosts and 2 OpenFlow switches connected in a tree structure:

```text
h1 ──┐
h2 ──┤── s1 ──── s2 ──┬── h4
h3 ──┘                └── h5
```

* **Switches**: s1, s2 (Configured for OpenFlow 1.3)
* **Hosts**: h1, h2, h3, h4, h5 (Static IPs 10.0.0.1 - 10.0.0.5)
* **Links**: Bandwidth limited to 10Mbps for hosts and 100Mbps for switch-to-switch.

## Execution Steps

### 1. Start the Ryu Controller
```bash
ryu-manager traffic_monitor.py --observe-links --ofp-tcp-listen-port 6633
```

### 2. Start the Mininet Topology
```bash
sudo python3 topology.py
```

### 3. Run Traffic Tests
Inside the Mininet CLI:
```bash
mininet> pingall
mininet> h1 iperf3 -s &
mininet> h4 iperf3 -c 10.0.0.1 -t 15
```

## Performance Analysis & Validation
The following results were verified during the demonstration:
* **Functional Correctness**: Connectivity was established with 0% packet loss during `pingall` tests.
* **Throughput**: High-load testing via `iperf3` between hosts recorded a bitrate of 9.71 Mbits/sec, matching the link bandwidth constraints.
* **Flow Management**: Priority-1 rules were successfully installed upon traffic detection and automatically removed after the 30-second `idle_timeout`.
* **Data Accuracy**: Traffic volumes (approx. 17.4 MegaBytes for burst tests) were correctly captured by the collector and logged in the CSV reports.

## Project Structure
* `traffic_monitor.py`: Ryu controller logic.
* `topology.py`: Mininet network script.
* `reports/`: Directory containing generated CSV files.
* `screenshots/`: Terminal logs and test results.

## References
1. Ryu SDN Framework Documentation
2. OpenFlow Switch Specification v1.3.0
3. Mininet Network Emulator Project

---
**Author**: Abhi
**Course**: UE24CS252B Computer Networks
**Project Level**: Orange
