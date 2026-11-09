"""Plugin framework, compatibility matrix, core maths, data handling."""
import math
import shutil

import numpy as np
import pandas as pd
import pytest

from conftest import tick
from edaq import codefile, engine as E, experiment, paths, rig, sim
from edaq.channels.channel import Calibration, Channel, ChannelManager, ConfigError
from edaq.channels.expression import Expression, ExpressionError
from edaq.channels.units import UnitRegistry
from edaq.clock import Clock
from edaq.data import exchange
from edaq.data.store import RunReader
from edaq.hw import codecs
from edaq.hw.driver import availability, make_candidate
from edaq.hw.matrix import CompatibilityMatrix
from edaq.hw.registry import DriverRegistry
from edaq.ui import forms


def test_matrix_agrees_with_installed_drivers():
    registry = DriverRegistry()
    assert not registry.problems
    assert not CompatibilityMatrix().validate(registry)


def test_matrix_document_is_current(folder):
    scratch = folder / "matrix.md"
    CompatibilityMatrix().write_markdown(DriverRegistry(), scratch)
    assert scratch.read_text() == (paths.DOCS / "HARDWARE_COMPATIBILITY.md").read_text(), "Run tools/update_matrix_doc.py."


def matrix_copy(folder):
    path = folder / "matrix.py"
    shutil.copy(paths.CONFIG / "hardware_compatibility.py", path)
    return CompatibilityMatrix(path)


def test_bench_validated_needs_evidence(folder):
    registry, matrix = DriverRegistry(), matrix_copy(folder)
    row = {"id": "dmm1", "manufacturer": "Acme", "model": "M1", "interface": "RS-232", "driver": "stream", "status": "implemented"}
    matrix.add_device(row)
    with pytest.raises(ValueError):
        matrix.add_device({**row, "id": "dmm2", "status": "bench_validated"})
    with pytest.raises(ValueError):
        matrix.record_validation("dmm1", {"date": "2026-11-09"})
    matrix.record_validation("dmm1", {"date": "2026-11-09", "by": "K", "firmware": "1.2", "driverVersion": "1.0.0", "evidence": "Run 42", "result": "pass"})
    assert not matrix.validate(registry)
    assert [r for r in matrix.device_rows(registry) if r["Id"] == "dmm1"][0]["Status"] == "bench_validated"


def test_unknown_device_is_not_passed_off_as_supported():
    matrix = CompatibilityMatrix()
    assert matrix.classify(make_candidate("stream", "RS-232", "/dev/ttyUSB0", "", "", "", "port_only", ""))["code"] == "port_only"
    assert matrix.classify(make_candidate("visa", "GPIB", "x", "Nobody", "Z9", "", "device_identified", ""))["code"] == "unknown"


def test_new_driver_needs_no_core_change(folder):
    text = (paths.ROOT / "plugins" / "templates" / "template_driver.txt").read_text().replace("TemplateDriver", "ProbeXDriver").replace('"template"', '"probex"')
    (folder / "probe_x_driver.py").write_text(text)
    registry = DriverRegistry([paths.PLUGINS, folder])
    assert registry.has("probex")
    assert type(registry.create("probex")).__name__ == "ProbeXDriver"


def test_broken_plugin_is_reported_not_fatal(folder):
    (folder / "broken_driver.py").write_text("this is not python !!!")
    registry = DriverRegistry([folder])
    assert any("broken_driver" in problem for problem in registry.problems)


def test_drivers_expose_no_control_path():
    for entry in DriverRegistry().entries:
        names = [n.lower() for n in dir(entry["cls"]) if not n.startswith("_")]
        assert not [n for n in names if any(word in n for word in ("output", "actuate", "setload", "stopengine", "command"))], entry["id"]


def test_unavailable_driver_gives_reason():
    available, reason = availability({"platforms": ["plan9"], "requires": []})
    assert not available and "plan9" in reason


def test_reconnects_after_comm_loss(started_engine):
    tick(started_engine, 3)
    session = started_engine.devices.get("rig_sim")
    assert session.state == "online"
    session.driver.inject({"kind": "commLoss", "t0": started_engine.clock.now(), "durationS": 4})
    tick(started_engine, 2)
    assert session.state == "reconnecting"
    tick(started_engine, 10)
    assert session.state == "online" and session.reconnect_count >= 1


def test_failure_is_contained_to_one_device():
    setup = sim.demo_setup()
    setup["devices"].append({**setup["devices"][0], "id": "rig_b", "name": "Second DAQ", "settings": {"seed": 2}})
    setup["channels"].append({"id": "b_rpm", "kind": "hardware", "deviceId": "rig_b", "hwChannel": "rpm", "quantity": "Rotational speed", "unit": "rpm", "sampleRateHz": 50})
    engine = E.Engine(clock=Clock(1e9), units=UnitRegistry())
    engine.load_setup(setup)
    engine.start()
    tick(engine, 2)
    engine.devices.get("rig_sim").driver.inject({"kind": "commLoss", "t0": engine.clock.now(), "durationS": 30})
    tick(engine, 3)
    assert engine.clock.now() - engine.latest("b_rpm")[1] < 0.2
    assert engine.devices.get("rig_b").state == "online" and engine.devices.get("rig_sim").state == "reconnecting"


def test_expressions_evaluate_and_reject_code():
    assert Expression("1+2*3").evaluate({}) == 7
    assert Expression("-2^2").evaluate({}) == -4
    assert Expression("2^3^2").evaluate({}) == 512
    assert Expression("if(a>1, a*10, 0)").evaluate({"a": 2}) == 20
    assert Expression("a>1 && b<1").evaluate({"a": 2, "b": 0}) == 1
    assert Expression("a*b + sqrt(a)").inputs == ["a", "b"]
    for bad in ("system(1)", "eval('x')", "(1+2", "sqrt(1,2)", "__import__('os')"):
        with pytest.raises(ExpressionError):
            Expression(bad)


def test_units_convert_and_refuse_mismatch():
    u = UnitRegistry()
    assert u.convert(100, "degC", "degF") == pytest.approx(212)
    assert u.convert(1, "bar", "kPa") == pytest.approx(100)
    with pytest.raises(ValueError):
        u.convert(1, "bar", "degC")
    u.define("Force", "myForce", 2, 0)
    assert u.convert(3, "myForce", "N") == 6


def test_calibration_does_not_extrapolate_table():
    c = Calibration.from_dict({"type": "table", "tableRaw": [0, 1, 2], "tableValue": [0, 10, 40]})
    assert c.apply(1.5) == 25
    assert math.isnan(float(c.apply(3)))
    assert Calibration.from_dict({"type": "polynomial", "coefficients": [1, 2, 3]}).apply(2) == 17


def test_channels_can_be_user_defined():
    m = ChannelManager(UnitRegistry())
    m.add(Channel.from_dict({"id": "humidity", "kind": "manual", "quantity": "Relative humidity", "unit": "%RH"}))
    m.add(Channel.from_dict({"id": "x", "kind": "derived", "expression": "humidity*2"}))
    m.rename("humidity", "Cell humidity")
    assert m.get("humidity").name == "Cell humidity"
    with pytest.raises(ConfigError):
        m.remove("humidity")
    m.add(Channel.from_dict({"id": "y", "kind": "derived", "expression": "z"}))
    m.add(Channel.from_dict({"id": "z", "kind": "derived", "expression": "y"}))
    with pytest.raises(ConfigError):
        m.derived_order()


def test_limits_must_be_ordered():
    with pytest.raises(ConfigError):
        Channel.from_dict({"id": "a", "kind": "manual", "warnHigh": 10, "critHigh": 5})


def test_frame_parser_handles_formats():
    assert codecs.parse_frame("1.5,2.5,3.5", "delimited", ",", ["3", "1"]) == ([3.5, 1.5], True)
    assert codecs.parse_frame("rpm=2000,tq=30", "keyvalue", ",", ["tq", "rpm"]) == ([30, 2000], True)
    assert codecs.parse_frame('{"a":1,"b":2}', "json", ",", ["b", "a"]) == ([2, 1], True)
    assert not codecs.parse_frame("1.5,garbage", "delimited", ",", ["1", "2"])[1]
    assert not codecs.parse_frame("1.5", "delimited", ",", ["1", "2"])[1]
    assert not codecs.parse_frame("{broken", "json", ",", ["a"])[1]


def test_register_codec_decodes():
    assert codecs.decode_registers([65535], "int16", "big") == -1
    assert codecs.decode_registers([1, 2], "uint32", "big") == 65538
    assert codecs.decode_registers([2, 1], "uint32", "little") == 65538
    import struct
    low, high = struct.unpack("<2H", struct.pack("<f", 12.5))
    assert codecs.decode_registers([high, low], "single", "big") == 12.5


def test_can_codec_follows_dbc_conventions():
    assert codecs.decode_can(bytes([0x34, 0x12]), 0, 16, "intel", False, 1, 0) == 0x1234
    assert codecs.decode_can(bytes([0x12, 0x34]), 7, 16, "motorola", False, 1, 0) == 0x1234
    assert codecs.decode_can(bytes([0xFF, 0xFF]), 0, 16, "intel", True, 0.5, 10) == 9.5
    assert codecs.decode_can(bytes([0b00001100]), 2, 2, "intel", False, 1, 0) == 3
    with pytest.raises(ValueError):
        codecs.decode_can(bytes([1]), 0, 16, "intel", False, 1, 0)


def test_geometry_relations():
    g = rig.Geometry.from_dict({"boreMm": 80, "strokeMm": 100, "cylinders": 4})
    vs = math.pi / 4 * 8 ** 2 * 10
    assert g.swept_cm3() == pytest.approx(vs)
    assert g.compression_ratio(g.clearance_for(10)) == pytest.approx(10)


def test_performance_relations():
    assert rig.brake_power_kw(3000, 100) == pytest.approx(31.41592653589793)
    assert rig.bmep_kpa(100, 2000, 4) == pytest.approx(628.3185307)
    assert rig.bsfc_g_kwh(10, 40) == 250
    assert math.isnan(rig.bsfc_g_kwh(10, 0))
    assert rig.brake_thermal_efficiency(36, 10, 43.2) == pytest.approx(0.3)


def test_compression_ratio_keeps_provenance():
    latest = lambda _: 11.5
    fixed = rig.CompressionRatio({"mode": "fixed", "nominal": 9.5})
    assert not fixed.is_variable() and fixed.current(latest)["provenance"] == "configured"
    unknown = rig.CompressionRatio({"mode": "unknown"}).current(latest)
    assert unknown["provenance"] == "unknown" and math.isnan(unknown["value"])
    assert rig.CompressionRatio({"mode": "measured", "channelId": "cr"}).current(latest) == {"value": 11.5, "provenance": "measured"}
    manual = rig.CompressionRatio({"mode": "manual", "min": 8, "max": 14})
    manual.enter_manual(10, 100, "first")
    assert manual.current(latest)["provenance"] == "manual"
    with pytest.raises(ValueError):
        manual.enter_manual(20, 200)
    geometry = rig.Geometry.from_dict({"boreMm": 80, "strokeMm": 100, "clearanceCm3": 50})
    assert rig.CompressionRatio({"mode": "calculated"}, geometry).current(latest)["provenance"] == "calculated"


def test_performance_analysis_uses_recorded_data(recorded_run, folder):
    reader, _ = recorded_run
    mapping = {"rpm": "rpm", "torque": "torque", "fuel": "fuel_flow", "air": "air_flow", "intakePress": "intake_press", "intakeTemp": "intake_temp"}
    table = rig.analyze_run(reader, mapping, rig.Geometry.from_dict({"boreMm": 86, "strokeMm": 86, "cylinders": 4}), {"lhvMJkg": 42.5})
    for column in ("brake_power_kW", "bmep_kPa", "bsfc_g_kWh", "brake_thermal_efficiency", "volumetric_efficiency", "air_fuel_ratio"):
        assert column in table
    late = table.tail(5)
    assert np.allclose(late["brake_power_kW"], 2 * np.pi * late["rpm"] * late["torque_Nm"] / 60000)
    path = folder / "calc.csv"
    exchange.export(reader, path, channels=["rpm"], calculated=table)
    assert "calc_brake_power_kW" in pd.read_csv(path).columns


def test_compression_ratio_series_from_recorded_run(recorded_run):
    reader, _ = recorded_run
    table = rig.CompressionRatio({"mode": "measured", "channelId": "compression_ratio"}).series(reader, {"rpm": "rpm", "torque": "torque"})
    assert table["provenance"].iloc[0] == "measured"
    assert table["compression_ratio"].mean() == pytest.approx(1.2 * 5 + 4.5, abs=0.05)
    compared = rig.CompressionRatio.compare_by_ratio(table.iloc[1:], "torque", 0.5)
    assert compared["samples"].sum() == len(table) - 1
    fixed = rig.CompressionRatio({"mode": "fixed", "nominal": 9}).series(reader, {"rpm": "rpm"})
    assert set(fixed["provenance"]) == {"configured"} and set(fixed["compression_ratio"]) == {9}


@pytest.mark.parametrize("extension", ["csv", "txt", "xlsx", "xls", "json", "xml", "mat", "h5", "parquet"])
def test_export_formats(recorded_run, folder, extension):
    reader, _ = recorded_run
    path = folder / f"out.{extension}"
    exchange.export(reader, path, channels=["rpm", "oil_press"], content="both", include_flags=True)
    assert path.stat().st_size > 0


@pytest.mark.parametrize("extension", ["pdf", "png", "svg", "jpg"])
def test_figure_formats(recorded_run, folder, extension):
    path = folder / f"fig.{extension}"
    exchange.export_figure(recorded_run[0], path, channels=["rpm", "torque"])
    assert path.stat().st_size > 0


def test_export_keeps_all_raw_samples(recorded_run, folder):
    reader, _ = recorded_run
    path = folder / "out.csv"
    exchange.export(reader, path, channels=["rpm", "oil_press"], content="both")
    assert pd.read_csv(path)["oil_press_raw"].notna().sum() == reader.read(["oil_press"])["oil_press"]["raw"].size


def test_export_selects_time_range_and_channels(recorded_run, folder):
    reader, _ = recorded_run
    path = folder / "subset.csv"
    exchange.export(reader, path, channels=["rpm"], time_range=(reader.start_t + 1, reader.start_t + 2))
    table = pd.read_csv(path)
    assert table["t_run_s"].max() <= 2.0001 and table["t_run_s"].min() >= 0.9999 and "torque_N_m" not in table


def test_truncated_run_is_still_readable(recorded_run):
    reader, run = recorded_run
    before = reader.read(["rpm"])["rpm"]["t"].size
    with (run / "samples.bin").open("ab") as file:
        file.write(bytes(range(7)))
    assert RunReader(run).read(["rpm"])["rpm"]["t"].size == before


def test_run_retains_configuration_and_calibration(recorded_run):
    reader, _ = recorded_run
    assert reader.channel("oil_press")["calibration"]["certificate"] == "SIM-CAL-001"
    assert reader.manifest["setup"]["schemaVersion"] == 2
    assert len(reader.manifest["configSha256"]) == 64


def test_import_maps_columns_and_keeps_source(recorded_run, folder):
    reader, _ = recorded_run
    path = folder / "src.csv"
    exchange.export(reader, path, channels=["rpm"])
    mapping = {"time": "t_posix_s", "timeKind": "posix", "channels": [{"column": "rpm_rpm", "id": "speed", "unit": "rpm", "quantity": "Rotational speed"}]}
    imported = RunReader(exchange.ingest(path, mapping, folder / "imported"))
    assert imported.manifest["source"] == "import" and len(imported.manifest["sourceFile"]["sha256"]) == 64
    assert np.allclose(imported.read(["speed"])["speed"]["value"], reader.read(["rpm"])["rpm"]["value"], rtol=1e-4)


@pytest.mark.parametrize("extension", ["xlsx", "mat", "parquet", "h5", "json"])
def test_imports_other_formats(recorded_run, folder, extension):
    path = folder / f"src.{extension}"
    exchange.export(recorded_run[0], path, channels=["rpm"])
    assert "rpm_rpm" in exchange.preview(path).columns


def test_report_is_built_from_template(recorded_run, folder):
    path = folder / "report.pdf"
    template = {"title": "Test", "sections": [{"type": "summary"}, {"type": "channels", "ids": ["rpm", "oil_press"]}, {"type": "statistics", "ids": ["oil_press"], "units": {"oil_press": "psi"}},
                                              {"type": "plot", "ids": ["rpm"], "heading": "Speed"}, {"type": "alarms"}, {"type": "text", "heading": "Notes", "body": "OK"}]}
    experiment.build_report(recorded_run[0], template, path)
    assert path.stat().st_size > 1000


def test_experiment_runs_store_context(started_engine, folder):
    store = experiment.ExperimentStore(folder / "exp")
    experiment_id = store.create({"name": "Fuel sweep", "engines": [{"name": "E1"}], "fuels": [{"name": "E10", "lhvMJkg": 41}], "methods": [{"name": "Steady"}]})
    run = store.start_run(started_engine, experiment_id, {"fuel": "E10", "operator": "K"})
    tick(started_engine, 1)
    started_engine.stop()
    context = RunReader(run).manifest["context"]
    assert context["fuel"] == "E10" and context["experimentSpec"]["name"] == "Fuel sweep" and len(store.runs(experiment_id)) == 1


def test_setup_round_trips_through_code(started_engine, folder):
    started_engine.stop()
    path = folder / "setup.py"
    codefile.write(path, started_engine.current_setup())
    again = E.Engine(clock=Clock(1e9), units=UnitRegistry())
    again.load_setup(codefile.read(path))
    assert again.channels.ids() == started_engine.channels.ids()
    assert codefile.encode(again.current_setup()["channels"]) == codefile.encode(started_engine.current_setup()["channels"])


def test_setup_library_keeps_several_setups(folder):
    library, setup = E.SetupLibrary(folder), sim.demo_setup()
    library.save("Engine A", setup)
    library.save("Engine B", setup)
    assert library.list() == ["Engine_A", "Engine_B"] and library.read("Engine B")["name"] == "Engine_B"
    with pytest.raises(FileExistsError):
        library.save("Engine A", setup)
    library.save("Engine A", setup, True)
    library.remove("Engine A")
    assert library.list() == ["Engine_B"]
    for bad in ("../escape", "a/b", "", ".hidden", "name.", "1abc", "current", "rejected_x"):
        with pytest.raises(ValueError):
            library.save(bad, setup)
    codefile.write(folder / "current.py", setup)
    assert library.list() == ["Engine_B"]


def test_unusable_saved_setup_falls_back_to_demo(folder):
    setup = sim.demo_setup()
    setup["devices"][0]["driver"] = "driver_not_on_this_computer"
    path = folder / "current.py"
    codefile.write(path, setup)
    engine = E.Engine(clock=Clock(1e9), units=UnitRegistry())
    assert "driver_not_on_this_computer" in E.restore_setup(engine, path)
    assert engine.devices.has("rig_sim") and not path.exists() and len(list(folder.glob("rejected_*.py"))) == 1


VALUE = {"text": 'say "hi"\nagain', "list": ["a", "b"], "n": 0.1, "big": 1.5e300, "far": -math.inf, "flag": True, "row": [1, -2.5, 3], "matrix": [[1, 2], [3, 4]],
         "nothing": None, "nested": {"a": 1}, "blank": {}, "items": [{"x": 1}, "two", [], [1, 2]]}


def test_code_files_round_trip_and_are_real_python(folder):
    path = folder / "sample.py"
    codefile.write(path, VALUE)
    assert codefile.read(path) == VALUE
    namespace = {}
    exec(path.read_text(), namespace)
    assert namespace["data"] == VALUE
    codefile.write(path, {"gone": math.nan})
    assert math.isnan(codefile.read(path)["gone"])


@pytest.mark.parametrize("attack", ["import os\ndata = 1", "data = __import__('os').system('x')", "data = 1\nprint(1)", "other = 1", "data = 1 + 1", "data = [x for x in []]",
                                    "data = open('x')", "data = {**{}}", "data.append(1)\ndata = eval('1')", "def f(): pass"])
def test_code_files_refuse_anything_but_plain_values(attack):
    with pytest.raises(codefile.UnsafeCode):
        codefile.decode(attack)


def test_truncated_log_tail_is_ignored():
    assert codefile.decode('data = []\ndata.append({"a": 1})\ndata.append({"a":', True) == [{"a": 1}]


def test_reads_matlab_code_files_without_running_them():
    text = 'function data = sample()\n% note\ndata = struct();\ndata.name = "a ""b""" + newline + "c";\ndata.gone = NaN;\ndata.row = [1 -2.5];\ndata.items{2}.x = true;\ndata.none = strings(1, 0);\n'
    data = codefile.decode_matlab(text)
    assert data["name"] == 'a "b"\nc' and math.isnan(data["gone"]) and data["row"] == [1, -2.5] and data["items"][1]["x"] is True and data["none"] == []
    with pytest.raises(codefile.UnsafeCode):
        codefile.decode_matlab('data.x = system("rm");')


def test_forms_convert_table_rows():
    fields = [forms.field("type", "Type", "choice", ["", "roc"]), forms.field("maxRate", "Rate", "number"), forms.field("note", "Note"), forms.field("on", "On", "bool")]
    rows = forms.to_rows([{"type": "roc", "maxRate": 40, "on": True}], fields, 2)
    assert len(rows) == 3 and forms.from_rows(rows, fields) == [{"type": "roc", "maxRate": 40.0, "on": True}]
    rows[0][1] = "fast"
    with pytest.raises(ValueError):
        forms.from_rows(rows, fields)
    assert forms.number_list("2.5, -1.25", "c") == [2.5, -1.25]
    assert forms.range_text(math.nan, math.nan) == "" and forms.range_text(math.nan, 5) == "<= 5"


def test_report_template_follows_section_table(recorded_run, folder):
    rows = [{"section": "alarms", "include": True}, {"section": "plot", "include": False}, {"section": "statistics", "include": True, "channels": "rpm, oil_press", "units": "oil_press=psi"}, {"section": "text", "include": True, "body": "Done"}]
    template = forms.template_from_rows("R", rows, None)
    assert [s["type"] for s in template["sections"]] == ["alarms", "statistics", "text"]
    assert template["sections"][1]["ids"] == ["rpm", "oil_press"] and template["sections"][1]["units"]["oil_press"] == "psi" and template["sections"][2]["heading"] == "Notes"
    path = folder / "from_table.pdf"
    experiment.build_report(recorded_run[0], template, path)
    assert path.exists()


def test_alarms_reach_disk_without_recording(started_engine, folder):
    path = folder / "events.py"
    started_engine.set_event_log(path)
    sim.inject(started_engine, sim.find_scenario("daq_failure"), "rig_sim", started_engine.clock.now())
    tick(started_engine, 2)
    assert "instrument_fault" in path.read_text() and codefile.read(path, True)


def _sources():
    return [f for root in (paths.ROOT / "edaq", paths.ROOT / "plugins") for f in root.rglob("*.py")]


def test_no_json_in_the_application():
    found = [f for f in paths.ROOT.rglob("*.json*") if ".venv" not in f.parts and paths.TMP not in f.parents]
    assert not found
    # Only the JSON data export/import format and the JSON instrument protocol.
    allowed = {"exchange.py", "codecs.py"}
    for file in _sources():
        if file.name not in allowed:
            assert "json." not in file.read_text() and "import json" not in file.read_text(), file.name


def test_independent_of_the_matlab_build():
    for file in _sources():
        assert "../MATLAB" not in file.read_text() and "MATLAB/" not in file.read_text() and "matlab.engine" not in file.read_text(), file.name


def test_nothing_writes_outside_the_application():
    for name in ("WORKSPACE", "CONFIG", "PLUGINS", "DOCS", "TMP"):
        assert paths.ROOT in getattr(paths, name).parents
    for file in _sources():
        text = file.read_text()
        assert not any(word in text for word in ("Path.home", "expanduser", "tempfile", "gettempdir", "site-packages")), file.name
