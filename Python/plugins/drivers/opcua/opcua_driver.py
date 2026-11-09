"""OPC UA client, read-only. Node quality codes are used as packet status."""
from edaq.hw.driver import Driver, empty_status, make_block
from edaq.util import to_list


class OpcUaDriver(Driver):
    def __init__(self):
        super().__init__()
        self._client = None
        self._packets_ok = 0
        self._packet_errors = 0
        self._next_poll_t = 0.0

    @staticmethod
    def manifest():
        return {"id": "opcua", "name": "OPC UA", "version": "1.0.0", "interfaces": ["OPC UA"], "platforms": ["any"], "requires": ["asyncua"], "configSchema": [
            {"name": "host", "label": "Server host", "type": "string", "default": "localhost", "help": ""},
            {"name": "tcpPort", "label": "Server port", "type": "number", "default": 4840, "help": ""},
            {"name": "pollHz", "label": "Poll rate (Hz)", "type": "number", "default": 2, "help": ""},
            {"name": "channels", "label": "Nodes", "type": "table:hw,namespace,identifier", "required": True, "help": "Node namespace index and identifier."}]}

    def connect(self):
        from asyncua.sync import Client
        s = self.settings
        client = Client(f'opc.tcp://{s["host"]}:{int(s["tcpPort"])}')
        client.connect()
        self._client = client
        rows = to_list(s["channels"])
        self._names = [str(r["hw"]) for r in rows]
        self._nodes = [client.get_node(f'ns={int(r["namespace"])};s={r["identifier"]}') for r in rows]

    def identify(self):
        return {"vendor": "", "model": str(self.settings["host"]), "serial": "", "firmware": "", "evidence": "OPC UA endpoint address."}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": float(self.settings["pollHz"]), "description": "OPC UA node"} for name in self._names]

    def read(self, t_now):
        if t_now < self._next_poll_t:
            return []
        self._next_poll_t = t_now + 1 / float(self.settings["pollHz"])
        times, values = [], []
        for node in self._nodes:
            reading = node.read_data_value()
            if reading.StatusCode.is_good():
                times.append([t_now])
                values.append([float(reading.Value.Value)])
                self._packets_ok += 1
            else:
                times.append([])
                values.append([])
                self._packet_errors += 1
        return make_block(self._names, times, values)

    def status(self):
        st = empty_status()
        st.update(connected=self._client is not None, packetsOk=self._packets_ok, packetErrors=self._packet_errors)
        return st

    def disconnect(self):
        if self._client is not None:
            self._client.disconnect()
            self._client = None
