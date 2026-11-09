"""The five fault classes and the lifecycle of every alarm condition.

Colour is computed in color_of and only there, so a measurement anomaly can
never turn red and a confirmed fault can never display as yellow.

States: normal, pending, active_unack, active_ack, cleared_unack,
cleared_latched. There is deliberately no shelve or disable operation. A
condition too brief to pass its persistence period is still counted and logged
as a transient, so nothing is silently dropped.
"""
from __future__ import annotations

import math

from .util import iso_time, opt

MEASUREMENT_ANOMALY = "measurement_anomaly"
COMMUNICATION_FAULT = "communication_fault"
INSTRUMENT_FAULT = "instrument_fault"
CONFIRMED_EQUIPMENT_FAULT = "confirmed_equipment_fault"
CRITICAL_SAFETY_ALARM = "critical_safety_alarm"
CLASSES = (MEASUREMENT_ANOMALY, COMMUNICATION_FAULT, INSTRUMENT_FAULT, CONFIRMED_EQUIPMENT_FAULT, CRITICAL_SAFETY_ALARM)
LABELS = dict(zip(CLASSES, ("Measurement anomaly", "Communication fault", "Instrument fault",
                            "Confirmed equipment fault", "Critical safety alarm")))
_MAX_HISTORY = 50000
_TRANSIENT_ROLLUP_S = 10


def color_of(classification: str, severity: str) -> str:
    if classification == MEASUREMENT_ANOMALY:
        return "yellow"
    if classification in (COMMUNICATION_FAULT, INSTRUMENT_FAULT):
        return "red" if severity == "critical" else "yellow"
    if classification in (CONFIRMED_EQUIPMENT_FAULT, CRITICAL_SAFETY_ALARM):
        return "red"
    raise ValueError(f'Unknown classification "{classification}".')


class AlarmManager:
    def __init__(self):
        self.conditions: dict[str, dict] = {}
        self.history: list[dict] = []
        self.history_dropped = 0
        self.event_sink = None          # called with each event
        self.supporting_fn = None       # describes supporting measurements

    def update(self, key, is_true, info, t) -> None:
        """Feed the current truth of a condition into its state machine."""
        if key not in self.conditions:
            if not is_true:
                return
            self.conditions[key] = self._new_condition(key, info)
        c = self.conditions[key]
        if is_true:
            self._refresh(c, info)
            c["lastTrueT"] = t
        self._step(c, is_true, t)

    def acknowledge(self, key, who, t) -> None:
        c = self.conditions[key]
        if c["state"] == "active_unack":
            c["state"] = "active_ack"
        elif c["state"] == "cleared_unack":
            c["state"] = "cleared_latched" if c["latched"] else "normal"
        else:
            return
        c["ackBy"], c["ackT"] = who, t
        self._record(c, "acknowledged", t, f"by {who}")

    def acknowledge_all(self, who, t) -> None:
        for key in list(self.conditions):
            self.acknowledge(key, who, t)

    def reset(self, key, who, t) -> bool:
        """Release a latched alarm. Refused while the condition holds."""
        c = self.conditions[key]
        if c["state"] != "cleared_latched":
            return False
        c["state"] = "normal"
        self._record(c, "reset", t, f"by {who}")
        return True

    def current(self) -> list[dict]:
        """Conditions that must be visible, red first then newest."""
        visible = [c for c in self.conditions.values() if c["state"] not in ("normal", "pending")]
        return sorted(visible, key=lambda c: (c["color"] != "red", -c["activatedT"]))

    def is_visible(self, key) -> bool:
        return key in self.conditions and self.conditions[key]["state"] not in ("normal", "pending")

    def overall(self) -> str:
        visible = self.current()
        if not visible:
            return "green"
        return "red" if any(c["color"] == "red" for c in visible) else "yellow"

    def flush(self, t) -> None:
        """Write out any transient counts still waiting for a rollup."""
        for c in self.conditions.values():
            if c["transientPending"] > 0:
                self._rollup(c, t)

    @staticmethod
    def _new_condition(key, info) -> dict:
        color = color_of(info["classification"], info["severity"])
        return {"key": key, "source": opt(info, "source", ""), "channel": opt(info, "channel", ""),
                "equipment": opt(info, "equipment", ""), "classification": info["classification"],
                "severity": info["severity"], "color": color, "state": "normal",
                "persistenceS": opt(info, "persistenceS", 0), "clearDelayS": opt(info, "clearDelayS", 0),
                "latched": bool(info.get("latched", color == "red")), "value": math.nan, "expected": "", "reason": "",
                "recommendation": "", "supporting": "", "onsetT": math.nan, "lastTrueT": math.nan, "activatedT": math.nan,
                "ackT": math.nan, "ackBy": "", "occurrences": 0, "transientPending": 0, "lastRollupT": -math.inf}

    @staticmethod
    def _refresh(c, info) -> None:
        # A running condition tracks the latest evidence, e.g. a missing-data
        # alarm learning the device went down.
        c["classification"], c["severity"] = info["classification"], info["severity"]
        c["color"] = color_of(c["classification"], c["severity"])
        c["value"] = info.get("value", math.nan)
        c["expected"] = opt(info, "expected", "")
        c["reason"] = opt(info, "reason", "")
        c["recommendation"] = opt(info, "recommendation", "")

    def _step(self, c, is_true, t) -> None:
        state = c["state"]
        if state == "normal":
            if is_true:
                c["onsetT"], c["state"] = t, "pending"
                self._try_activate(c, t)
        elif state == "pending":
            if is_true:
                self._try_activate(c, t)
            else:
                c["state"] = "normal"
                c["transientPending"] += 1
                if t - c["lastRollupT"] >= _TRANSIENT_ROLLUP_S:
                    self._rollup(c, t)
        elif state in ("active_unack", "active_ack"):
            if not is_true and t - c["lastTrueT"] >= c["clearDelayS"]:
                if state == "active_unack":
                    c["state"] = "cleared_unack"
                else:
                    c["state"] = "cleared_latched" if c["latched"] else "normal"
                c["onsetT"] = math.nan
                self._record(c, "cleared", t, "Condition no longer true.")
        else:
            if is_true:
                if math.isnan(c["onsetT"]):
                    c["onsetT"] = t
                self._try_activate(c, t)
            else:
                c["onsetT"] = math.nan

    def _try_activate(self, c, t) -> None:
        if t - c["onsetT"] >= c["persistenceS"]:
            c["state"], c["activatedT"] = "active_unack", t
            c["occurrences"] += 1
            c["ackBy"], c["ackT"] = "", math.nan
            if self.supporting_fn:
                c["supporting"] = self.supporting_fn(c)
            self._record(c, "activated", t, c["reason"])

    def _rollup(self, c, t) -> None:
        self._record(c, "transient", t, f'{c["transientPending"]} occurrence(s) shorter than the '
                                        f'{c["persistenceS"]:.2f} s persistence period. Last: {c["reason"]}')
        c["transientPending"] = 0
        c["lastRollupT"] = t

    def _record(self, c, event, t, detail) -> None:
        e = {"t": t, "time": iso_time(t), "event": event, "key": c["key"], "classification": c["classification"],
             "color": c["color"], "severity": c["severity"], "channel": c["channel"], "equipment": c["equipment"],
             "source": c["source"], "value": float(c["value"]), "expected": c["expected"], "detail": detail,
             "recommendation": c["recommendation"], "supporting": c["supporting"]}
        self.history.append(e)
        if len(self.history) > _MAX_HISTORY:
            del self.history[0]
            self.history_dropped += 1       # the event sink log keeps everything
        if self.event_sink:
            self.event_sink(e)
