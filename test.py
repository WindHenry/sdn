from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.topology import event, switches
from ryu.topology.api import get_switch, get_link
from ryu.lib.ovs import bridge
from ryu.lib import dpid as dpid_lib


class SDNApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNApp, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.topology_api_app = self
        self.network_graph = {}
        self.mac_to_dpid = {}
        self.sw_port = {}
    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def install_path_flows(self, datapath, path):
        for i in range(1, len(path)):
            in_port = self.network_graph[path[i-1]][path[i]]['port']
            out_port = self.network_graph[path[i]][path[i-1]]['port']

            match = datapath.ofproto_parser.OFPMatch(
                in_port=in_port,
                eth_dst=path[i]
            )
            actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]

            self.add_flow(datapath, 1, match, actions)

    def get_paths(self, src, dst):
        src_dpid = self.mac_to_dpid.get(src)
        dst_dpid = self.mac_to_dpid.get(dst)

        #if src_dpid is None or dst_dpid is None:
            # Handle the case when either source or destination MAC is not in the network_graph
            #return []
      
        if src_dpid == dst_dpid:
            return [[src_dpid]]

        paths = []
        stack = [(src_dpid, [src_dpid])]

        while stack:
            (node, path) = stack.pop()

            for next_node in set(self.network_graph[node]) - set(path):
                if next_node == dst_dpid:
                    paths.append(path + [next_node])
                else:
                    stack.append((next_node, path + [next_node]))

        return paths

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        switch = ev.switch
        dpid = switch.dp.id
        self.logger.info("Switch %s entered the network", dpid)
        self.network_graph[dpid] = {}

        # Update mac_to_dpid mapping
        for port in switch.ports:
            print(port.hw_addr)
            port_name = port.name.decode('utf-8')
            self.sw_port[dpid].setdefault(port.port_no, port.name)

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):
        link = ev.link
        src_dpid = self.mac_to_dpid.get(link.src.addr)
        dst_dpid = self.mac_to_dpid.get(link.dst.addr)

        if src_dpid is not None and dst_dpid is not None:
            src_port = link.src.port_no
            dst_port = link.dst.port_no
            self.logger.info("Link added: (%s, %s) -> (%s, %s)",
                             src_dpid, src_port, dst_dpid, dst_port)

            self.network_graph[src_dpid][dst_dpid] = {'port': src_port}
            self.network_graph[dst_dpid][src_dpid] = {'port': dst_port}
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Install the default flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                           ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth_pkt = pkt.get_protocols(ethernet.ethernet)[0]

        src_mac = eth_pkt.src
        dst_mac = eth_pkt.dst

        self.mac_to_port.setdefault(datapath.id, {})
        self.mac_to_port[datapath.id][src_mac] = in_port

        if dst_mac in self.mac_to_port[datapath.id]:
            out_port = self.mac_to_port[datapath.id][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Add flow entry for the packet's path
        paths = self.get_paths(src_mac, dst_mac)
        if paths:
            self.install_path_flows(datapath, paths[0])

        # Send the packet to the specified output port
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )
        datapath.send_msg(out)
    def test(self):
        ovsdb_server = 'tcp:127.0.0.1:6640'
        
        ovs_bridge = bridge.OVSBridge(self.CONF, 1, ovsdb_server)
        t = ovs_bridge.get_port_name_list()


if __name__ == '__main__':

    from ryu.cmd import manager
    manager.main()

