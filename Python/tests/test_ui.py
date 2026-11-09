"""Builds the real window and exercises every page and editor against the
simulator on a manual clock."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from conftest import tick
from edaq import codefile, engine as E, experiment, paths, sim
from edaq.channels.units import UnitRegistry
from edaq.clock import Clock
from edaq.ui import dialogs, forms
from edaq.ui import widgets as W
from edaq.ui.app import App
from edaq.ui.pages import DataPage


@pytest.fixture
def qt():
    application = QApplication.instance() or QApplication([])
    application.setProperty("edaq_test", True)
    return application


@pytest.fixture
def app(qt, folder, monkeypatch):
    monkeypatch.setattr(paths, "DASHBOARDS", folder / "dashboards")
    monkeypatch.setattr(paths, "EXPORTS", folder / "exports")
    (folder / "dashboards").mkdir()
    engine = E.Engine(clock=Clock(1.8e9), units=UnitRegistry())
    engine.load_setup(sim.demo_setup())
    window = App(engine=engine, persist=False, library=E.SetupLibrary(folder), experiments=experiment.ExperimentStore(folder / "exp"), start_timers=False)
    yield window
    window.close()


def no_error(widget):
    assert widget.window().property("last_error") is None, widget.window().property("last_error")


def test_every_page_refreshes_while_acquiring(app):
    app.engine.start()
    tick(app.engine, 30)
    for name in app.pages:
        app.select_page(name)
        app.refresh()
    no_error(app)


def test_both_themes_build(app):
    app.toggle_theme()
    app.refresh()
    app.toggle_theme()
    app.refresh()
    no_error(app)


def test_banner_follows_fault_and_persists(app):
    engine = app.engine
    engine.start()
    tick(engine, 10)
    app.refresh()
    assert app.red_banner.isHidden()
    engine.devices.get("rig_sim").driver.inject({"kind": "set", "channel": "dyno_fault", "t0": engine.clock.now(), "durationS": 1, "value": 1})
    tick(engine, 3)
    app.refresh()
    assert engine.overall_status() == "red" and not app.red_banner.isHidden()
    tick(engine, 5)
    app.refresh()
    assert not app.red_banner.isHidden()


def test_dashboard_round_trips_through_file(app):
    page = app.dashboard
    page.set_widgets(page.page_widgets() + [{"type": "numeric", "title": "x", "channels": ["rpm"], "windowS": 60, "row": 8, "col": 12, "rowSpan": 1, "colSpan": 1}])
    before = len(page.page_widgets())
    page.name.setText("Bench_one")
    page.save()
    page.load_named("Bench_one")
    assert len(page.page_widgets()) == before


def test_widget_for_deleted_channel_does_not_break_page(app):
    app.dashboard.set_widgets([{"type": "gauge", "title": "gone", "channels": ["no_such"], "windowS": 60, "row": 1, "col": 1, "rowSpan": 2, "colSpan": 2}])
    assert not app.dashboard.widgets


def test_wizard_builds_form_for_every_driver(app):
    wizard = dialogs.DeviceWizard(app)
    for k in range(wizard.picker.count()):
        wizard.picker.setCurrentIndex(k)
    no_error(wizard)


def test_wizard_tests_and_adds_stream_device(app):
    wizard = dialogs.DeviceWizard(app)
    wizard.picker.setCurrentIndex(wizard.picker.findData("stream"))
    wizard.inputs["channels"].setItem(0, 0, forms.QTableWidgetItem("a"))
    wizard.inputs["channels"].setItem(0, 1, forms.QTableWidgetItem("1"))
    wizard.inputs["transport"].setCurrentText("tcp")
    wizard.inputs["tcpPort"].setText("1")
    wizard.test()
    assert "NOT verified" in wizard.log.toPlainText()
    wizard.add()
    assert app.engine.devices.has("device_1")


def test_channel_editor_creates_and_edits_channel(app):
    editor = dialogs.ChannelEditor(app)
    editor.controls["kind"].setCurrentText("manual")
    editor.controls["id"].setText("operator_note")
    editor.save()
    assert app.engine.channels.has("operator_note")
    channel = app.engine.channels.get("oil_temp")
    editor = dialogs.ChannelEditor(app, channel)
    editor.controls["name"].setText("Sump temperature")
    editor.save()
    edited = app.engine.channels.get("oil_temp")
    assert edited.name == "Sump temperature" and len(edited.rules) == len(channel.rules)


def test_channel_editor_keeps_calibration_and_rules(app):
    before = app.engine.channels.get("oil_press").to_dict()
    dialogs.ChannelEditor(app, app.engine.channels.get("oil_press")).save()
    after = app.engine.channels.get("oil_press").to_dict()
    assert after["calibration"]["coefficients"] == before["calibration"]["coefficients"] and after["calibration"]["certificate"] == before["calibration"]["certificate"]
    assert after["rangeMax"] == before["rangeMax"] and after["rules"][0]["windowS"] == before["rules"][0]["windowS"]


def test_channel_editor_rejects_bad_number(app):
    editor = dialogs.ChannelEditor(app, app.engine.channels.get("oil_temp"))
    editor.controls["warnHigh"].setText("hot")
    with pytest.raises(ValueError, match="not a number"):
        editor.save()


def test_cross_rule_editor_saves(app):
    before = len(app.engine.cross_rules)
    dialogs.CrossRulesEditor(app).save()
    assert len(app.engine.cross_rules) == before


def test_add_widget_dialog_adds_widget(app):
    page = app.dashboard
    page.set_widgets([])
    editor = dialogs.WidgetEditor(page)
    editor.channels.item(0).setSelected(True)
    editor.add()
    assert len(page.page_widgets()) == 1


def test_layout_editor_drag_and_apply(app):
    page = app.dashboard
    editor = dialogs.LayoutEditor(page)
    canvas = editor.canvas
    canvas.resize(1200, 800)
    first = dict(canvas.widgets[0])
    centre = canvas._rect(first).center()
    canvas.press_at(centre)
    canvas.move_to(centre + QPointF(0, 300))
    canvas.drag = None
    assert canvas.widgets[0]["row"] > first["row"]
    canvas.widgets[0] = first
    editor.apply()
    no_error(page)


def test_layout_snaps_and_rejects_overlap():
    w = {"row": 1, "col": 1, "rowSpan": 2, "colSpan": 3}
    assert (W.moved(w, 100, 100)["row"], W.moved(w, 100, 100)["col"]) == (7, 10)
    assert (W.resized(w, 0.2, 50)["rowSpan"], W.resized(w, 0.2, 50)["colSpan"]) == (1, 12)
    assert W.has_overlap([w, w]) and not W.overlap(W.placed(w, [w]), w)


def channel_name(setup):
    return [c for c in setup["channels"] if c["id"] == "rpm"][0]["name"]


def test_several_setups_can_be_saved_and_switched(app):
    library = app.library
    dialog = dialogs.SetupLibraryDialog(app)
    dialog.name.setText("Rig A")
    dialog.save()
    app.engine.channels.rename("rpm", "Speed B")
    dialog.name.setText("Rig B")
    dialog.save()
    assert library.list() == ["Rig_A", "Rig_B"]
    app.engine.channels.rename("rpm", "Changed")
    dialog.save()
    assert channel_name(library.read("Rig B")) == "Speed B", "First press must not overwrite."
    dialog.save()
    assert channel_name(library.read("Rig B")) == "Changed"
    dialog.list.setCurrentRow(0)
    dialog.remove()
    assert library.exists("Rig A"), "First press must not delete."
    dialog.remove()
    assert not library.exists("Rig A")
    dialog.list.setCurrentRow(0)
    app.engine.channels.rename("rpm", "Unsaved")
    dialog.load()
    assert app.engine.channels.get("rpm").name == "Changed"
    app.refresh()


def test_failed_load_keeps_current_setup(app):
    bad = sim.demo_setup()
    bad["devices"][0]["driver"] = "driver_not_on_this_computer"
    app.library.save("Bad", bad)
    dialog = dialogs.SetupLibraryDialog(app)
    dialog.list.setCurrentRow(0)
    with pytest.raises(KeyError):
        dialog.load()
    assert app.engine.devices.has("rig_sim") and app.engine.channels.list


def test_experiment_is_created_from_forms(app):
    page = app.pages["Experiments"]
    page.name.setText("Fuel sweep")
    page.tables["fuels"].setItem(0, 0, forms.QTableWidgetItem("E10"))
    page.create()
    ids = app.experiments.list()
    assert len(ids) == 1 and app.experiments.load(ids[0])["fuels"][0]["name"] == "E10"
    assert page.fuel_choice.findText("E10") >= 0


def test_record_export_and_report_through_pages(app, monkeypatch):
    page = app.pages["Experiments"]
    page.name.setText("Run test")
    page.create()
    app.engine.start()
    page.start_run()
    tick(app.engine, 2)
    app.engine.stop_recording()
    app.engine.stop()
    page.show()
    page.report()
    assert "Report:" in page.status.text() and list(paths.EXPORTS.glob("*_report.pdf"))
    data = app.pages["Data"]
    monkeypatch.setattr("edaq.ui.pages._all_run_folders", lambda: app.experiments.runs(app.experiments.list()[0]))
    data.list_runs()
    data.format.setCurrentText("csv")
    data.do_export()
    assert list(paths.EXPORTS.glob("*.csv"))


def test_simple_prompts(app, monkeypatch):
    channels = app.pages["Channels"]
    monkeypatch.setattr(forms, "ask", lambda *_: ["Force", "myForce2", "2", "0"])
    channels.define_unit()
    assert app.engine.units.is_known("myForce2")
    compatibility = app.pages["Compatibility"]
    monkeypatch.setattr(app.engine.matrix, "save", lambda: None)
    monkeypatch.setattr(forms, "ask", lambda *_: ["dmm9", "Acme", "M9", "DAQ", "RS-232", "stream", "implemented", ""])
    compatibility.add_device()
    compatibility.refresh()
    compatibility.equipment.selectRow(compatibility.equipment.rowCount() - 1)
    monkeypatch.setattr(forms, "ask", lambda *_: ["2026-11-09", "K", "1.2", "1.0.0", "Run 42", "pass"])
    compatibility.validate()
    assert [r for r in app.engine.matrix.device_rows(app.engine.registry) if r["Id"] == "dmm9"][0]["Status"] == "bench_validated"
    engine_page = app.pages["Engine"]
    engine_page.mode.setCurrentText("manual")
    engine_page.apply()
    monkeypatch.setattr(forms, "ask", lambda *_: ["11", "note"])
    engine_page.manual_entry()
    assert engine_page.manager.current(lambda _: 0)["provenance"] == "manual"


def test_acknowledge_and_reset_through_page(app):
    engine = app.engine
    engine.start()
    tick(engine, 2)
    engine.devices.get("rig_sim").driver.inject({"kind": "set", "channel": "dyno_fault", "t0": engine.clock.now(), "durationS": 30, "value": 1})
    tick(engine, 3)
    page = app.pages["Alarms"]
    page.refresh()
    page.current.selectRow(0)
    with pytest.raises(ValueError):
        page.reset()
    page.ack()
    assert engine.alarms.conditions["chan:dyno_fault:signal"]["state"] == "active_ack"
