"""SCPI instruments through VISA: GPIB, USBTMC, LXI, serial. Only queries are sent
during acquisition; user-written init commands run once at connect and configure
the measuring instrument only. Uses NI-VISA when present, else PyVISA-py."""
from edaq.hw.driver import Driver, empty_status, make_block, make_candidate
from edaq.util import to_list


def _manager():
    import pyvisa
    try:
        return pyvisa.ResourceManager()
    except (OSError, ValueError):
        return pyvisa.ResourceManager("@py")


class VisaDriver(Driver):
    def __init__(self):
        super().__init__()
        self._link = None
        self._identity = ""
        self._packets_ok = 0
        self._packet_errors = 0
        self._next_poll_t = 0.0
        self._instrument = "not_assessed"
        self._detail = "No error query configured."

    @staticmethod
    def manifest():
        return {"id": "visa", "name": "VISA / GPIB (SCPI)", "version": "1.0.0", "interfaces": ["NI-VISA", "GPIB"], "platforms": ["any"], "requires": ["pyvisa"], "configSchema": [
            {"name": "resource", "label": "VISA resource", "type": "string", "required": True, "help": "e.g. GPIB0::12::INSTR"},
            {"name": "pollHz", "label": "Poll rate (Hz)", "type": "number", "default": 2, "help": ""},
            {"name": "errorQuery", "label": "Error query", "type": "string", "default": "SYST:ERR?", "help": "Gives real instrument-health evidence. Blank to disable."},
            {"name": "initCommands", "label": "Init commands", "type": "string", "default": " ", "help": "Semicolon separated. Sent once at connect."},
            {"name": "channels", "label": "Queries", "type": "table:hw,query", "required": True, "help": "One numeric query per channel, must end with ?"}]}

    @staticmethod
    def discover():
        return [make_candidate("visa", "NI-VISA", resource, "", "", "", "port_only", "VISA resource present.") for resource in _manager().list_resources()]

    def connect(self):
        s = self.settings
        self._rows = to_list(s["channels"])
        self._names = [str(r["hw"]) for r in self._rows]
        for row in self._rows:
            if not str(row["query"]).strip().endswith("?"):
                raise ValueError(f'Channel {row["hw"]}: "{row["query"]}" is not a query.')
        self._link = _manager().open_resource(s["resource"])
        self._link.timeout = 500
        self._identity = self._link.query("*IDN?").strip()
        for command in str(s["initCommands"]).split(";"):
            if command.strip():
                self._link.write(command.strip())

    def identify(self):
        parts = [p.strip() for p in self._identity.split(",")] + ["", "", "", ""]
        return {"vendor": parts[0], "model": parts[1], "serial": parts[2], "firmware": parts[3], "evidence": f"*IDN? response: {self._identity}"}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": float(self.settings["pollHz"]), "description": "SCPI query"} for name in self._names]

    def read(self, t_now):
        if t_now < self._next_poll_t:
            return []
        self._next_poll_t = t_now + 1 / float(self.settings["pollHz"])
        times, values = [], []
        for row in self._rows:
            try:
                value = float(self._link.query(str(row["query"])))
                times.append([t_now])
                values.append([value])
                self._packets_ok += 1
            except ValueError:
                times.append([])
                values.append([])
                self._packet_errors += 1
        self._poll_instrument_errors()
        return make_block(self._names, times, values)

    def status(self):
        st = empty_status()
        st.update(connected=self._link is not None, instrument=self._instrument, instrumentDetail=self._detail,
                  packetsOk=self._packets_ok, packetErrors=self._packet_errors)
        return st

    def disconnect(self):
        if self._link is not None:
            self._link.close()
            self._link = None

    def _poll_instrument_errors(self):
        query = str(self.settings["errorQuery"]).strip()
        if not query:
            return
        reply = self._link.query(query).strip()
        if reply.startswith(("0,", "+0,")):
            self._instrument, self._detail = "ok", "Instrument error queue is empty."
        else:
            self._instrument, self._detail = "degraded", f"Instrument reports: {reply}"
