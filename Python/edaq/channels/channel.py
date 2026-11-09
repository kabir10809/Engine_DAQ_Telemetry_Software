"""Measurement channels, fully defined by configuration, and their calibration.

Nothing in the application knows about RPM or torque in particular; those are
just channels a setup happens to contain. id is stable and used by expressions
and recorded data; name is free text and can be renamed at any time.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..util import number, opt, to_list
from .expression import Expression


class ConfigError(ValueError):
    pass


@dataclass
class Calibration:
    """Raw-to-engineering conversion plus its traceability metadata."""
    type: str = "none"              # none, linear, polynomial, table
    coefficients: list = field(default_factory=list)   # linear: [gain, offset]; polynomial: ascending powers
    table_raw: list = field(default_factory=list)
    table_value: list = field(default_factory=list)
    raw_min: float = math.nan       # calibrated span of the raw signal
    raw_max: float = math.nan
    certificate: str = ""
    calibrated_on: str = ""         # ISO date
    due_on: str = ""
    calibrated_by: str = ""

    def apply(self, raw):
        raw = np.asarray(raw, float)
        if self.type == "none":
            return raw
        if self.type == "linear":
            return self.coefficients[0] * raw + self.coefficients[1]
        if self.type == "polynomial":
            return np.polyval(list(reversed(self.coefficients)), raw)
        if self.type == "table":
            # NaN outside the table: extrapolating a calibration invents data.
            return np.interp(raw, self.table_raw, self.table_value, left=math.nan, right=math.nan)
        raise ConfigError(f'Unknown calibration type "{self.type}".')

    def validate(self) -> None:
        if self.type == "linear" and not (len(self.coefficients) == 2 and all(map(math.isfinite, self.coefficients))):
            raise ConfigError("Linear calibration needs [gain, offset].")
        if self.type == "polynomial" and not (self.coefficients and all(map(math.isfinite, self.coefficients))):
            raise ConfigError("Polynomial calibration needs coefficients.")
        if self.type == "table":
            increasing = all(b > a for a, b in zip(self.table_raw, self.table_raw[1:]))
            if len(self.table_raw) < 2 or len(self.table_raw) != len(self.table_value) or not increasing:
                raise ConfigError("Table calibration needs at least two strictly increasing raw points.")
        if self.type not in ("none", "linear", "polynomial", "table"):
            raise ConfigError(f'Unknown calibration type "{self.type}".')

    def to_dict(self) -> dict:
        return {"type": self.type, "coefficients": list(self.coefficients), "tableRaw": list(self.table_raw),
                "tableValue": list(self.table_value), "rawMin": self.raw_min, "rawMax": self.raw_max,
                "certificate": self.certificate, "calibratedOn": self.calibrated_on, "dueOn": self.due_on,
                "calibratedBy": self.calibrated_by}

    @classmethod
    def from_dict(cls, s) -> "Calibration":
        s = s or {}
        c = cls(type=opt(s, "type", "none"), coefficients=[float(x) for x in to_list(opt(s, "coefficients"))],
                table_raw=[float(x) for x in to_list(opt(s, "tableRaw"))],
                table_value=[float(x) for x in to_list(opt(s, "tableValue"))],
                raw_min=number(s.get("rawMin")), raw_max=number(s.get("rawMax")),
                certificate=opt(s, "certificate", ""), calibrated_on=opt(s, "calibratedOn", ""),
                due_on=opt(s, "dueOn", ""), calibrated_by=opt(s, "calibratedBy", ""))
        c.validate()
        return c


# Attribute name and stored key for each numeric field that may be unset.
_NUMERIC = (("range_min", "rangeMin"), ("range_max", "rangeMax"), ("sample_rate_hz", "sampleRateHz"),
            ("accuracy", "accuracy"), ("resolution", "resolution"), ("warn_low", "warnLow"), ("warn_high", "warnHigh"),
            ("crit_low", "critLow"), ("crit_high", "critHigh"))


@dataclass
class Channel:
    id: str = ""
    name: str = ""
    kind: str = "hardware"          # hardware, derived, manual
    role: str = "measurement"       # measurement, fault_signal, interlock
    enabled: bool = True
    sensor_type: str = ""
    description: str = ""
    equipment: str = ""
    device_id: str = ""
    hw_channel: str = ""
    expression: str = ""
    quantity: str = "Dimensionless"
    unit: str = "-"
    raw_unit: str = ""
    calibration: Calibration = field(default_factory=Calibration)
    range_min: float = math.nan     # sensor measurement range
    range_max: float = math.nan
    sample_rate_hz: float = math.nan
    accuracy: float = math.nan
    resolution: float = math.nan
    warn_low: float = math.nan
    warn_high: float = math.nan
    crit_low: float = math.nan
    crit_high: float = math.nan
    critical: bool = False          # loss of this channel is a red condition
    active_value: float = 1.0       # level treated as asserted for fault_signal and interlock
    rules: list = field(default_factory=list)

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.id):
            raise ConfigError(f'Channel id "{self.id}" must be letters, digits and underscores, not starting with a digit.')
        if self.kind not in ("hardware", "derived", "manual"):
            raise ConfigError(f'Channel {self.id} has unknown kind "{self.kind}".')
        if self.role not in ("measurement", "fault_signal", "interlock"):
            raise ConfigError(f'Channel {self.id} has unknown role "{self.role}".')
        if self.kind == "hardware" and not (self.device_id and self.hw_channel):
            raise ConfigError(f"Hardware channel {self.id} needs a device and a hardware channel.")
        if self.kind == "derived":
            Expression(self.expression)

        def ordered(lo, hi):
            return math.isnan(lo) or math.isnan(hi) or lo <= hi
        if not (ordered(self.range_min, self.range_max) and ordered(self.warn_low, self.warn_high)
                and ordered(self.crit_low, self.crit_high) and ordered(self.crit_low, self.warn_low)
                and ordered(self.warn_high, self.crit_high)):
            raise ConfigError(f"Channel {self.id} has inconsistent limits (need critLow <= warnLow <= warnHigh <= critHigh).")
        self.calibration.validate()

    def to_dict(self) -> dict:
        s = {"id": self.id, "name": self.name, "kind": self.kind, "role": self.role, "enabled": self.enabled,
             "sensorType": self.sensor_type, "description": self.description, "equipment": self.equipment,
             "deviceId": self.device_id, "hwChannel": self.hw_channel, "expression": self.expression,
             "quantity": self.quantity, "unit": self.unit, "rawUnit": self.raw_unit, "calibration": self.calibration.to_dict()}
        s.update({key: getattr(self, attr) for attr, key in _NUMERIC})
        s.update({"critical": self.critical, "activeValue": self.active_value, "rules": list(self.rules)})
        return s

    @classmethod
    def from_dict(cls, s: dict) -> "Channel":
        c = cls(id=s["id"], name=opt(s, "name", s["id"]), kind=opt(s, "kind", "hardware"), role=opt(s, "role", "measurement"),
                enabled=bool(s.get("enabled", True)), sensor_type=opt(s, "sensorType", ""), description=opt(s, "description", ""),
                equipment=opt(s, "equipment", ""), device_id=opt(s, "deviceId", ""), hw_channel=opt(s, "hwChannel", ""),
                expression=opt(s, "expression", ""), quantity=opt(s, "quantity", "Dimensionless"), unit=opt(s, "unit", "-"),
                raw_unit=opt(s, "rawUnit", ""), calibration=Calibration.from_dict(s.get("calibration")),
                critical=bool(s.get("critical", False)), active_value=number(s.get("activeValue"), 1.0),
                rules=to_list(opt(s, "rules")))
        for attr, key in _NUMERIC:
            setattr(c, attr, number(s.get(key)))
        c.validate()
        return c


class ChannelManager:
    def __init__(self, units):
        self.units = units
        self.list: list[Channel] = []

    def add(self, channel: Channel) -> None:
        if self.has(channel.id):
            raise ConfigError(f'Channel id "{channel.id}" already exists.')
        channel.validate()
        if not self.units.is_known(channel.unit):
            raise ConfigError(f'Channel {channel.id} uses unknown unit "{channel.unit}". Define it first.')
        self.list.append(channel)

    def remove(self, channel_id) -> None:
        """Refuses while another channel's formula depends on it."""
        users = self.dependents(channel_id)
        if users:
            raise ConfigError(f'Channel "{channel_id}" is used by: {", ".join(users)}.')
        self.list.remove(self.get(channel_id))

    def clear(self) -> None:
        self.list = []

    def replace(self, channel: Channel) -> None:
        channel.validate()
        self.list[self.list.index(self.get(channel.id))] = channel

    def rename(self, channel_id, name) -> None:
        self.get(channel_id).name = name

    def get(self, channel_id) -> Channel:
        for channel in self.list:
            if channel.id == channel_id:
                return channel
        raise KeyError(f'No channel with id "{channel_id}".')

    def has(self, channel_id) -> bool:
        return any(c.id == channel_id for c in self.list)

    def ids(self) -> list[str]:
        return [c.id for c in self.list]

    def dependents(self, channel_id) -> list[str]:
        return [c.id for c in self.list if c.kind == "derived" and channel_id in Expression(c.expression).inputs]

    def derived_order(self, parameters=()) -> list[Channel]:
        """Derived channels sorted so inputs compute first. Rejects unknown
        inputs and circular definitions."""
        derived = [c for c in self.list if c.kind == "derived"]
        derived_ids = {c.id for c in derived}
        order, done, pending = [], set(), list(derived)
        known = set(self.ids()) | set(parameters)
        while pending:
            progressed = False
            for c in list(pending):
                inputs = Expression(c.expression).inputs
                unknown = [name for name in inputs if name not in known]
                if unknown:
                    raise ConfigError(f"Channel {c.id} references unknown input(s): {', '.join(unknown)}.")
                if all(name not in derived_ids or name in done for name in inputs):
                    order.append(c)
                    done.add(c.id)
                    pending.remove(c)
                    progressed = True
            if not progressed:
                raise ConfigError(f"Circular derived channels: {', '.join(c.id for c in pending)}.")
        return order
