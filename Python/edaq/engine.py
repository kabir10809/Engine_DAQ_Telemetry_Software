"""The acquisition pipeline, the health model and the setup library.

Each step: poll devices, calibrate, compute derived channels, validate, update
alarms, record. It is independent of the UI and of any specific hardware, and
step() is deterministic under a manual clock, which is how every alert
classification is tested.
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

import numpy as np

from . import APP_VERSION, RUN_SCHEMA, RUNTIME, SETUP_SCHEMA, alarms, codefile, paths, validation
from .channels.channel import Channel, ChannelManager
from .channels.expression import Expression
from .channels.units import UnitRegistry
from .clock import Clock
from .data.store import EventFile, LiveBuffer, RunRecorder
from .hw.driver import empty_status
from .hw.matrix import CompatibilityMatrix
from .hw.registry import DriverRegistry
from .hw.session import DeviceManager
from .util import iso_time, new_id, opt, sha256, to_list

_PACKET_WINDOW_S = 5
_PACKET_ERROR_LIMIT = 0.02
_CROSS_STALE_S = 5
_EMPTY = np.zeros(0)


class Engine:
    def __init__(self, clock=None, registry=None, matrix=None, units=None, buffer_seconds=600):
        self.clock = clock or Clock()
        self.registry = registry or DriverRegistry()
        self.matrix = matrix or CompatibilityMatrix()
        self.units = units or UnitRegistry(paths.WORKSPACE / "user_units.py")
        self.buffer_seconds = buffer_seconds
        self.devices = DeviceManager(self.registry)
        self.channels = ChannelManager(self.units)
        self.alarms = alarms.AlarmManager()
        self.alarms.supporting_fn = self._supporting_text
        self.alarms.event_sink = self._on_alarm_event
        self.cross_rules: list = []
        self.parameters: dict = {}
        self.setup: dict = {}
        self.running = False
        self.start_t = math.nan
        self.recorder = None
        self.tick_count = 0
        self.last_tick_s = 0.0
        self.max_tick_s = 0.0
        self.loop_errors = 0
        self.last_loop_error = ""
        self.event_log = None
        self._manual: list = []
        self._packet_history: dict = {}
        self.rebuild()

    def set_event_log(self, path) -> None:
        """Keep every alarm event on disk, recording or not."""
        self.event_log = EventFile(path)

    def load_setup(self, setup: dict) -> None:
        """Replace all configuration. Refused while running."""
        self._require_stopped()
        for session in list(self.devices.sessions):
            self.devices.remove(session.id)
        self.channels.clear()
        for device in to_list(setup.get("devices")):
            self.devices.add(device)
        for spec in to_list(setup.get("channels")):
            self.channels.add(Channel.from_dict(spec))
        self.parameters = opt(setup, "parameters", {})
        self.setup = setup
        self.cross_rules = [validation.CrossRule(s, self.channels) for s in to_list(setup.get("crossRules"))]
        self.rebuild()

    def add_device(self, config: dict) -> None:
        self._require_stopped()
        self.devices.add(config)

    def set_cross_rules(self, specs: list) -> None:
        """Replace the cross-channel rules. All are validated first."""
        self._require_stopped()
        self.cross_rules = [validation.CrossRule(s, self.channels) for s in specs]

    def replace_channel(self, channel: Channel) -> None:
        self._require_stopped()
        self.channels.replace(channel)
        self.rebuild()

    def current_setup(self) -> dict:
        setup = dict(self.setup)
        setup.update(schemaVersion=SETUP_SCHEMA, devices=[s.config for s in self.devices.sessions],
                     channels=[c.to_dict() for c in self.channels.list],
                     crossRules=[r.spec() for r in self.cross_rules], parameters=self.parameters)
        return setup

    def rebuild(self) -> None:
        """Recompile routing and rules after configuration changes."""
        self._order = [c for c in self.channels.list if c.enabled]
        self._index = {c.id: k for k, c in enumerate(self._order)}
        self._buffers = {c.id: LiveBuffer(c.sample_rate_hz, self.buffer_seconds) for c in self._order}
        self._rule_sets = {c.id: validation.rules_for_channel(c) for c in self._order}
        self._routes: dict = {}
        self._parsed = {}
        for k, c in enumerate(self._order):
            if c.kind == "hardware":
                self._routes.setdefault((c.device_id, c.hw_channel), []).append(k)
            elif c.kind == "derived":
                self._parsed[c.id] = Expression(c.expression)
        self._derived = [c for c in self.channels.derived_order(self.parameters.keys()) if c.enabled]
        self._packet_history = {}

    def start(self) -> None:
        """Connect all devices and begin monitoring."""
        if self.running:
            return
        self.rebuild()
        self.start_t = self.clock.now()
        self.devices.open_all(self.start_t)
        self.running = True

    def stop(self) -> None:
        if not self.running:
            return
        self.alarms.flush(self.clock.now())
        self.stop_recording()
        self.devices.close_all()
        self.running = False

    def start_recording(self, folder, extra: dict) -> None:
        """Snapshot configuration and begin writing a run."""
        if not self.running:
            raise RuntimeError("Start acquisition before recording.")
        if self.recorder:
            raise RuntimeError("Already recording.")
        setup = self.current_setup()
        manifest = {"schemaVersion": RUN_SCHEMA, "runtime": RUNTIME, "appVersion": APP_VERSION, "startT": self.clock.now(),
                    "endT": None, "source": "acquisition", "channels": [c.to_dict() for c in self._order],
                    "devices": self._device_snapshot(), "setup": setup, "configSha256": sha256(codefile.encode(setup)), "context": extra}
        self.recorder = RunRecorder(folder, manifest)

    def stop_recording(self) -> None:
        if self.recorder:
            self.recorder.close(self.clock.now())
            self.recorder = None

    def set_manual(self, channel_id, value, note="") -> None:
        """Queue an operator-entered reading for a manual channel."""
        if self.channels.get(channel_id).kind != "manual":
            raise ValueError(f"Channel {channel_id} is not a manual channel.")
        t = self.clock.now()
        self._manual.append((channel_id, t, value))
        if self.recorder:
            self.recorder.event({"t": t, "time": iso_time(t), "event": "manual_entry", "channel": channel_id, "value": value, "detail": note})

    def step(self) -> None:
        if not self.running:
            return
        started = time.perf_counter()
        t = self.clock.now()
        n = len(self._order)
        t_new, raw_new = self._acquire(t, n)
        v_new = list(raw_new)
        flags_new = [_EMPTY] * n
        for k, c in enumerate(self._order):
            if c.kind != "derived":
                v_new[k] = c.calibration.apply(raw_new[k]) if raw_new[k].size else raw_new[k]
                flags_new[k] = self._ingest_and_validate(c, t_new[k], raw_new[k], v_new[k], t, 0)
        for c in self._derived:
            k = self._index[c.id]
            t_new[k], raw_new[k], input_flags = self._compute_derived(c)
            v_new[k] = raw_new[k]
            flags_new[k] = self._ingest_and_validate(c, t_new[k], raw_new[k], v_new[k], t, input_flags)
        self._evaluate_cross_rules(t, t_new, flags_new)
        self._evaluate_devices(t)
        if self.recorder:
            for k in range(n):
                self.recorder.append(k + 1, t_new[k], raw_new[k], v_new[k], flags_new[k])
        self.tick_count += 1
        self.last_tick_s = time.perf_counter() - started
        self.max_tick_s = max(self.max_tick_s, self.last_tick_s)

    def safe_step(self) -> None:
        """A loop that throws must not stop monitoring silently, so errors are
        counted and raised as an alarm while acquisition continues."""
        try:
            self.step()
        except Exception as error:
            self.loop_errors += 1
            self.last_loop_error = str(error)
            self.alarms.update("sys:loop", True, {"source": "Acquisition loop", "classification": alarms.INSTRUMENT_FAULT, "severity": "critical",
                                                  "reason": f"Acquisition software error: {error}",
                                                  "recommendation": "Monitoring may be incomplete. Check the diagnostics page and do not rely on this application until resolved."},
                               self.clock.now())

    def latest(self, channel_id):
        """(value, time, flags) of the newest sample."""
        buffer = self._buffers.get(channel_id)
        if buffer is None:
            return math.nan, math.nan, 0
        t, v, _, flags = buffer.last()
        return v, t, flags

    def history(self, channel_id, seconds):
        """(t, v, flags) over the last `seconds`."""
        buffer = self._buffers.get(channel_id)
        if buffer is None:
            return _EMPTY, _EMPTY, _EMPTY
        t, v, _, flags = buffer.window(seconds, self.clock.now())
        return t, v, flags

    def overall_status(self) -> str:
        """red, yellow, green or no_data. Green is only available once real
        samples are flowing; idle is never green."""
        state = self.alarms.overall()
        return "no_data" if state == "green" and not self._has_fresh_data() else state

    def _require_stopped(self) -> None:
        if self.running:
            raise RuntimeError("Stop acquisition before changing the configuration.")

    def _acquire(self, t, n):
        t_new, raw_new = [[] for _ in range(n)], [[] for _ in range(n)]
        for session in self.devices.sessions:
            session.driver.note_time(t)
            for block in session.poll(t):
                for k in self._routes.get((session.id, block.hw), []):
                    t_new[k].append(block.t)
                    raw_new[k].append(block.raw)
        for channel_id, when, value in self._manual:
            if channel_id in self._index:
                t_new[self._index[channel_id]].append(np.array([when]))
                raw_new[self._index[channel_id]].append(np.array([float(value)]))
        self._manual = []
        join = lambda parts: np.concatenate(parts) if parts else _EMPTY
        return [join(p) for p in t_new], [join(p) for p in raw_new]

    def _ingest_and_validate(self, c, t_new, raw_new, v_new, t, input_flags):
        buffer = self._buffers[c.id]
        previous_t, previous_v, _, _ = buffer.last()
        prev_t, prev_v = (_EMPTY, _EMPTY) if math.isnan(previous_t) else (np.array([previous_t]), np.array([previous_v]))
        count = t_new.size
        flags = np.full(count, input_flags, np.int64)
        buffer.append(t_new, raw_new, v_new, flags)
        last_t = float(t_new[-1]) if count else previous_t
        session_state = "online"
        if c.kind == "hardware":
            session_state = self.devices.get(c.device_id).state if self.devices.has(c.device_id) else "offline"
        ctx = validation.Context(c, t_new, v_new, raw_new, prev_t, prev_v, t, self.start_t, last_t, session_state, flags, buffer)
        for rule in self._rule_sets[c.id]:
            if count == 0 and not rule.time_driven:
                continue
            result = rule.evaluate(ctx)
            if result.mask.size:
                flags[result.mask] |= rule.flag
            if result.active:
                self.alarms.update(rule.key, True, {"source": c.name, "channel": c.id, "equipment": c.equipment,
                                                    "classification": result.classification, "severity": result.severity,
                                                    "persistenceS": rule.persistence_s, "clearDelayS": rule.clear_delay_s,
                                                    "value": result.value, "expected": result.expected, "reason": result.reason,
                                                    "recommendation": rule.recommendation}, t)
            else:
                self.alarms.update(rule.key, False, None, t)
        buffer.or_flags(count, flags)
        return flags

    def _compute_derived(self, c):
        """Latest-value evaluation at the newest input time. Inputs are not
        interpolated, so a derived value never mixes in invented samples."""
        expression = self._parsed[c.id]
        variables = dict(self.parameters)
        newest, input_flags = math.nan, 0
        for name in expression.inputs:
            if name in self.parameters:
                continue
            value, when, flags = self.latest(name)
            if math.isnan(when):
                return _EMPTY, _EMPTY, 0
            variables[name] = value
            newest = when if math.isnan(newest) else max(newest, when)
            if flags:
                input_flags = validation.DERIVED_INPUT
        last_t = self._buffers[c.id].last()[0]
        if math.isnan(newest) or (not math.isnan(last_t) and newest <= last_t):
            return _EMPTY, _EMPTY, 0
        return np.array([newest]), np.array([float(expression.evaluate(variables))]), input_flags

    def _evaluate_cross_rules(self, t, t_new, flags_new) -> None:
        for rule in self.cross_rules:
            variables, ok, parts = dict(self.parameters), True, []
            for name in rule.expression.inputs:
                value, when, _ = self.latest(name)
                ok = ok and not math.isnan(value) and t - when <= _CROSS_STALE_S
                variables[name] = value
                parts.append(f"{name}={value:.4g}")
            if not (ok and float(rule.expression.evaluate(variables)) != 0):
                self.alarms.update(rule.key, False, None, t)
                continue
            self.alarms.update(rule.key, True, {"source": "Cross-channel check", "channel": ", ".join(rule.expression.inputs),
                                                "equipment": rule.equipment, "classification": rule.classification, "severity": rule.severity,
                                                "persistenceS": rule.persistence_s, "clearDelayS": rule.clear_delay_s,
                                                "expected": f"NOT ({rule.expression.source})", "reason": f"{rule.description} [{', '.join(parts)}]",
                                                "recommendation": rule.recommendation}, t)
            for name in rule.expression.inputs:
                k = self._index[name]
                if t_new[k].size:
                    flags_new[k] = flags_new[k] | validation.CROSS
                    self._buffers[name].or_flags(t_new[k].size, np.full(t_new[k].size, validation.CROSS))

    def _evaluate_devices(self, t) -> None:
        for session in self.devices.sessions:
            device, name, status = session.id, opt(session.config, "name", session.id), session.last_status
            base = {"source": name, "equipment": name, "clearDelayS": 1}
            comm, instrument = alarms.COMMUNICATION_FAULT, alarms.INSTRUMENT_FAULT
            self.alarms.update(f"dev:{device}:link", session.state in ("reconnecting", "failed"),
                               {**base, "classification": comm, "severity": "warning", "reason": f"Link to the device is down: {session.last_error}",
                                "recommendation": "Check cable, power and address. Reconnection is automatic while enabled."}, t)
            self.alarms.update(f"dev:{device}:instrument_fault", status["instrument"] == "fault",
                               {**base, "classification": instrument, "severity": "critical", "reason": f"Instrument reports a fault: {status['instrumentDetail']}",
                                "recommendation": "Inspect the instrument; its readings cannot be trusted until the fault is cleared."}, t)
            self.alarms.update(f"dev:{device}:instrument_degraded", status["instrument"] == "degraded",
                               {**base, "classification": instrument, "severity": "warning", "reason": f"Instrument reports degraded operation: {status['instrumentDetail']}",
                                "recommendation": "Review the instrument's diagnostics before relying on its readings."}, t)
            self.alarms.update(f"dev:{device}:overload", bool(status["overload"]),
                               {**base, "classification": instrument, "severity": "critical", "reason": "Instrument reports an input overload.",
                                "recommendation": "Reduce the input or correct the range; check the input for damage."}, t)
            self._evaluate_packets(device, base, status, t)

    def _evaluate_packets(self, device, base, status, t) -> None:
        history = self._packet_history.setdefault(device, [(t, status["packetsOk"], status["packetErrors"])])
        if t - history[-1][0] >= 1:
            history.append((t, status["packetsOk"], status["packetErrors"]))
            history[:] = [h for h in history if h[0] >= t - _PACKET_WINDOW_S]
        errors = status["packetErrors"] - history[0][2]
        total = status["packetsOk"] - history[0][1] + errors
        fraction = errors / max(total, 1)
        self.alarms.update(f"dev:{device}:packets", fraction > _PACKET_ERROR_LIMIT,
                           {**base, "classification": alarms.COMMUNICATION_FAULT, "severity": "warning", "persistenceS": 0, "clearDelayS": 2,
                            "value": fraction * 100, "expected": f"error rate <= {_PACKET_ERROR_LIMIT * 100:.1f}%",
                            "reason": f"{errors} of {total} packets failed in the last {_PACKET_WINDOW_S} s.",
                            "recommendation": "Check cabling, termination and bus load."}, t)

    def _has_fresh_data(self) -> bool:
        if not self.running:
            return False
        now = self.clock.now()
        return any(not math.isnan(b.last()[0]) and now - b.last()[0] < 5 for b in self._buffers.values())

    def _supporting_text(self, condition) -> str:
        """Snapshot of related measurements at activation."""
        parts = []
        for c in self._order:
            if (c.id == condition["channel"] or (condition["equipment"] and c.equipment == condition["equipment"])) and len(parts) < 8:
                parts.append(f"{c.id}={self.latest(c.id)[0]:.4g} {c.unit}")
        return "; ".join(parts)

    def _on_alarm_event(self, event) -> None:
        if self.recorder:
            self.recorder.event(event)
        if self.event_log:
            self.event_log.append(event)

    def _device_snapshot(self) -> list[dict]:
        return [{"id": s.id, "driver": s.config["driver"], "driverVersion": self.registry.entry(s.config["driver"])["manifest"]["version"],
                 "identity": s.identity} for s in self.devices.sessions]


def compute_health(engine) -> dict:
    """Communication, instrument, measurement and equipment health, separately.
    No layer's good state is ever inferred from another: a live link says
    nothing about the instrument, and an instrument that gives no
    self-diagnostics is not_assessed, never ok."""
    devices = []
    for s in engine.devices.sessions:
        status = s.last_status
        comm = {"online": "ok", "reconnecting": "lost", "failed": "lost"}.get(s.state, "offline")
        detail = s.last_error
        if comm == "ok" and engine.alarms.is_visible(f"dev:{s.id}:packets"):
            comm, detail = "degraded", "Packet errors"
        devices.append({"device": s.id, "comm": comm, "commDetail": detail, "instrument": status["instrument"],
                        "instrumentDetail": status["instrumentDetail"], "packetsOk": status["packetsOk"],
                        "packetErrors": status["packetErrors"], "reconnects": s.reconnect_count})
    return {"overall": engine.overall_status(), "devices": devices, "channels": channel_quality(engine), "equipment": equipment_health(engine)}


_QUALITY_SUFFIXES = ("range", "roc", "stuck", "noise", "outlier", "drift", "interval", "timestamp", "calibration", "missing", "commloss")


def channel_quality(engine) -> list[dict]:
    rows, now = [], engine.clock.now()
    for c in engine.channels.list:
        value, when, flags = engine.latest(c.id)
        suspect = any(engine.alarms.is_visible(f"chan:{c.id}:{suffix}") for suffix in _QUALITY_SUFFIXES)
        quality = "no_data" if math.isnan(when) or not c.enabled else ("suspect" if suspect else "good")
        rows.append({"channel": c.id, "value": value, "age": now - when, "quality": quality, "flags": validation.describe_flags(flags)})
    return rows


def equipment_health(engine) -> dict:
    """Reports what is known. With no fault signals wired there is no evidence
    about the rig, and the answer says so."""
    signals = [c for c in engine.channels.list if c.enabled and c.role != "measurement"]
    if not signals:
        return {"state": "not_monitored", "detail": "No equipment fault or interlock signals are configured."}
    asserted, unknown = [], []
    for c in signals:
        value, when, _ = engine.latest(c.id)
        if math.isnan(when) or engine.clock.now() - when > 5:
            unknown.append(c.id)
        elif value == c.active_value:
            asserted.append(c.id)
    if asserted:
        return {"state": "fault", "detail": f"Asserted: {', '.join(asserted)}"}
    if unknown:
        return {"state": "unknown", "detail": f"No recent data from: {', '.join(unknown)}"}
    return {"state": "no_fault_reported", "detail": f"{len(signals)} signal(s) clear. Covers only what those signals observe."}


class SetupLibrary:
    """Named setups, one code file each. current (restored at launch) and
    rejected_ files belong to the application and are not listed."""

    def __init__(self, folder=None):
        self.folder = Path(folder or paths.SETUPS)

    def list(self) -> list[str]:
        names = sorted(file.stem for file in self.folder.glob("*.py"))
        return [n for n in names if n != "current" and not n.startswith("rejected_")]

    def exists(self, name) -> bool:
        return self._path(name).is_file()

    def save(self, name, setup: dict, overwrite=False) -> None:
        if self.exists(name) and not overwrite:
            raise FileExistsError(f'A setup named "{name}" already exists.')
        codefile.write(self._path(name), {**setup, "name": self.normalize(name)})

    def read(self, name) -> dict:
        if not self.exists(name):
            raise KeyError(f'No setup named "{name}".')
        return codefile.read(self._path(name))

    def remove(self, name) -> None:
        if not self.exists(name):
            raise KeyError(f'No setup named "{name}".')
        self._path(name).unlink()

    @staticmethod
    def normalize(name) -> str:
        """The stored name, which is also a valid file name."""
        name = str(name).strip().replace(" ", "_")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,59}", name):
            raise ValueError("Setup names may use letters, digits, spaces and underscores, must start with a letter, and be at most 60 characters.")
        if name.lower() == "current" or name.lower().startswith("rejected_"):
            raise ValueError(f'The name "{name}" is reserved by the application.')
        return name

    def _path(self, name) -> Path:
        return self.folder / f"{self.normalize(name)}.py"


def restore_setup(engine, path) -> str:
    """Load the saved setup, or the demo if there is none or it cannot be used
    here. An unusable file is set aside, never overwritten. Returns a notice."""
    from .sim import demo_setup
    path = Path(path)
    if not path.is_file():
        engine.load_setup(demo_setup())
        return ""
    try:
        engine.load_setup(codefile.read(path))
        return ""
    except Exception as error:
        rejected = path.with_name(f"{new_id('rejected')}.py")
        path.replace(rejected)
        engine.load_setup(demo_setup())
        return f"The saved setup could not be used, so the demo setup was loaded.\n\nReason: {error}\n\nThe file was kept as {rejected}"
