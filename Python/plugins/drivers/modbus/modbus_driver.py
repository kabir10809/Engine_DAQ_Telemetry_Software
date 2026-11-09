"""Modbus RTU and TCP, read-only. Each channel maps to a register range.
Addresses follow the MATLAB build: 1 is the first register."""
import inspect

from edaq.hw.codecs import decode_registers, word_count
from edaq.hw.driver import Driver, empty_status, make_block
from edaq.util import opt, to_list

_MAX_CONSECUTIVE_ERRORS = 3


class ModbusDriver(Driver):
    def __init__(self):
        super().__init__()
        self._client = None
        self._packets_ok = 0
        self._packet_errors = 0
        self._next_poll_t = 0.0
        self._consecutive_errors = 0

    @staticmethod
    def manifest():
        field = lambda name, label, kind, default, help_text="": {"name": name, "label": label, "type": kind, "default": default, "help": help_text}
        return {"id": "modbus", "name": "Modbus RTU / TCP", "version": "1.0.0", "interfaces": ["Modbus RTU", "Modbus TCP"], "platforms": ["any"],
                "requires": ["pymodbus", "serial"], "configSchema": [
                    field("transport", "Transport", "choice:tcp,rtu", "tcp"), field("host", "Host", "string", "127.0.0.1"), field("tcpPort", "TCP port", "number", 502),
                    field("port", "Serial port", "string", " "), field("baudRate", "Baud rate", "number", 9600), field("parity", "Parity", "choice:even,none,odd", "even"),
                    field("timeoutS", "Timeout (s)", "number", 0.2, "Keep short; blocks the acquisition loop."), field("pollHz", "Poll rate (Hz)", "number", 5),
                    field("wordOrder", "Word order", "choice:big,little", "big"),
                    {"name": "channels", "label": "Registers", "type": "table:hw,table,address,type,serverId", "required": True,
                     "help": "table: holdingregs, inputregs, coils or inputs. type: uint16, int16, uint32, int32, single, double."}]}

    def connect(self):
        from pymodbus.client import ModbusSerialClient, ModbusTcpClient
        s = self.settings
        self._rows = to_list(s["channels"])
        self._names = [str(r["hw"]) for r in self._rows]
        if s["transport"] == "tcp":
            self._client = ModbusTcpClient(s["host"], port=int(s["tcpPort"]), timeout=float(s["timeoutS"]))
        else:
            parity = {"even": "E", "none": "N", "odd": "O"}[s["parity"]]
            self._client = ModbusSerialClient(str(s["port"]).strip(), baudrate=int(s["baudRate"]), parity=parity, timeout=float(s["timeoutS"]))
        if not self._client.connect():
            self._client = None
            raise ConnectionError("Could not open the Modbus connection.")
        self._consecutive_errors = 0

    def identify(self):
        return {"vendor": "", "model": "", "serial": "", "firmware": "", "evidence": "Standard Modbus register reads give no device identity."}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": float(self.settings["pollHz"]), "description": "Configured register"} for name in self._names]

    def read(self, t_now):
        if t_now < self._next_poll_t:
            return []
        self._next_poll_t = t_now + 1 / float(self.settings["pollHz"])
        times, values = [], []
        for row in self._rows:
            try:
                value = self._read_row(row)
                times.append([t_now])
                values.append([value])
                self._packets_ok += 1
                self._consecutive_errors = 0
            except Exception as error:
                times.append([])
                values.append([])
                self._packet_errors += 1
                self._consecutive_errors += 1
                self.last_error = str(error)
                if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    raise ConnectionError(f"Modbus not responding: {error}") from error
        return make_block(self._names, times, values)

    def status(self):
        st = empty_status()
        st.update(connected=self._client is not None, packetsOk=self._packets_ok, packetErrors=self._packet_errors)
        return st

    def disconnect(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def _request(self, method, address, count, server):
        # The keyword for the server id has changed between pymodbus releases.
        keyword = next(name for name in ("device_id", "slave", "unit") if name in inspect.signature(method).parameters)
        response = method(address, count=count, **{keyword: server})
        if response.isError():
            raise IOError(str(response))
        return response

    def _read_row(self, row):
        address, server = int(row["address"]) - 1, int(opt(row, "serverId", 1))
        table = row["table"]
        if table in ("coils", "inputs"):
            method = self._client.read_coils if table == "coils" else self._client.read_discrete_inputs
            return float(self._request(method, address, 1, server).bits[0])
        method = self._client.read_holding_registers if table == "holdingregs" else self._client.read_input_registers
        registers = self._request(method, address, word_count(row["type"]), server).registers
        return decode_registers(registers, row["type"], self.settings["wordOrder"])
