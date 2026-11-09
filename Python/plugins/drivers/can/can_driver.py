"""CAN and CAN FD through python-can. Listens only by default where the
interface supports it, so the application puts nothing on the bus."""
import math

from edaq.hw.codecs import decode_can
from edaq.hw.driver import Driver, empty_status, make_block
from edaq.util import to_list


class CanDriver(Driver):
    def __init__(self):
        super().__init__()
        self._bus = None
        self._packets_ok = 0
        self._packet_errors = 0
        self._offset = math.nan

    @staticmethod
    def manifest():
        field = lambda name, label, kind, default, help_text="": {"name": name, "label": label, "type": kind, "default": default, "help": help_text}
        return {"id": "can", "name": "CAN / CAN FD", "version": "1.0.0", "interfaces": ["CAN", "CAN FD"], "platforms": ["any"], "requires": ["can"], "configSchema": [
            field("interface", "python-can interface", "string", "virtual", "socketcan, pcan, vector, kvaser, ..."), field("channel", "Channel", "string", "vcan0"),
            field("bitrate", "Bit rate", "number", 500000), field("fd", "CAN FD", "bool", False), field("listenOnly", "Listen only", "bool", True, "Recommended: nothing is transmitted."),
            {"name": "channels", "label": "Signals", "type": "table:hw,messageId,extended,startBit,length,byteOrder,signed,scale,offset", "required": True,
             "help": "byteOrder: intel or motorola, DBC conventions."}]}

    @staticmethod
    def discover():
        import can
        from edaq.hw.driver import make_candidate
        found = []
        for config in can.detect_available_configs():
            found.append(make_candidate("can", "CAN", f'{config.get("interface")} {config.get("channel")}', str(config.get("interface")), "", "",
                                        "device_identified", "Interface found: says nothing about what is on the bus."))
        return found

    def connect(self):
        import can
        s = self.settings
        self._signals = to_list(s["channels"])
        self._names = [str(r["hw"]) for r in self._signals]
        options = {"interface": s["interface"], "channel": s["channel"], "fd": bool(s["fd"])}
        if s["interface"] != "virtual":
            options["bitrate"] = int(s["bitrate"])
        self._bus = can.Bus(**options)
        if s["listenOnly"]:
            try:
                self._bus.state = can.BusState.PASSIVE
            except Exception as error:      # not every interface supports it
                self.last_error = f"Interface does not support listen-only: {error}"
        self._offset = math.nan

    def identify(self):
        return {"vendor": str(self.settings["interface"]), "model": str(self.settings["channel"]), "serial": "", "firmware": "",
                "evidence": "Identifies the CAN interface, not the instruments on the bus."}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": math.nan, "description": "Configured CAN signal"} for name in self._names]

    def read(self, t_now):
        times = {name: [] for name in self._names}
        values = {name: [] for name in self._names}
        while True:
            message = self._bus.recv(timeout=0)
            if message is None:
                break
            if message.is_error_frame:
                self._packet_errors += 1
                continue
            self._packets_ok += 1
            if math.isnan(self._offset):
                self._offset = t_now - message.timestamp
            for signal in self._signals:
                if int(signal["messageId"]) == message.arbitration_id and bool(signal["extended"]) == message.is_extended_id:
                    times[signal["hw"]].append(message.timestamp + self._offset)
                    values[signal["hw"]].append(decode_can(bytes(message.data), int(signal["startBit"]), int(signal["length"]), signal["byteOrder"],
                                                           bool(signal["signed"]), float(signal["scale"]), float(signal["offset"])))
        return make_block(self._names, [times[n] for n in self._names], [values[n] for n in self._names])

    def status(self):
        st = empty_status()
        st.update(connected=self._bus is not None, packetsOk=self._packets_ok, packetErrors=self._packet_errors,
                  instrumentDetail="Bus error state only. Sensors on the bus report nothing here.")
        return st

    def disconnect(self):
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
