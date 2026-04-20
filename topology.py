#!/usr/bin/env python3
"""
SDN Traffic Monitoring Project — Custom Mininet Topology
UE24CS252B | Topic 3: Traffic Monitoring and Statistics Collector

Topology:
         h1
          \
    h3 -- s1 -- s2 -- h4
          /      \
         h2       h5

- 2 switches (s1, s2)
- 5 hosts (h1–h5)
- s1 and s2 are connected
- Hosts h1, h2, h3 connect to s1
- Hosts h4, h5 connect to s2
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI


class TrafficMonitorTopo(Topo):
    """Custom topology for traffic monitoring demonstration."""

    def build(self):
        info("*** Creating Traffic Monitor Topology\n")

        # Add switches
        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')

        # Add hosts with static IPs
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')
        h5 = self.addHost('h5', ip='10.0.0.5/24', mac='00:00:00:00:00:05')

        # Add links with bandwidth constraints (for iperf testing)
        self.addLink(h1, s1, bw=10)
        self.addLink(h2, s1, bw=10)
        self.addLink(h3, s1, bw=10)
        self.addLink(s1, s2, bw=100)
        self.addLink(h4, s2, bw=10)
        self.addLink(h5, s2, bw=10)


def run():
    """Start the network and connect to remote Ryu controller."""
    topo = TrafficMonitorTopo()

    net = Mininet(
        topo=topo,
        controller=RemoteController('c0', ip='127.0.0.1', port=6633),
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=False
    )

    net.start()
    info("*** Network started\n")
    info("*** Hosts: {}\n".format([h.name for h in net.hosts]))
    info("*** Switches: {}\n".format([s.name for s in net.switches]))

    # Test basic connectivity
    info("*** Running pingAll test\n")
    net.pingAll()

    info("*** Starting CLI — type 'exit' to quit\n")
    CLI(net)

    net.stop()
    info("*** Network stopped\n")


if __name__ == '__main__':
    setLogLevel('info')
    run()
