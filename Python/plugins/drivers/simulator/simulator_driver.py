"""Simulated engine rig with fault injection. It goes through the same driver
contract as real hardware, so the whole pipeline is exercised."""
import math

import numpy as np

from edaq import sim
from edaq.hw.driver import Driver, empty_status, make_block, make_candidate


class SimulatorDriver(Driver):
    def __init__(self):
        super().__init__()
        self.faults: list[dict] = []
        self._connected = False
        self._origin = math.nan
        self._next: dict = {}
        self._rng = None
        self._packets_ok = 0
        self._packet_errors = 0

    @staticmethod
    def manifest():
        return {"id": "simulator", "name": "Simulated engine rig", "version": "1.0.0", "interfaces": ["Simulation"], "platforms": ["any"],
                "requires": [], "configSchema": [{"name": "seed", "label": "Random seed", "type": "number", "default": 1, "help": "Same seed gives the same noise."}]}

    @staticmethod
    def discover():
        return [make_candidate("simulator", "Simulation", "sim://engine-rig", "EngineDAQ", "Simulated Engine Rig", "SIM-0001",
                               "protocol_verified", "Software model, always present. Not real hardware.")]

    def connect(self):
        if self._active("commLoss", self.time_hint):
            raise ConnectionError("Simulated: device not responding.")
        if self._rng is None:
            self._rng = np.random.default_rng(int(self.settings["seed"]))
        self._connected = True
        self._next = {}

    def identify(self):
        return {"vendor": "EngineDAQ", "model": "Simulated Engine Rig", "serial": "SIM-0001", "firmware": "1.0.0", "evidence": "Software model. Not real hardware."}

    def discover_channels(self):
        return [{"hw": s[0], "unit": s[1], "sampleRateHz": s[2], "description": "Simulated signal"} for s in sim.SIGNALS]

    def inject(self, fault: dict):
        self.faults.append({**fault, "applied": False})

    def clear_faults(self):
        self.faults = []

    def read(self, t_now):
        if self._active("commLoss", t_now):
            self._connected = False
            raise ConnectionError("Simulated: communication lost.")
        if math.isnan(self._origin):
            self._origin = t_now
        names, times, values = [], [], []
        for signal, _, fs, sigma in sim.SIGNALS:
            t, v = self._generate(signal, fs, sigma, t_now)
            names.append(signal)
            times.append(t)
            values.append(v)
        count = sum(t.size for t in times)
        self._packets_ok += count
        if self._active("packetErrors", t_now):
            self._packet_errors += math.ceil(count * 0.2)
        return make_block(names, times, values)

    def status(self):
        st = empty_status()
        st.update(connected=self._connected, packetsOk=self._packets_ok, packetErrors=self._packet_errors,
                  instrument="ok", instrumentDetail="Simulated self-test passed.")
        t = self.time_hint
        if self._active("instrumentFault", t):
            st.update(instrument="fault", instrumentDetail="Simulated DAQ hardware failure.")
        elif self._active("instrumentDegraded", t):
            st.update(instrument="degraded", instrumentDetail="Simulated reference out of tolerance.")
        st["overload"] = self._active("overload", t)
        return st

    def disconnect(self):
        self._connected = False

    def _generate(self, signal, fs, sigma, t_now):
        start = self._next.setdefault(signal, t_now)
        count = int(math.floor((t_now - start) * fs + 1e-9)) + 1
        if count <= 0:
            return np.zeros(0), np.zeros(0)
        t = start + np.arange(count) / fs
        self._next[signal] = float(t[-1]) + 1 / fs
        v = sim.nominal(signal, t - self._origin) + sigma * self._rng.standard_normal(count)
        return self._apply_faults(signal, sigma, t, v)

    def _apply_faults(self, signal, sigma, t, v):
        for f in self.faults:
            if f.get("channel") != signal:
                continue
            mask = (t >= f["t0"]) & (t < f["t0"] + f["durationS"])
            if not mask.any():
                continue
            kind, first = f["kind"], int(np.argmax(mask))
            if kind == "spike":
                if not f["applied"]:
                    v[first] += f["magnitude"]
                    f["applied"] = True
            elif kind == "frozen":
                v[mask] = sim.nominal(signal, f["t0"] - self._origin)
            elif kind == "noise":
                v[mask] += f["magnitude"] * self._rng.standard_normal(int(mask.sum()))
            elif kind == "drift":
                v[mask] += f["rate"] * (t[mask] - f["t0"])
            elif kind == "set":
                v[mask] = f["value"] + sigma * self._rng.standard_normal(int(mask.sum()))
            elif kind == "dropout":
                t, v = t[~mask], v[~mask]
            elif kind == "jitter":
                drop = mask & (self._rng.random(t.size) < 0.6)
                t, v = t[~drop], v[~drop]
            elif kind == "timeBack":
                t[first] -= 0.5
            else:
                raise ValueError(f'Unknown fault kind "{kind}".')
        return t, v

    def _active(self, kind, t):
        return any(f["kind"] == kind and f["t0"] <= t < f["t0"] + f["durationS"] for f in self.faults)
