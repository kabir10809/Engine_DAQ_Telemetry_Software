"""Engine geometry, performance relations and compression ratio with provenance.

Unknown values are NaN and stay NaN. Compression ratio values always carry their
provenance: measured, manual, calculated, configured or unknown. Supporting VCR
never implies that an engine has VCR; that is decided only by the configured mode.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .util import number, opt


@dataclass
class Geometry:
    bore_mm: float = math.nan
    stroke_mm: float = math.nan
    con_rod_mm: float = math.nan
    cylinders: float = math.nan
    strokes_per_cycle: float = 4
    clearance_cm3: float = math.nan

    def swept_cm3(self) -> float:
        return math.pi / 4 * (self.bore_mm / 10) ** 2 * (self.stroke_mm / 10)

    def displacement_cm3(self) -> float:
        return self.swept_cm3() * self.cylinders

    def compression_ratio(self, clearance_cm3=None) -> float:
        clearance = self.clearance_cm3 if clearance_cm3 is None else clearance_cm3
        return (self.swept_cm3() + clearance) / clearance

    def clearance_for(self, ratio) -> float:
        return self.swept_cm3() / (ratio - 1)

    def to_dict(self) -> dict:
        return {"boreMm": self.bore_mm, "strokeMm": self.stroke_mm, "conRodMm": self.con_rod_mm, "cylinders": self.cylinders,
                "strokesPerCycle": self.strokes_per_cycle, "clearanceCm3": self.clearance_cm3}

    @classmethod
    def from_dict(cls, s) -> "Geometry":
        s = s or {}
        return cls(number(s.get("boreMm")), number(s.get("strokeMm")), number(s.get("conRodMm")), number(s.get("cylinders")),
                   number(s.get("strokesPerCycle"), 4), number(s.get("clearanceCm3")))


def _safe_divide(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    with np.errstate(all="ignore"):
        result = a / b
    return np.where(b == 0, np.nan, result)[()]


def brake_power_kw(rpm, torque_nm):
    return 2 * np.pi * np.asarray(rpm) * np.asarray(torque_nm) / 60000


def bmep_kpa(torque_nm, displacement_cm3, strokes_per_cycle):
    return 2 * np.pi * (strokes_per_cycle / 2) * np.asarray(torque_nm) / (displacement_cm3 * 1e-6) / 1000


def bsfc_g_kwh(fuel_kg_h, power_kw):
    return _safe_divide(np.asarray(fuel_kg_h) * 1000, power_kw)


def brake_thermal_efficiency(power_kw, fuel_kg_h, lhv_mj_kg):
    """Fraction, not percent."""
    return _safe_divide(power_kw, np.asarray(fuel_kg_h) / 3600 * lhv_mj_kg * 1000)


def volumetric_efficiency(air_kg_h, rpm, displacement_cm3, strokes_per_cycle, air_density_kg_m3):
    ideal = np.asarray(air_density_kg_m3) * displacement_cm3 * 1e-6 * np.asarray(rpm) / 60 / (strokes_per_cycle / 2) * 3600
    return _safe_divide(air_kg_h, ideal)


def air_fuel_ratio(air_kg_h, fuel_kg_h):
    return _safe_divide(air_kg_h, fuel_kg_h)


def air_density(pressure_kpa, temperature_c):
    """Dry air ideal gas, kg/m3."""
    return np.asarray(pressure_kpa) * 1000 / (287.05 * (np.asarray(temperature_c) + 273.15))


def iso_correction_si(dry_pressure_kpa, temperature_c):
    """ISO 1585 factor for naturally aspirated spark ignition engines only."""
    return (99 / np.asarray(dry_pressure_kpa)) ** 1.2 * ((np.asarray(temperature_c) + 273.15) / 298) ** 0.6


def _hold_latest(series, t):
    """Newest sample at or before each time; NaN before the first sample."""
    result = np.full(t.shape, np.nan)
    if series["t"].size == 0:
        return result
    order = np.argsort(series["t"], kind="stable")
    times, values = series["t"][order], series["value"][order]
    index = np.searchsorted(times, t, side="right") - 1
    result[index >= 0] = values[index[index >= 0]]
    return result


def analyze_run(reader, mapping: dict, geometry: Geometry, fuel: dict) -> pd.DataFrame:
    """Calculated parameters on the speed channel's time base. mapping holds
    channel ids (rpm, torque, fuel, air, intakePress, intakeTemp); outputs of an
    absent input are left out."""
    data = reader.read(list(dict.fromkeys(mapping.values())))
    base = data[mapping["rpm"]]
    t = base["t"]
    table = pd.DataFrame({"t": t, "rpm": base["value"]})
    align = lambda key: _hold_latest(data[mapping[key]], t)
    displacement = geometry.displacement_cm3()
    if "torque" in mapping:
        table["torque_Nm"] = align("torque")
        table["brake_power_kW"] = brake_power_kw(table["rpm"], table["torque_Nm"])
        if math.isfinite(displacement):
            table["bmep_kPa"] = bmep_kpa(table["torque_Nm"], displacement, geometry.strokes_per_cycle)
    if "fuel" in mapping:
        table["fuel_kg_h"] = align("fuel")
        if "brake_power_kW" in table:
            table["bsfc_g_kWh"] = bsfc_g_kwh(table["fuel_kg_h"], table["brake_power_kW"])
            if "lhvMJkg" in fuel:
                table["brake_thermal_efficiency"] = brake_thermal_efficiency(table["brake_power_kW"], table["fuel_kg_h"], fuel["lhvMJkg"])
    if all(k in mapping for k in ("air", "intakePress", "intakeTemp")) and math.isfinite(displacement):
        table["air_kg_h"] = align("air")
        density = air_density(align("intakePress"), align("intakeTemp"))
        table["volumetric_efficiency"] = volumetric_efficiency(table["air_kg_h"], table["rpm"], displacement, geometry.strokes_per_cycle, density)
        if "fuel" in mapping:
            table["air_fuel_ratio"] = air_fuel_ratio(table["air_kg_h"], table["fuel_kg_h"])
    return table


_PROVENANCE = {"fixed": "configured", "measured": "measured", "manual": "manual", "calculated": "calculated", "unknown": "unknown"}


class CompressionRatio:
    """What is known about compression ratio, and how it is known."""

    def __init__(self, config=None, geometry=None):
        config = config or {}
        self.mode = opt(config, "mode", "unknown")
        self.nominal = number(config.get("nominal"))
        self.min = number(config.get("min"))
        self.max = number(config.get("max"))
        self.channel_id = opt(config, "channelId", "")
        self.geometry = geometry or Geometry()
        self.manual_entries: list[dict] = []
        if self.mode not in _PROVENANCE:
            raise ValueError(f'Unknown compression ratio mode "{self.mode}".')
        if self.mode == "measured" and not self.channel_id:
            raise ValueError("Measured compression ratio needs a channel.")

    def is_variable(self) -> bool:
        """True only when the configuration provides for adjustment."""
        return self.mode in ("measured", "manual")

    def enter_manual(self, value, t, note="") -> None:
        """Operator-set ratio, effective from time t."""
        if self.mode != "manual":
            raise ValueError("Compression ratio is not in manual mode.")
        if not (value > 1 and (math.isnan(self.min) or value >= self.min) and (math.isnan(self.max) or value <= self.max)):
            raise ValueError(f"Compression ratio {value:g} is outside the configured limits.")
        self.manual_entries.append({"t": t, "value": value, "note": note})

    def current(self, latest_fn) -> dict:
        """latest_fn returns the newest value of a channel."""
        if self.mode == "fixed":
            return {"value": self.nominal, "provenance": "configured"}
        if self.mode == "measured":
            return {"value": latest_fn(self.channel_id), "provenance": "measured"}
        if self.mode == "manual" and self.manual_entries:
            return {"value": self.manual_entries[-1]["value"], "provenance": "manual"}
        if self.mode == "calculated":
            return {"value": self.geometry.compression_ratio(), "provenance": "calculated"}
        return {"value": math.nan, "provenance": "manual" if self.mode == "manual" else "unknown"}

    def series(self, reader, others: dict) -> pd.DataFrame:
        """Ratio over a recorded run, with other channels aligned to it."""
        ids = list(others.values())
        if self.mode == "measured":
            ids.insert(0, self.channel_id)
        data = reader.read(list(dict.fromkeys(ids)))
        if self.mode == "measured":
            t, value = data[self.channel_id]["t"], data[self.channel_id]["value"]
        else:
            t = data[ids[0]]["t"]
            value = self._values_at(t)
        table = pd.DataFrame({"t": t, "compression_ratio": value, "provenance": _PROVENANCE[self.mode]})
        for name, channel in others.items():
            table[name] = _hold_latest(data[channel], t)
        return table

    @staticmethod
    def compare_by_ratio(table: pd.DataFrame, metric: str, bin_width: float) -> pd.DataFrame:
        """Mean of a performance column per compression ratio."""
        bins = (table["compression_ratio"] / bin_width).round() * bin_width
        grouped = table.groupby(bins)[metric]
        return pd.DataFrame({metric: grouped.mean(), "samples": grouped.size()}).reset_index()

    def to_dict(self) -> dict:
        return {"mode": self.mode, "nominal": self.nominal, "min": self.min, "max": self.max,
                "channelId": self.channel_id, "manualEntries": self.manual_entries}

    def _values_at(self, t):
        values = np.full(t.shape, np.nan)
        if self.mode == "fixed":
            values[:] = self.nominal
        elif self.mode == "calculated":
            values[:] = self.geometry.compression_ratio()
        elif self.mode == "manual":
            for entry in self.manual_entries:
                values[t >= entry["t"]] = entry["value"]
        return values
