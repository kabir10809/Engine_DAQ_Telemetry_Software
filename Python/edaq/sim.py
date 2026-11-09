"""Simulated rig: signal model, demo setup and the fault scenario table.

One scenario table drives both the Simulation page and the test suite, so what
the operator rehearses is exactly what has been verified.
"""
from __future__ import annotations

import math

import numpy as np

from . import SETUP_SCHEMA
from . import alarms as A

# id, unit, sample rate (Hz), noise sigma
SIGNALS = (("rpm", "rpm", 50, 2.0), ("torque", "N.m", 50, 0.3), ("coolant_in", "degC", 10, 0.05),
           ("coolant_out", "degC", 10, 0.05), ("oil_temp", "degC", 10, 0.05), ("oil_press_v", "V", 10, 0.002),
           ("exhaust_temp", "degC", 10, 1.0), ("intake_press", "kPa", 10, 0.05), ("intake_temp", "degC", 10, 0.05),
           ("air_flow", "kg/h", 10, 0.2), ("fuel_flow", "kg/h", 10, 0.01), ("vibration", "g", 50, 0.02),
           ("cr_pos_mm", "mm", 2, 0.002), ("dyno_fault", "state", 5, 0.0), ("pump_fail", "state", 5, 0.0), ("estop", "state", 5, 0.0))


def nominal(signal: str, tau):
    """Noise-free signal tau seconds after start. Closed form, so runs are
    reproducible. Not an engine simulation: its job is plausible signals."""
    tau = np.asarray(tau, float)
    wave = np.sin(2 * np.pi * tau / 120)
    table = {"rpm": lambda: 2000 + 800 * wave, "torque": lambda: 160 * (0.5 + 0.2 * wave),
             "coolant_in": lambda: 78 + 0.5 * np.sin(tau / 80), "coolant_out": lambda: 86 + 1.0 * np.sin(tau / 80),
             "oil_temp": lambda: 95 + 2.0 * np.sin(tau / 100), "oil_press_v": lambda: 2.1 + 0.2 * wave,
             "exhaust_temp": lambda: 520 + 90 * wave, "intake_press": lambda: 99.5 + 0.2 * np.sin(tau / 60),
             "intake_temp": lambda: 28 + 0.4 * np.sin(tau / 90), "air_flow": lambda: 110 + 45 * wave,
             "fuel_flow": lambda: 7.5 + 3.5 * wave, "vibration": lambda: 0.8 + 0.2 * wave,
             "cr_pos_mm": lambda: 5.0 * np.ones_like(tau)}
    return table.get(signal, lambda: np.zeros_like(tau))()


def _rule(kind, **parameters):
    return {"type": kind, **parameters}


def _hardware(cid, name, sensor, quantity, unit, fs, lo, hi, **extra):
    return {"id": cid, "name": name, "kind": "hardware", "deviceId": "rig_sim", "hwChannel": cid, "sensorType": sensor,
            "quantity": quantity, "unit": unit, "sampleRateHz": fs, "rangeMin": lo, "rangeMax": hi, **extra}


def _signal(cid, name, role, equipment):
    return {"id": cid, "name": name, "kind": "hardware", "role": role, "deviceId": "rig_sim", "hwChannel": cid,
            "sensorType": "Digital input", "quantity": "State", "unit": "state", "sampleRateHz": 5, "equipment": equipment}


def demo_setup() -> dict:
    """Complete setup for the simulated rig. Ordinary configuration throughout."""
    temp, press, flow = "Temperature", "Pressure", "Mass flow"
    channels = [
        _hardware("rpm", "Engine speed", "Encoder", "Rotational speed", "rpm", 50, 0, 8000, equipment="Engine", warnHigh=4500,
                  critHigh=6000, critical=True, rules=[_rule("roc", maxRate=3000), _rule("stuck", windowS=5, tolerance=0.001)]),
        _hardware("torque", "Brake torque", "Load cell", "Torque", "N.m", 50, -50, 500, equipment="Dynamometer", warnHigh=300,
                  critHigh=450, rules=[_rule("roc", maxRate=400)]),
        _hardware("coolant_in", "Coolant inlet", "PT100", temp, "degC", 10, -40, 150, equipment="Cooling system"),
        _hardware("coolant_out", "Coolant outlet", "PT100", temp, "degC", 10, -40, 150, equipment="Cooling system", warnHigh=100,
                  critHigh=112, rules=[_rule("noise", windowS=3, maxStd=0.8), _rule("stuck", windowS=15, tolerance=0.0001)]),
        _hardware("oil_temp", "Oil temperature", "PT100", temp, "degC", 10, -40, 200, equipment="Engine", warnHigh=120, critHigh=135,
                  rules=[_rule("roc", maxRate=40), _rule("outlier", windowS=5, k=8, minScale=0.3), _rule("drift", windowS=40, maxSlope=0.08)]),
        _hardware("oil_press", "Oil pressure", "Pressure transducer", press, "bar", 10, 0, 10, equipment="Engine", warnLow=1.0, critLow=0.5,
                  hwChannel="oil_press_v", rawUnit="V", rules=[_rule("stuck", windowS=8, tolerance=0.0001)],
                  calibration={"type": "linear", "coefficients": [2.5, -1.25], "rawMin": 0.5, "rawMax": 4.5, "certificate": "SIM-CAL-001",
                               "calibratedOn": "2026-01-15", "dueOn": "2099-01-15", "calibratedBy": "Simulator"}),
        _hardware("exhaust_temp", "Exhaust temperature", "K-type TC", temp, "degC", 10, 0, 1200, equipment="Engine", warnHigh=800, critHigh=950),
        _hardware("intake_press", "Intake pressure", "Pressure transducer", press, "kPa", 10, 50, 300, equipment="Engine"),
        _hardware("intake_temp", "Intake temperature", "PT100", temp, "degC", 10, -40, 100, equipment="Engine", rules=[_rule("interval", windowS=4)]),
        _hardware("air_flow", "Air mass flow", "MAF", flow, "kg/h", 10, 0, 1000, equipment="Engine"),
        _hardware("fuel_flow", "Fuel mass flow", "Coriolis", flow, "kg/h", 10, 0, 100, equipment="Fuel system"),
        _hardware("vibration", "Block vibration", "Accelerometer", "Acceleration", "g", 50, -50, 50, equipment="Engine", warnHigh=5, critHigh=10),
        _hardware("compression_ratio", "Compression ratio", "LVDT", "Dimensionless", "ratio", 2, 6, 20, equipment="Engine", hwChannel="cr_pos_mm",
                  rawUnit="mm", calibration={"type": "linear", "coefficients": [1.2, 4.5]}),
        _signal("dyno_fault", "Dynamometer fault", "fault_signal", "Dynamometer"),
        _signal("pump_fail", "Coolant pump fail", "fault_signal", "Cooling system"),
        _signal("estop", "E-stop interlock", "interlock", "Test cell"),
        {"id": "brake_power", "name": "Brake power", "kind": "derived", "quantity": "Power", "unit": "kW",
         "expression": "2*pi*rpm*torque/60000", "equipment": "Engine", "rangeMin": -100, "rangeMax": 500},
        {"id": "bsfc", "name": "BSFC", "kind": "derived", "quantity": "Specific fuel consumption", "unit": "g/kWh",
         "expression": "if(brake_power>1, fuel_flow*1000/brake_power, 0)", "equipment": "Engine"},
        {"id": "brake_efficiency", "name": "Brake thermal efficiency", "kind": "derived", "quantity": "Dimensionless", "unit": "%",
         "expression": "if(fuel_flow>0.1, 100*brake_power/(fuel_flow/3600*lhv_mj_kg*1000), 0)", "equipment": "Engine"},
    ]
    return {"schemaVersion": SETUP_SCHEMA, "name": "Simulated engine rig",
            "devices": [{"id": "rig_sim", "name": "Simulated DAQ", "driver": "simulator", "settings": {"seed": 1},
                         "autoReconnect": True, "silenceTimeoutS": 10}],
            "parameters": {"lhv_mj_kg": 42.5},
            "engine": {"geometry": {"boreMm": 86, "strokeMm": 86, "conRodMm": 143, "cylinders": 4, "strokesPerCycle": 4},
                       "compressionRatio": {"mode": "measured", "nominal": 10.5, "min": 8, "max": 16, "channelId": "compression_ratio"},
                       "map": {"crChannel": "compression_ratio", "rpm": "rpm", "torque": "torque", "power": "brake_power"}},
            "channels": channels,
            "crossRules": [
                {"id": "torque_without_speed", "expression": "rpm < 50 && torque > 20", "description": "Brake torque reported while the engine is not turning"},
                {"id": "coolant_reversed", "expression": "coolant_in > coolant_out + 15", "description": "Coolant inlet far hotter than outlet"},
                {"id": "pump_failure_confirmed", "expression": "pump_fail == 1 && coolant_out > 95",
                 "description": "Coolant pump reports failure and coolant temperature is rising",
                 "classification": A.CONFIRMED_EQUIPMENT_FAULT, "severity": "critical", "equipment": "Cooling system", "persistenceS": 0.5,
                 "recommendation": "Cooling system failure confirmed by the pump signal and coolant temperature. Follow the rig's shutdown procedure."}],
            }


def _f(kind, channel, duration, **extra):
    return {"kind": kind, "channel": channel, "durationS": duration, **extra}


def _d(kind, duration):
    return {"kind": kind, "durationS": duration}


_M, _C, _I, _E, _S = A.MEASUREMENT_ANOMALY, A.COMMUNICATION_FAULT, A.INSTRUMENT_FAULT, A.CONFIRMED_EQUIPMENT_FAULT, A.CRITICAL_SAFETY_ALARM

# id, title, faults, run seconds, expected colour, expected alarm keys, expected classes
_TABLE = [
    ("normal", "Normal operation", [], 30, "green", [], []),
    ("spike", "Single-sample spike", [_f("spike", "oil_temp", 1, magnitude=30)], 8, "yellow", ["chan:oil_temp:roc", "chan:oil_temp:outlier"], [_M]),
    ("spike_past_critical", "Isolated reading beyond critical limit", [_f("spike", "oil_temp", 1, magnitude=60)], 8, "yellow", ["chan:oil_temp:roc"], [_M]),
    ("frozen", "Frozen sensor", [_f("frozen", "coolant_out", 25)], 25, "yellow", ["chan:coolant_out:stuck"], [_M]),
    ("noisy", "Excessive noise", [_f("noise", "coolant_out", 10, magnitude=3)], 10, "yellow", ["chan:coolant_out:noise"], [_M]),
    ("under_range", "Below sensor range (open circuit)", [_f("set", "exhaust_temp", 4, value=-50)], 5, "yellow", ["chan:exhaust_temp:range"], [_M]),
    ("drift", "Sensor drift", [_f("drift", "oil_temp", 50, rate=0.3)], 50, "yellow", ["chan:oil_temp:drift"], [_M]),
    ("dropout", "Non-critical channel stops", [_f("dropout", "intake_temp", 6)], 6, "yellow", ["chan:intake_temp:missing"], [_M]),
    ("jitter", "Abnormal sampling intervals", [_f("jitter", "intake_temp", 10)], 10, "yellow", ["chan:intake_temp:interval"], [_M]),
    ("implausible", "Torque while not turning", [_f("set", "rpm", 5, value=0)], 5, "yellow", ["cross:torque_without_speed"], [_M]),
    ("cal_span", "Raw signal outside calibrated span", [_f("set", "oil_press_v", 4, value=4.7)], 5, "yellow", ["chan:oil_press:calibration"], [_M]),
    ("time_back", "Timestamp runs backwards", [_f("timeBack", "intake_press", 1)], 4, "yellow", ["chan:intake_press:timestamp"], [_M]),
    ("packets", "Communication packet errors", [_d("packetErrors", 8)], 8, "yellow", ["dev:rig_sim:packets"], [_C]),
    ("comm_blip", "Short communication dropout", [_d("commLoss", 0.8)], 6, "yellow", ["dev:rig_sim:link"], [_C]),
    ("instrument_degraded", "Instrument degraded", [_d("instrumentDegraded", 4)], 5, "yellow", ["dev:rig_sim:instrument_degraded"], [_I]),
    ("comm_loss", "Sustained loss of communication", [_d("commLoss", 20)], 12, "red", ["dev:rig_sim:link", "chan:rpm:commloss"], [_C]),
    ("critical_sensor_lost", "Critical sensor stops reporting", [_f("dropout", "rpm", 8)], 8, "red", ["chan:rpm:commloss"], [_C]),
    ("daq_failure", "DAQ hardware failure", [_d("instrumentFault", 4)], 5, "red", ["dev:rig_sim:instrument_fault"], [_I]),
    ("overload", "Hardware-reported overload", [_d("overload", 4)], 5, "red", ["dev:rig_sim:overload"], [_I]),
    ("dyno_fault", "Dynamometer fault signal", [_f("set", "dyno_fault", 4, value=1)], 5, "red", ["chan:dyno_fault:signal"], [_E]),
    ("cooling_failure", "Confirmed cooling system failure", [_f("set", "pump_fail", 15, value=1), _f("set", "coolant_out", 15, value=104)], 12, "red",
     ["chan:pump_fail:signal", "cross:pump_failure_confirmed"], [_E]),
    ("over_temp", "Critical temperature exceeded", [_f("set", "oil_temp", 6, value=140)], 6, "red", ["chan:oil_temp:threshold_critical"], [_S]),
    ("low_oil_press", "Critical pressure limit", [_f("set", "oil_press_v", 5, value=0.6)], 5, "red", ["chan:oil_press:threshold_critical"], [_S]),
    ("interlock", "Equipment interlock activated", [_f("set", "estop", 4, value=1)], 5, "red", ["chan:estop:signal"], [_S]),
]
SCENARIOS = [dict(zip(("id", "title", "faults", "runS", "color", "keys", "classes"), row)) for row in _TABLE]


def find_scenario(scenario_id) -> dict:
    for scenario in SCENARIOS:
        if scenario["id"] == scenario_id:
            return scenario
    raise KeyError(f'Unknown scenario "{scenario_id}".')


def inject(engine, scenario, device_id, t0) -> None:
    """Apply a scenario's faults, starting at t0, to a simulator device."""
    driver = engine.devices.get(device_id).driver
    for fault in scenario["faults"]:
        driver.inject({**fault, "t0": t0})
