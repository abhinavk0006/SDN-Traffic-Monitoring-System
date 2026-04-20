#!/usr/bin/env python3


from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, tcp, udp
from ryu.lib import hub

import os
import csv
import datetime
import collections


# ─── Configuration ────────────────────────────────────────────────────────────
STATS_INTERVAL   = 10      # seconds between each stats poll
REPORT_DIR       = "./reports"
IDLE_TIMEOUT     = 30      # flow rule idle timeout (seconds)
HARD_TIMEOUT     = 0       # 0 = no hard timeout
FLOW_PRIORITY    = 1       # priority for installed flow rules
TABLE_ID         = 0
# ──────────────────────────────────────────────────────────────────────────────


class TrafficMonitor(app_manager.RyuApp):
    """
    Ryu controller: Learning Switch + Traffic Statistics Collector.
    Handles packet_in events, installs flow rules, and periodically
    queries flow/port statistics from all connected switches.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TrafficMonitor, self).__init__(*args, **kwargs)

        # MAC address table: {dpid: {mac: port}}
        self.mac_to_port = {}

        # Datapaths registry: {dpid: datapath}
        self.datapaths = {}

        # Statistics history: {dpid: {(in_port, eth_dst): [stat records]}}
        self.flow_stats_history = collections.defaultdict(dict)

        # Port statistics history: {dpid: {port_no: [stat records]}}
        self.port_stats_history = collections.defaultdict(dict)

        # Ensure reports directory exists
        os.makedirs(REPORT_DIR, exist_ok=True)

        # Start background statistics polling thread
        self.monitor_thread = hub.spawn(self._monitor_loop)

        self.logger.info("=" * 60)
        self.logger.info("  Traffic Monitor Controller Started")
        self.logger.info("  Stats interval : %d seconds", STATS_INTERVAL)
        self.logger.info("  Reports dir    : %s", os.path.abspath(REPORT_DIR))
        self.logger.info("=" * 60)

    # ─────────────────────────────────────────────────────────────────────────
    # Switch Handshake
    # ─────────────────────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Called when a switch connects. Installs table-miss flow entry."""
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        self.logger.info("[CONNECT] Switch dpid=%016x connected", datapath.id)

        # Install table-miss entry: send all unmatched packets to controller
        match  = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, priority=0, match=match, actions=actions,
                       idle_timeout=0, hard_timeout=0)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        """Track connected/disconnected datapaths."""
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.logger.info("[REGISTER] dpid=%016x registered", datapath.id)
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]
                self.logger.info("[UNREGISTER] dpid=%016x removed", datapath.id)

    # ─────────────────────────────────────────────────────────────────────────
    # Packet-In Handler (Learning Switch Logic)
    # ─────────────────────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """
        Handles all packets sent to the controller.
        Implements L2 learning switch:
          1. Learn source MAC → in_port mapping
          2. Look up destination MAC
          3. If known: install flow rule + forward
          4. If unknown: flood
        """
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match['in_port']

        # Parse packet
        pkt      = packet.Packet(msg.data)
        eth_pkt  = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP
        if eth_pkt.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst_mac  = eth_pkt.dst
        src_mac  = eth_pkt.src
        dpid     = datapath.id

        # Initialise MAC table for this switch
        self.mac_to_port.setdefault(dpid, {})

        # ── Step 1: Learn source MAC ──────────────────────────────────────
        if src_mac not in self.mac_to_port[dpid]:
            self.logger.info(
                "[LEARN] dpid=%016x  src=%s  in_port=%s",
                dpid, src_mac, in_port
            )
        self.mac_to_port[dpid][src_mac] = in_port

        # ── Step 2: Determine output port ────────────────────────────────
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD   # destination unknown → flood

        actions = [parser.OFPActionOutput(out_port)]

        # ── Step 3: Install flow rule if destination is known ─────────────
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac,
                                    eth_src=src_mac)
            # Only install rule if buffer_id is valid
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self._add_flow(datapath, FLOW_PRIORITY, match, actions,
                               IDLE_TIMEOUT, HARD_TIMEOUT,
                               buffer_id=msg.buffer_id)
                return
            else:
                self._add_flow(datapath, FLOW_PRIORITY, match, actions,
                               IDLE_TIMEOUT, HARD_TIMEOUT)

        # ── Step 4: Send the current packet ──────────────────────────────
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )
        datapath.send_msg(out)

    # ─────────────────────────────────────────────────────────────────────────
    # Helper: Add Flow Rule
    # ─────────────────────────────────────────────────────────────────────────

    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=0, buffer_id=None):
        """Install an OpenFlow flow rule on a switch."""
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        kwargs = dict(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout
        )
        if buffer_id and buffer_id != ofproto.OFP_NO_BUFFER:
            kwargs['buffer_id'] = buffer_id

        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

        self.logger.debug(
            "[FLOW INSTALLED] dpid=%016x priority=%d idle_timeout=%d",
            datapath.id, priority, idle_timeout
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Periodic Monitoring Loop
    # ─────────────────────────────────────────────────────────────────────────

    def _monitor_loop(self):
        """
        Background thread that periodically requests statistics
        from all connected switches. Runs every STATS_INTERVAL seconds.
        """
        self.logger.info("[MONITOR] Polling thread started (interval=%ds)",
                         STATS_INTERVAL)
        while True:
            for dp in list(self.datapaths.values()):
                self._request_flow_stats(dp)
                self._request_port_stats(dp)
            hub.sleep(STATS_INTERVAL)

    def _request_flow_stats(self, datapath):
        """Send OFPFlowStatsRequest to a switch."""
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    def _request_port_stats(self, datapath):
        """Send OFPPortStatsRequest to a switch."""
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(
            datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    # ─────────────────────────────────────────────────────────────────────────
    # Flow Statistics Reply Handler
    # ─────────────────────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """
        Called when a switch replies with flow statistics.
        Displays a formatted table and saves to CSV.
        """
        body     = ev.msg.body
        datapath = ev.msg.datapath
        dpid     = datapath.id
        now      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.logger.info("")
        self.logger.info("┌─────────────────────────────────────────────────────────────┐")
        self.logger.info("│  FLOW STATISTICS  │  Switch dpid=%-26x  │", dpid)
        self.logger.info("│  Timestamp: %-48s│", now)
        self.logger.info("├──────────┬──────────┬──────────────┬──────────────┬─────────┤")
        self.logger.info("│ Priority │ In-Port  │ Eth-Dst      │ Packets      │ Bytes   │")
        self.logger.info("├──────────┼──────────┼──────────────┼──────────────┼─────────┤")

        # CSV report rows for this poll cycle
        csv_rows = []

        for stat in sorted(body, key=lambda s: s.priority):
            priority    = stat.priority
            packet_count = stat.packet_count
            byte_count  = stat.byte_count
            duration    = stat.duration_sec

            # Extract match fields safely
            in_port  = stat.match.get('in_port',  'ANY')
            eth_dst  = stat.match.get('eth_dst',  'ANY')
            eth_src  = stat.match.get('eth_src',  'ANY')

            self.logger.info(
                "│ %-8s │ %-8s │ %-12s │ %-12s │ %-7s │",
                priority, in_port, eth_dst, packet_count, byte_count
            )

            csv_rows.append({
                'timestamp'    : now,
                'dpid'         : format(dpid, '016x'),
                'priority'     : priority,
                'in_port'      : in_port,
                'eth_src'      : eth_src,
                'eth_dst'      : eth_dst,
                'packet_count' : packet_count,
                'byte_count'   : byte_count,
                'duration_sec' : duration
            })

        self.logger.info("└──────────┴──────────┴──────────────┴──────────────┴─────────┘")
        self.logger.info("  Total flow entries: %d", len(body))

        # Save to CSV
        self._save_flow_stats_csv(dpid, csv_rows)

    # ─────────────────────────────────────────────────────────────────────────
    # Port Statistics Reply Handler
    # ─────────────────────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        """
        Called when a switch replies with port statistics.
        Displays per-port packet/byte counts and saves to CSV.
        """
        body     = ev.msg.body
        datapath = ev.msg.datapath
        dpid     = datapath.id
        now      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.logger.info("")
        self.logger.info("┌──────────────────────────────────────────────────────────────────────────────┐")
        self.logger.info("│  PORT STATISTICS  │  Switch dpid=%-26x                      │", dpid)
        self.logger.info("├────────┬───────────────┬───────────────┬───────────────┬────────────────────┤")
        self.logger.info("│  Port  │  RX Packets   │  TX Packets   │  RX Bytes     │  TX Bytes          │")
        self.logger.info("├────────┼───────────────┼───────────────┼───────────────┼────────────────────┤")

        csv_rows = []

        for stat in sorted(body, key=lambda s: s.port_no):
            self.logger.info(
                "│ %-6s │ %-13s │ %-13s │ %-13s │ %-18s │",
                stat.port_no,
                stat.rx_packets,
                stat.tx_packets,
                stat.rx_bytes,
                stat.tx_bytes
            )
            csv_rows.append({
                'timestamp'  : now,
                'dpid'       : format(dpid, '016x'),
                'port_no'    : stat.port_no,
                'rx_packets' : stat.rx_packets,
                'tx_packets' : stat.tx_packets,
                'rx_bytes'   : stat.rx_bytes,
                'tx_bytes'   : stat.tx_bytes,
                'rx_errors'  : stat.rx_errors,
                'tx_errors'  : stat.tx_errors,
                'rx_dropped' : stat.rx_dropped,
                'tx_dropped' : stat.tx_dropped
            })

        self.logger.info("└────────┴───────────────┴───────────────┴───────────────┴────────────────────┘")

        # Save to CSV
        self._save_port_stats_csv(dpid, csv_rows)

    # ─────────────────────────────────────────────────────────────────────────
    # CSV Report Generation
    # ─────────────────────────────────────────────────────────────────────────

    def _save_flow_stats_csv(self, dpid, rows):
        """Append flow stats rows to a per-switch CSV file."""
        if not rows:
            return
        filename = os.path.join(
            REPORT_DIR,
            "flow_stats_dpid_{}.csv".format(format(dpid, '016x'))
        )
        fieldnames = ['timestamp', 'dpid', 'priority', 'in_port',
                      'eth_src', 'eth_dst', 'packet_count',
                      'byte_count', 'duration_sec']
        self._append_csv(filename, fieldnames, rows)

    def _save_port_stats_csv(self, dpid, rows):
        """Append port stats rows to a per-switch CSV file."""
        if not rows:
            return
        filename = os.path.join(
            REPORT_DIR,
            "port_stats_dpid_{}.csv".format(format(dpid, '016x'))
        )
        fieldnames = ['timestamp', 'dpid', 'port_no', 'rx_packets',
                      'tx_packets', 'rx_bytes', 'tx_bytes',
                      'rx_errors', 'tx_errors', 'rx_dropped', 'tx_dropped']
        self._append_csv(filename, fieldnames, rows)

    def _append_csv(self, filename, fieldnames, rows):
        """Append rows to a CSV file, writing header only on first write."""
        write_header = not os.path.exists(filename)
        with open(filename, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)
        self.logger.debug("[CSV] Written %d rows to %s", len(rows), filename)

    def _generate_summary_report(self):
        """
        Generate a human-readable summary text report.
        Called automatically when the controller shuts down.
        """
        now      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(REPORT_DIR, "summary_report_{}.txt".format(now))

        with open(filename, 'w') as f:
            f.write("=" * 60 + "\n")
            f.write("  SDN TRAFFIC MONITORING — SUMMARY REPORT\n")
            f.write("  Generated: {}\n".format(now))
            f.write("=" * 60 + "\n\n")

            f.write("Connected Switches\n")
            f.write("-" * 30 + "\n")
            for dpid in self.datapaths:
                f.write("  dpid = {}\n".format(format(dpid, '016x')))

            f.write("\nMAC Address Table\n")
            f.write("-" * 30 + "\n")
            for dpid, mac_table in self.mac_to_port.items():
                f.write("  Switch dpid={}\n".format(format(dpid, '016x')))
                for mac, port in mac_table.items():
                    f.write("    {} -> port {}\n".format(mac, port))

            f.write("\nCSV Files Generated\n")
            f.write("-" * 30 + "\n")
            for fname in os.listdir(REPORT_DIR):
                if fname.endswith('.csv'):
                    f.write("  {}\n".format(fname))

        self.logger.info("[REPORT] Summary saved to %s", filename)
        return filename


