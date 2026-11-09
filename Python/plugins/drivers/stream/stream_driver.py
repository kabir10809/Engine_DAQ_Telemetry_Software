"""Line-oriented text instruments over serial, TCP or UDP. Covers RS-232, RS-485
(adapter handles direction), USB virtual COM ports, Ethernet and vendor bridges
emitting JSON lines."""
import socket

import numpy as np

from edaq.hw.codecs import parse_frame
from edaq.hw.driver import Driver, empty_status, make_block, make_candidate
from edaq.util import to_list

_TERMINATORS = {"LF": b"\n", "CR": b"\r", "CRLF": b"\r\n"}


def _field(name, label, kind, default, help_text=""):
    return {"name": name, "label": label, "type": kind, "default": default, "help": help_text}


class StreamDriver(Driver):
    def __init__(self):
        super().__init__()
        self._link = None
        self._buffer = b""
        self._packets_ok = 0
        self._packet_errors = 0
        self._last_time = float("nan")
        self._next_poll_t = 0.0

    @staticmethod
    def manifest():
        schema = [_field("transport", "Transport", "choice:serial,tcp,udp", "serial"),
                  _field("port", "Serial port", "string", "", "e.g. /dev/ttyUSB0 or COM3"),
                  _field("baudRate", "Baud rate", "number", 9600), _field("parity", "Parity", "choice:none,even,odd", "none"),
                  _field("dataBits", "Data bits", "number", 8), _field("stopBits", "Stop bits", "number", 1),
                  _field("host", "Host", "string", "127.0.0.1", "TCP peer or UDP sender"), _field("tcpPort", "TCP/UDP port", "number", 5000),
                  _field("terminator", "Line terminator", "choice:LF,CR,CRLF", "LF"),
                  _field("format", "Frame format", "choice:delimited,keyvalue,json", "delimited"), _field("delimiter", "Delimiter", "string", ","),
                  _field("pollCommand", "Poll command", "string", " ", "Sent each poll. Blank for free-running devices."),
                  _field("pollHz", "Poll rate (Hz)", "number", 5, "Used only with a poll command."),
                  _field("vendor", "Vendor (declared)", "string", " ", "Stream devices cannot identify themselves."), _field("model", "Model (declared)", "string", " "),
                  {"name": "channels", "label": "Channels", "type": "table:hw,field", "required": True, "help": "hw: channel name. field: column number, key or JSON name."}]
        return {"id": "stream", "name": "Text stream (serial / TCP / UDP)", "version": "1.0.0",
                "interfaces": ["USB", "USB-to-Serial", "RS-232", "RS-485", "Ethernet", "TCP/IP", "UDP", "Vendor adapter"],
                "platforms": ["any"], "requires": [], "configSchema": schema}

    @staticmethod
    def discover():
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        return [make_candidate("stream", "RS-232", port.device, "", "", "", "port_only", "Serial port present. Device and protocol unknown.")
                for port in list_ports.comports()]

    def connect(self):
        s = self.settings
        rows = to_list(s["channels"])
        self._names = [str(r["hw"]) for r in rows]
        self._fields = [str(r["field"]) for r in rows]
        self._buffer = b""
        transport = s["transport"]
        if transport == "serial":
            import serial       # optional: only needed for this transport
            parity = {"none": serial.PARITY_NONE, "even": serial.PARITY_EVEN, "odd": serial.PARITY_ODD}[s["parity"]]
            self._link = serial.Serial(s["port"], int(s["baudRate"]), parity=parity, bytesize=int(s["dataBits"]), stopbits=s["stopBits"], timeout=0)
        elif transport == "tcp":
            self._link = socket.create_connection((s["host"], int(s["tcpPort"])), timeout=3)
            self._link.setblocking(False)
        elif transport == "udp":
            self._link = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._link.bind(("", int(s["tcpPort"])))
            self._link.setblocking(False)
        else:
            raise ValueError(f'Unknown transport "{transport}".')

    def identify(self):
        return {"vendor": str(self.settings["vendor"]).strip(), "model": str(self.settings["model"]).strip(), "serial": "", "firmware": "",
                "evidence": "Declared by the user. Text streams do not self-identify."}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": float("nan"), "description": "Configured stream field"} for name in self._names]

    def read(self, t_now):
        self._poll(t_now)
        lines = self._drain_lines()
        n, count = len(lines), len(self._names)
        values, good = np.full((n, count), np.nan), np.zeros(n, bool)
        for k, line in enumerate(lines):
            parsed, good[k] = parse_frame(line, self.settings["format"], self.settings["delimiter"], self._fields)
            values[k, :] = parsed
        self._packets_ok += int(good.sum())
        self._packet_errors += int(n - good.sum())
        times = self._frame_times(t_now, n)
        return make_block(self._names, [times[good]] * count, [values[good, c] for c in range(count)])

    def status(self):
        st = empty_status()
        st.update(connected=self._link is not None, packetsOk=self._packets_ok, packetErrors=self._packet_errors)
        return st

    def disconnect(self):
        if self._link is not None:
            self._link.close()
            self._link = None

    def _poll(self, t_now):
        command = str(self.settings["pollCommand"]).strip()
        if not command or t_now < self._next_poll_t:
            return
        self._next_poll_t = t_now + 1 / float(self.settings["pollHz"])
        payload = command.encode() + _TERMINATORS[self.settings["terminator"]]
        if self.settings["transport"] == "udp":
            self._link.sendto(payload, (self.settings["host"], int(self.settings["tcpPort"])))
        elif self.settings["transport"] == "tcp":
            self._link.send(payload)
        else:
            self._link.write(payload)

    def _receive(self) -> bytes:
        if self.settings["transport"] == "serial":
            return self._link.read(self._link.in_waiting or 0)
        received = b""
        try:
            while True:
                chunk = self._link.recv(65536)
                if not chunk and self.settings["transport"] == "tcp":
                    raise ConnectionError("The peer closed the connection.")
                received += chunk
        except BlockingIOError:
            pass
        return received

    def _drain_lines(self) -> list[str]:
        self._buffer += self._receive()
        terminator = _TERMINATORS[self.settings["terminator"]]
        end = self._buffer.rfind(terminator)
        if end < 0:
            return []
        complete, self._buffer = self._buffer[:end], self._buffer[end + len(terminator):]
        return [line.decode("utf-8", "replace") for line in complete.split(terminator) if line.strip()]

    def _frame_times(self, t_now, n):
        # Frames arrive in bursts; spread them across the interval since the
        # previous read. These are host receipt times, not device times.
        if self._last_time != self._last_time:
            self._last_time = t_now - 0.1
        times = self._last_time + (t_now - self._last_time) * np.arange(1, n + 1) / max(n, 1)
        self._last_time = t_now
        return times
