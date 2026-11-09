"""Configurable checks on channels, and per-sample quality flags.

Rules only report; raw data is never altered or discarded. A new rule type is a
new Rule subclass registered in RULE_TYPES.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from . import alarms
from .channels.expression import Expression
from .util import opt

RANGE, ROC, STUCK, NOISE, OUTLIER, THRESHOLD, CROSS, TIMESTAMP = 1, 2, 4, 8, 16, 32, 64, 128
COMM, MISSING, DRIFT, CALIBRATION, INTERVAL, DERIVED_INPUT = 256, 512, 1024, 2048, 4096, 8192
FLAG_NAMES = ("RANGE", "ROC", "STUCK", "NOISE", "OUTLIER", "THRESHOLD", "CROSS", "TIMESTAMP", "COMM", "MISSING",
              "DRIFT", "CALIBRATION", "INTERVAL", "DERIVED_INPUT")


def describe_flags(mask) -> str:
    mask = int(mask)
    return "|".join(name for k, name in enumerate(FLAG_NAMES) if mask & (1 << k))


class UnverifiedFault(ValueError):
    """A rule claimed a confirmed equipment fault without a fault signal."""


@dataclass
class Result:
    active: bool
    mask: np.ndarray
    value: float
    expected: str
    reason: str
    classification: str
    severity: str


@dataclass
class Context:
    ch: object
    t_new: np.ndarray
    v_new: np.ndarray
    raw_new: np.ndarray
    prev_t: np.ndarray
    prev_v: np.ndarray
    t_now: float
    start_t: float
    last_t: float
    device_state: str
    flags_new: np.ndarray
    buffer: object


def limit_text(lo, hi, unit) -> str:
    if math.isnan(lo):
        return f"<= {hi:g} {unit}"
    if math.isnan(hi):
        return f">= {lo:g} {unit}"
    return f"{lo:g} to {hi:g} {unit}"


class Rule:
    flag = 0
    time_driven = False             # evaluated on ticks with no new samples

    def __init__(self, ch, spec, suffix):
        self.spec = spec
        self.key = f"chan:{ch.id}:{suffix}"
        self.classification = alarms.MEASUREMENT_ANOMALY
        self.severity = "warning"
        self.persistence_s = 0.0
        self.clear_delay_s = 1.0
        self.recommendation = "Inspect the sensor, wiring and signal conditioning."

    def apply_overrides(self) -> None:
        """Spec fields win over each rule's built-in defaults."""
        s = self.spec
        self.persistence_s = opt(s, "persistenceS", self.persistence_s)
        self.clear_delay_s = opt(s, "clearDelayS", self.clear_delay_s)
        self.recommendation = opt(s, "recommendation", self.recommendation)
        self.severity = opt(s, "severity", self.severity)
        wanted = opt(s, "classification")
        if wanted:
            if wanted == alarms.CONFIRMED_EQUIPMENT_FAULT and not isinstance(self, FaultSignal):
                raise UnverifiedFault(f"Rule {self.key} cannot be classified as a confirmed equipment fault: a single "
                                      "measurement is not proof of one. Use a fault_signal channel or a cross-channel "
                                      "rule that includes one.")
            self.classification = wanted

    def evaluate(self, ctx: Context) -> Result:
        raise NotImplementedError

    def result(self, active, mask, value, expected, reason) -> Result:
        return Result(active, mask, value, expected, reason, self.classification, self.severity)

    def quiet(self, ctx) -> Result:
        return self.result(False, np.zeros(ctx.t_new.size, bool), math.nan, "", "")


class Range(Rule):
    flag = RANGE

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "range")
        self.min = float(opt(spec, "min", ch.range_min))
        self.max = float(opt(spec, "max", ch.range_max))
        self.recommendation = "Check for an open or shorted sensor, wrong range setting or scaling error."
        self.apply_overrides()

    def evaluate(self, ctx):
        v = ctx.v_new
        mask = ~np.isfinite(v) | (v < self.min) | (v > self.max)
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        bad = float(v[mask][-1])
        return self.result(True, mask, bad, limit_text(self.min, self.max, ctx.ch.unit),
                           f"Reading {bad:g} is outside the sensor measurement range.")


class Threshold(Rule):
    """A critical limit stays red even on a suspect reading: the application
    cannot prove the sensor wrong, so it flags the doubt and leaves the decision
    to the operator. Persistence stops one bad sample reaching red."""
    flag = THRESHOLD

    def __init__(self, ch, spec):
        self.level = opt(spec, "level", "warning")
        super().__init__(ch, spec, f"threshold_{self.level}")
        if self.level == "critical":
            self.low = float(opt(spec, "low", ch.crit_low))
            self.high = float(opt(spec, "high", ch.crit_high))
            self.classification = alarms.CRITICAL_SAFETY_ALARM
            self.severity = "critical"
            self.recommendation = ("Follow the rig's critical-limit procedure. Act through the rig's independent "
                                   "safety system; this application does not control the rig.")
        else:
            self.low = float(opt(spec, "low", ch.warn_low))
            self.high = float(opt(spec, "high", ch.warn_high))
            self.recommendation = "Review the operating point and confirm the reading against a second source."
        self.persistence_s = 0.5
        self.clear_delay_s = 2.0
        self.apply_overrides()

    def evaluate(self, ctx):
        v = ctx.v_new
        mask = (v < self.low) | (v > self.high)
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        bad = float(v[mask][-1])
        reason = f"Reading {bad:g} {ctx.ch.unit} breached the {self.level} limit."
        if (ctx.flags_new[mask] != 0).any():
            reason += " The reading is also flagged suspect: verify the sensor, but treat the limit as real until shown otherwise."
        return self.result(True, mask, bad, limit_text(self.low, self.high, ctx.ch.unit), reason)


def _diff_against_previous(previous, new):
    return np.diff(np.concatenate((previous, new)))


class Roc(Rule):
    """Physically unrealistic jump between consecutive samples."""
    flag = ROC

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "roc")
        self.max_rate = float(spec["maxRate"])
        self.recommendation = "Look for electrical interference, a loose connection or a decoding error."
        self.apply_overrides()

    def evaluate(self, ctx):
        if ctx.t_new.size == 0:
            return self.quiet(ctx)
        with np.errstate(all="ignore"):
            rate = np.abs(_diff_against_previous(ctx.prev_v, ctx.v_new)) / np.maximum(
                _diff_against_previous(ctx.prev_t, ctx.t_new), np.finfo(float).eps)
        if rate.size < ctx.t_new.size:      # first sample ever has no predecessor
            rate = np.concatenate(([0.0], rate))
        mask = rate > self.max_rate
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        return self.result(True, mask, float(ctx.v_new[mask][-1]), f"|rate| <= {self.max_rate:g} {ctx.ch.unit}/s",
                           f"Jump of {rate.max():.4g} {ctx.ch.unit}/s between samples is unrealistic.")


class Stuck(Rule):
    """Frozen signal: no movement beyond tolerance for a whole window."""
    flag = STUCK

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "stuck")
        self.window_s = float(spec["windowS"])
        self.tolerance = float(opt(spec, "tolerance", 0))
        self.recommendation = "Check for a seized sensor, a frozen transmitter or a driver repeating stale data."
        self.apply_overrides()

    def evaluate(self, ctx):
        t, v = ctx.buffer.window(self.window_s, ctx.t_now)[:2]
        if t.size < 5 or t[-1] - t[0] < 0.9 * self.window_s or v.max() - v.min() > self.tolerance:
            return self.quiet(ctx)
        return self.result(True, np.ones(ctx.t_new.size, bool), float(v[-1]),
                           f"variation > {self.tolerance:g} {ctx.ch.unit} in {self.window_s:g} s",
                           f"Value has not moved for {t[-1] - t[0]:.1f} s.")


class Noise(Rule):
    """Excessive noise. Sigma comes from successive differences, which ignores slow trends."""
    flag = NOISE

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "noise")
        self.window_s = float(spec["windowS"])
        self.max_std = float(spec["maxStd"])
        self.persistence_s = 1.0
        self.recommendation = "Check shielding, grounding and nearby ignition or VFD noise sources."
        self.apply_overrides()

    def evaluate(self, ctx):
        v = ctx.buffer.window(self.window_s, ctx.t_now)[1]
        if v.size < 8:
            return self.quiet(ctx)
        sigma = float(np.std(np.diff(v), ddof=1) / math.sqrt(2))
        if sigma <= self.max_std:
            return self.quiet(ctx)
        return self.result(True, np.ones(ctx.t_new.size, bool), sigma, f"noise sigma <= {self.max_std:g} {ctx.ch.unit}",
                           f"Estimated noise sigma {sigma:.4g} {ctx.ch.unit} exceeds the limit.")


class Outlier(Rule):
    """Hampel test: distance from the window median in robust sigmas."""
    flag = OUTLIER

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "outlier")
        self.window_s = float(spec["windowS"])
        self.k = float(opt(spec, "k", 5))
        self.min_scale = float(opt(spec, "minScale", 0))
        self.recommendation = "Single-sample disturbance. Check for interference if it recurs."
        self.apply_overrides()

    def evaluate(self, ctx):
        v = ctx.buffer.window(self.window_s, ctx.t_now)[1]
        if v.size < 8 or ctx.v_new.size == 0:
            return self.quiet(ctx)
        center = float(np.nanmedian(v))
        scale = max(1.4826 * float(np.nanmedian(np.abs(v - center))), self.min_scale)
        mask = np.abs(ctx.v_new - center) > self.k * scale
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        bad = float(ctx.v_new[mask][-1])
        return self.result(True, mask, bad, f"{center:g} +/- {self.k * scale:g} {ctx.ch.unit}",
                           f"Value {bad:g} is a statistical outlier against the recent window.")


class Drift(Rule):
    """Sustained slope over a long window. Best applied to signals expected to
    hold steady, since a genuine warm-up looks the same as drift."""
    flag = DRIFT

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "drift")
        self.window_s = float(spec["windowS"])
        self.max_slope = float(spec["maxSlope"])
        self.next_eval_t = -math.inf
        self.last_active = False
        self.last_slope = math.nan
        self.recommendation = "Compare against a reference sensor and check the calibration date."
        self.apply_overrides()

    def evaluate(self, ctx):
        if ctx.t_now >= self.next_eval_t:
            self.next_eval_t = ctx.t_now + 1.0
            t, v = ctx.buffer.window(self.window_s, ctx.t_now)[:2]
            ok = np.isfinite(v)
            self.last_active = False
            if ok.sum() >= 10 and t[-1] - t[0] >= 0.8 * self.window_s:
                self.last_slope = float(np.polyfit(t[ok] - t[0], v[ok], 1)[0])
                self.last_active = abs(self.last_slope) > self.max_slope
        if not self.last_active:
            return self.quiet(ctx)
        return self.result(True, np.ones(ctx.t_new.size, bool), self.last_slope,
                           f"|slope| <= {self.max_slope:g} {ctx.ch.unit}/s",
                           f"Sustained drift of {self.last_slope:.4g} {ctx.ch.unit}/s over {self.window_s:g} s.")


class Interval(Rule):
    """Abnormal sampling intervals against the channel's nominal rate."""
    flag = INTERVAL

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "interval")
        self.window_s = float(spec["windowS"])
        self.tolerance = float(opt(spec, "tolerance", 0.5))
        self.max_fraction = float(opt(spec, "maxFraction", 0.2))
        self.recommendation = "Check bus load, device sample clock and host performance."
        self.apply_overrides()

    def evaluate(self, ctx):
        t = ctx.buffer.window(self.window_s, ctx.t_now)[0]
        if t.size < 8 or not math.isfinite(ctx.ch.sample_rate_hz):
            return self.quiet(ctx)
        nominal = 1.0 / ctx.ch.sample_rate_hz
        dt = np.diff(t)
        fraction = float(np.mean(np.abs(dt - nominal) > self.tolerance * nominal))
        if fraction <= self.max_fraction:
            return self.quiet(ctx)
        return self.result(True, np.ones(ctx.t_new.size, bool), float(np.median(dt)),
                           f"{nominal:g} s +/- {self.tolerance * 100:g}%", f"{fraction * 100:.0f}% of sample intervals are abnormal.")


class Timestamp(Rule):
    """Non-increasing timestamps, or timestamps from the future."""
    flag = TIMESTAMP

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "timestamp")
        self.max_future_s = float(opt(spec, "maxFutureS", 2))
        self.recommendation = "Check device clock synchronisation and the driver's timestamp source."
        self.apply_overrides()

    def evaluate(self, ctx):
        mask = _diff_against_previous(ctx.prev_t, ctx.t_new) <= 0
        if mask.size < ctx.t_new.size:      # first sample ever has no predecessor
            mask = np.concatenate(([False], mask))
        mask = mask | (ctx.t_new > ctx.t_now + self.max_future_s)
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        return self.result(True, mask, math.nan, "strictly increasing, not in the future",
                           f"{int(mask.sum())} sample(s) with an inconsistent timestamp.")


class Missing(Rule):
    """No samples within the timeout. When the device link is down the cause is
    communication, not the measurement, so the classification follows."""
    flag = MISSING
    time_driven = True

    def __init__(self, ch, spec):
        critical = bool(opt(spec, "critical", False))
        super().__init__(ch, spec, "commloss" if critical else "missing")
        fs = ch.sample_rate_hz if math.isfinite(ch.sample_rate_hz) else 1.0
        if critical:
            self.classification = alarms.COMMUNICATION_FAULT
            self.severity = "critical"
            self.timeout_s = float(opt(spec, "timeoutS", max(2.0, 20.0 / fs)))
            self.recommendation = ("A critical sensor is not reporting. Monitoring of this quantity is lost: "
                                   "follow the rig's procedure before continuing.")
        else:
            self.timeout_s = float(opt(spec, "timeoutS", max(1.0, 10.0 / fs)))
            self.recommendation = "Check the sensor connection and the device channel configuration."
        self.apply_overrides()

    def evaluate(self, ctx):
        reference = ctx.start_t if math.isnan(ctx.last_t) else max(ctx.last_t, ctx.start_t)
        age = ctx.t_now - reference
        if ctx.t_new.size or age <= self.timeout_s:
            return self.quiet(ctx)
        result = self.result(True, np.zeros(0, bool), math.nan, f"new sample within {self.timeout_s:g} s",
                             f"No data for {age:.1f} s (device: {ctx.device_state}).")
        if ctx.device_state != "online":
            result.classification = alarms.COMMUNICATION_FAULT
        return result


class CalibrationCheck(Rule):
    """Calibration overdue, or raw signal outside the calibrated span."""
    flag = CALIBRATION

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "calibration")
        due = ch.calibration.due_on
        self.due_t = datetime.fromisoformat(due).replace(tzinfo=timezone.utc).timestamp() if due else math.inf
        self.recommendation = "Recalibrate, or confirm that the calibration certificate covers this operating range."
        self.apply_overrides()

    def evaluate(self, ctx):
        cal = ctx.ch.calibration
        mask = (ctx.raw_new < cal.raw_min) | (ctx.raw_new > cal.raw_max)
        if ctx.t_now > self.due_t:
            return self.result(True, np.ones(ctx.t_new.size, bool), math.nan, f"due {cal.due_on}", f"Calibration is overdue (due {cal.due_on}).")
        if not mask.any():
            return self.result(False, mask, math.nan, "", "")
        bad = float(ctx.raw_new[mask][-1])
        return self.result(True, mask, bad, f"raw {cal.raw_min:g} to {cal.raw_max:g}", f"Raw signal {bad:g} is outside the calibrated span.")


class FaultSignal(Rule):
    """A dedicated hardware fault or interlock line is asserted. The only
    channel-level path to a confirmed equipment fault, because the equipment
    itself is reporting it."""

    def __init__(self, ch, spec):
        super().__init__(ch, spec, "signal")
        if ch.role == "interlock":
            self.classification = alarms.CRITICAL_SAFETY_ALARM
            self.recommendation = "Interlock active. Establish why through the rig's safety system before any restart."
        else:
            self.classification = alarms.CONFIRMED_EQUIPMENT_FAULT
            self.recommendation = "Equipment is reporting a fault. Follow the rig's fault procedure and inspect the named equipment."
        self.severity = "critical"
        self.apply_overrides()

    def evaluate(self, ctx):
        if (ctx.v_new == ctx.ch.active_value).any():
            return self.result(True, np.zeros(ctx.t_new.size, bool), ctx.ch.active_value, f"not {ctx.ch.active_value:g}",
                               f"{ctx.ch.name} is asserted.")
        return self.quiet(ctx)


RULE_TYPES = {"range": Range, "threshold": Threshold, "roc": Roc, "stuck": Stuck, "noise": Noise, "outlier": Outlier,
              "drift": Drift, "interval": Interval, "timestamp": Timestamp, "missing": Missing,
              "calibrationCheck": CalibrationCheck, "faultSignal": FaultSignal}


def build_rule(ch, spec) -> Rule:
    if spec.get("type") not in RULE_TYPES:
        raise ValueError(f'Unknown validation rule type "{spec.get("type")}" on channel {ch.id}.')
    return RULE_TYPES[spec["type"]](ch, spec)


def rules_for_channel(ch) -> list[Rule]:
    """Rules implied by a channel's limits and role, plus the configured ones."""
    rules: list[Rule] = []
    if not (math.isnan(ch.range_min) and math.isnan(ch.range_max)):
        rules.append(Range(ch, {}))
    if not (math.isnan(ch.warn_low) and math.isnan(ch.warn_high)):
        rules.append(Threshold(ch, {"level": "warning"}))
    if not (math.isnan(ch.crit_low) and math.isnan(ch.crit_high)):
        rules.append(Threshold(ch, {"level": "critical"}))
    if ch.role in ("fault_signal", "interlock"):
        rules.append(FaultSignal(ch, {}))
    if ch.kind == "hardware":
        rules += [Timestamp(ch, {}), Missing(ch, {})]
        if ch.critical:
            rules.append(Missing(ch, {"critical": True}))
    if ch.calibration.type != "none":
        rules.append(CalibrationCheck(ch, {}))
    rules += [build_rule(ch, spec) for spec in ch.rules]
    return rules


class CrossRule:
    """Plausibility check across channels: fires when the expression is true.
    May only claim a confirmed equipment fault if it references a dedicated
    fault signal; measurements alone cannot confirm a failure."""

    def __init__(self, spec, channels):
        self.id = spec["id"]
        self.key = f"cross:{self.id}"
        self.expression = Expression(spec["expression"])
        self.description = opt(spec, "description", spec["expression"])
        self.classification = opt(spec, "classification", alarms.MEASUREMENT_ANOMALY)
        self.severity = opt(spec, "severity", "warning")
        self.persistence_s = opt(spec, "persistenceS", 1)
        self.clear_delay_s = opt(spec, "clearDelayS", 2)
        self.recommendation = opt(spec, "recommendation", "These readings cannot all be right. Check each sensor before trusting any of them.")
        self.equipment = opt(spec, "equipment", "")
        for name in self.expression.inputs:
            if not channels.has(name):
                raise ValueError(f'Cross rule {self.id} references unknown channel "{name}".')
        if self.classification == alarms.CONFIRMED_EQUIPMENT_FAULT \
                and not any(channels.get(name).role != "measurement" for name in self.expression.inputs):
            raise UnverifiedFault(f"Cross rule {self.id} claims a confirmed equipment fault but references no fault_signal or interlock channel.")

    def spec(self) -> dict:
        return {"id": self.id, "expression": self.expression.source, "description": self.description,
                "classification": self.classification, "severity": self.severity, "persistenceS": self.persistence_s,
                "clearDelayS": self.clear_delay_s, "recommendation": self.recommendation, "equipment": self.equipment}
