"""Read-only input for deciding whether to spend GPU time on offline playback."""
import time

TOPICS = {
    'state': ('/mavros/state', 'mavros_msgs/msg/State'),
    'landed': ('/mavros/extended_state', 'mavros_msgs/msg/ExtendedState'),
}
MAX_AGE = 2.8
NSEC = 1_000_000_000


class FlightState:
    def __init__(self, lock):
        self.lock = lock
        self.node = None
        self.endpoints = {}
        self.samples = {}
        self.checked = 0.0
        self.ground_since = None
        self.seen_fcu = False
        self.bench_since = None

    def subscribe(self, node, qos):
        try:
            from mavros_msgs.msg import State, ExtendedState
        except ImportError:
            return
        self.node = node
        for key, cls in (('state', State), ('landed', ExtendedState)):
            def collect(msg, kind=key):
                self.collect(kind, msg)
            node.create_subscription(cls, TOPICS[key][0], collect, qos)

    @staticmethod
    def expected_writer(writer, typ):
        ns, name = writer.node_namespace, writer.node_name
        return writer.topic_type == typ and (ns == '/mavros' or ns.startswith('/mavros/')
                 or (ns == '/' and name in ('mavros', 'mavros_node')))

    def refresh(self):
        if self.node is None: return
        now = time.monotonic()
        if now-self.checked < .35: return
        endpoints = {}
        fcu_present = any(
            ns == '/mavros' or ns.startswith('/mavros/') or name in ('mavros','mavros_node','mavros_router')
            for name,ns in self.node.get_node_names_and_namespaces()
        )
        for key, (topic, typ) in TOPICS.items():
            pubs = self.node.get_publishers_info_by_topic(topic)
            fcu_present = fcu_present or bool(pubs)
            if len(pubs)==1 and self.expected_writer(pubs[0],typ):
                endpoints[key]=bytes(pubs[0].endpoint_gid)
        with self.lock:
            if endpoints != self.endpoints:
                self.samples.clear()
                self.ground_since = None
                self.endpoints = endpoints
            self.seen_fcu = self.seen_fcu or fcu_present
            if self.seen_fcu:
                self.bench_since = None
            elif self.bench_since is None or now-self.checked >= 1.3:
                self.bench_since = now
            self.checked = now

    def collect(self, kind, msg):
        s=msg.header.stamp
        stamp=s.sec*NSEC+s.nanosec
        now=time.monotonic()
        stale=stamp<=0 or not -.5 <= (time.time_ns()-stamp)/NSEC <= MAX_AGE
        if kind=='state':
            value='armed' if msg.armed else 'ground'
            if not msg.connected: value='telemetry_unavailable'
        else:
            value={1:'ground',2:'in_flight',3:'in_flight',4:'in_flight'}.get(msg.landed_state,'telemetry_unavailable')
        with self.lock:
            # Humble callbacks provide the message only. Validate uniqueness through the graph.
            if kind not in self.endpoints:
                return
            if stale:
                self.samples.pop(kind,None)
                self.ground_since=None
                return
            prior=self.samples.get(kind)
            if prior and prior[1]>=stamp:
                if value!='ground':
                    self.samples.pop(kind,None)
                    self.ground_since=None
                return
            self.samples[kind]=(now,stamp,value)
            if value!='ground':self.ground_since=None

    def bench_reason(self):
        """Graph visibility only, never proof of physical isolation. Caller holds the lock."""
        now=time.monotonic()
        if self.seen_fcu:
            return 'bench_fcu_seen'
        if self.bench_since is None or now-self.checked>=1.3 or now-self.bench_since<3.0:
            return 'bench_waiting_graph'
        return None

    def reason(self):
        """Caller holds the lock; None means sustained, fresh, disarmed ground state."""
        now=time.monotonic()
        if now-self.checked>=1.3 or len(self.endpoints)!=2 or len(self.samples)!=2:
            self.ground_since=None
            return 'telemetry_unavailable'
        for received,stamp,_ in self.samples.values():
            if now-received>=MAX_AGE or not -.5<=(time.time_ns()-stamp)/NSEC<MAX_AGE:
                self.ground_since=None
                return 'telemetry_unavailable'
        state=self.samples['state'][2]
        landed=self.samples['landed'][2]
        if (landed,state)!=('ground','ground'):
            self.ground_since=None
            return 'in_flight' if landed=='in_flight' else ('armed' if state=='armed' else 'telemetry_unavailable')
        if self.ground_since is None:self.ground_since=now
        return 'confirming_ground' if now-self.ground_since<.65 else None
