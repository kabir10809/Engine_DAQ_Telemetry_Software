"""Every scenario in the catalog must produce exactly its expected colour,
alarm keys and classes. A deployment gate."""
import math

import numpy as np
import pytest

from edaq import alarms, engine as E, sim, validation
from edaq.channels.channel import Channel
from edaq.channels.units import UnitRegistry
from edaq.clock import Clock
from edaq.hw.driver import empty_status

WARM_UP_S, TICK_S, DEVICE = 45, 0.1, "rig_sim"


def make_engine(setup=None):
    engine = E.Engine(clock=Clock(1.8e9), units=UnitRegistry())
    engine.load_setup(setup or sim.demo_setup())
    engine.start()
    return engine


def run(engine, seconds):
    for _ in range(round(seconds / TICK_S)):
        engine.clock.advance(TICK_S)
        engine.step()


def describe(engine):
    return ", ".join(f'{c["key"]}({c["state"]})' for c in engine.alarms.current())


@pytest.mark.parametrize("scenario_id", [s["id"] for s in sim.SCENARIOS])
def test_scenario_classifies_correctly(scenario_id):
    scenario = sim.find_scenario(scenario_id)
    engine = make_engine()
    run(engine, WARM_UP_S)
    assert engine.overall_status() == "green", describe(engine)
    sim.inject(engine, scenario, DEVICE, engine.clock.now())
    run(engine, scenario["runS"])
    assert engine.overall_status() == scenario["color"], describe(engine)
    for key in scenario["keys"]:
        assert engine.alarms.is_visible(key), f"Expected {key}: {describe(engine)}"
    for kind in scenario["classes"]:
        assert any(c["classification"] == kind for c in engine.alarms.current()), describe(engine)
    if scenario["color"] == "yellow":
        assert not [c for c in engine.alarms.current() if c["color"] == "red"], describe(engine)


def test_isolated_spike_is_logged_as_transient_not_critical():
    engine = make_engine()
    run(engine, WARM_UP_S)
    sim.inject(engine, sim.find_scenario("spike_past_critical"), DEVICE, engine.clock.now())
    run(engine, 8)
    engine.alarms.flush(engine.clock.now())
    key = "chan:oil_temp:threshold_critical"
    assert not engine.alarms.is_visible(key)
    assert [e for e in engine.alarms.history if e["key"] == key][0]["event"] == "transient"


def test_red_stays_visible_until_acknowledged_and_reset():
    engine = make_engine()
    key = "chan:dyno_fault:signal"
    run(engine, 5)
    sim.inject(engine, sim.find_scenario("dyno_fault"), DEVICE, engine.clock.now())
    run(engine, 2)
    assert engine.alarms.conditions[key]["state"] == "active_unack"
    assert not engine.alarms.reset(key, "op", engine.clock.now())
    engine.alarms.acknowledge(key, "op", engine.clock.now())
    assert engine.alarms.conditions[key]["state"] == "active_ack"
    assert engine.overall_status() == "red"
    run(engine, 6)
    assert engine.alarms.conditions[key]["state"] == "cleared_latched"
    assert engine.overall_status() == "red"
    assert engine.alarms.reset(key, "op", engine.clock.now())
    assert engine.alarms.conditions[key]["state"] == "normal"


def test_raw_data_is_preserved_and_flagged():
    engine = make_engine()
    run(engine, 10)
    sim.inject(engine, sim.find_scenario("spike"), DEVICE, engine.clock.now())
    run(engine, 2)
    _, v, flags = engine.history("oil_temp", 3)
    k = int(np.argmax(v))
    assert v[k] > 115
    assert int(flags[k]) & validation.ROC


def test_default_status_makes_no_health_claim():
    assert empty_status()["instrument"] == "not_assessed"


def test_no_fault_signals_means_equipment_not_monitored():
    setup = sim.demo_setup()
    setup["channels"] = [c for c in setup["channels"] if c["id"] not in ("dyno_fault", "pump_fail", "estop")]
    setup["crossRules"] = setup["crossRules"][:2]
    engine = make_engine(setup)
    run(engine, 2)
    assert E.equipment_health(engine)["state"] == "not_monitored"


def test_idle_is_never_green():
    engine = make_engine()
    engine.stop()
    assert engine.overall_status() == "no_data"


def test_measurement_rule_cannot_claim_equipment_fault():
    channel = Channel.from_dict({"id": "t1", "kind": "manual"})
    with pytest.raises(validation.UnverifiedFault):
        validation.build_rule(channel, {"type": "roc", "maxRate": 1, "classification": alarms.CONFIRMED_EQUIPMENT_FAULT})


def test_cross_rule_without_signal_cannot_claim_equipment_fault():
    engine = make_engine()
    with pytest.raises(validation.UnverifiedFault):
        validation.CrossRule({"id": "bad", "expression": "oil_temp > 100", "classification": alarms.CONFIRMED_EQUIPMENT_FAULT}, engine.channels)


def test_anomaly_colour_is_always_yellow():
    assert alarms.color_of(alarms.MEASUREMENT_ANOMALY, "critical") == "yellow"
    assert alarms.color_of(alarms.CONFIRMED_EQUIPMENT_FAULT, "warning") == "red"
