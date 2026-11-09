"""The application pages. Each has a refresh method, called only while visible."""
from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import alarms, codefile, experiment, paths, sim
from ..channels.channel import Channel
from ..data import exchange
from ..data.store import RunReader
from ..engine import compute_health
from ..hw import discovery
from ..rig import CompressionRatio, Geometry
from ..util import iso_time, new_id, number, opt, to_list
from . import dialogs, forms
from . import widgets as W
from .forms import DataTable, SpecTable, button, field, guard, heading, hbox, number_text, range_text
from .theme import THEMES, pill_style


class Page(QWidget):
    title = ""
    hint = ""

    def __init__(self, app):
        super().__init__()
        self.setObjectName("page")
        self.app = app
        self.engine = app.engine
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 16, 20, 16)
        self.root.setSpacing(10)
        self.root.addWidget(heading(self.title, "pageTitle"))
        if self.hint:
            label = QLabel(self.hint)
            label.setObjectName("hint")
            self.root.addWidget(label)

    def b(self, text, action, primary=False):
        return button(text, action, primary, owner=self)

    def refresh(self):
        pass

    def selected_or_fail(self, table, what="a row"):
        row = table.selected_row()
        if row is None:
            raise ValueError(f"Select {what} first.")
        return row


class DashboardPage(Page):
    title = "Dashboard"

    def __init__(self, app):
        super().__init__(app)
        self.widgets = []
        self.picker = QComboBox()
        self.picker.setMinimumWidth(180)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Dashboard name")
        self.name.setMaximumWidth(180)
        self.root.addLayout(hbox(self.picker, self.name, self.b("Save", self.save), self.b("Add page", self.add_page), self.b("Add widget", lambda: dialogs.WidgetEditor(self).exec()),
                                 self.b("Edit layout", lambda: dialogs.LayoutEditor(self).exec()), self.b("Remove page", self.remove_page)))
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(guard(self, self.build_active))
        self.picker.activated.connect(guard(self, lambda: self.load_named(self.picker.currentData())))
        self.set_model(self.default_model())
        self.refresh_picker()

    def set_model(self, model):
        self.model = {**model, "pages": to_list(model["pages"])}
        self.name.setText(str(model["name"]))
        self.rebuild()

    def rebuild(self):
        selected = max(self.tabs.currentIndex(), 0)
        self.widgets = []
        self.tabs.blockSignals(True)
        self.tabs.clear()
        for page in self.model["pages"]:
            self.tabs.addTab(QWidget(), str(page["name"]))
        self.tabs.setCurrentIndex(min(selected, self.tabs.count() - 1))
        self.tabs.blockSignals(False)
        self.build_active()

    def build_active(self):
        k = self.tabs.currentIndex()
        tab = QWidget()
        self.widgets = []
        grid = QGridLayout(tab)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        for r in range(W.ROWS):
            grid.setRowStretch(r, 1)
        for c in range(W.COLS):
            grid.setColumnStretch(c, 1)
        for spec in to_list(self.model["pages"][k].get("widgets")):
            try:
                widget = W.Widget(spec, self.engine, self.app.theme)
                self.widgets.append(widget)
            except Exception as error:      # a widget naming a deleted channel must not lose the page
                widget = QLabel(f"Widget unavailable: {error}")
                widget.setWordWrap(True)
            grid.addWidget(widget, int(spec["row"]) - 1, int(spec["col"]) - 1, int(spec["rowSpan"]), int(spec["colSpan"]))
        self.tabs.blockSignals(True)
        title = self.tabs.tabText(k)
        self.tabs.removeTab(k)
        self.tabs.insertTab(k, tab, title)
        self.tabs.setCurrentIndex(k)
        self.tabs.blockSignals(False)

    def page_widgets(self):
        return list(to_list(self.model["pages"][self.tabs.currentIndex()].get("widgets")))

    def set_widgets(self, widgets):
        self.model["pages"][self.tabs.currentIndex()]["widgets"] = widgets
        self.build_active()

    def refresh(self):
        for widget in self.widgets:
            widget.refresh()

    def save(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError("Enter a dashboard name.")
        self.model["name"] = name
        codefile.write(paths.DASHBOARDS / f"{re.sub(r'\W', '_', name)}.py", self.model)
        self.refresh_picker()

    def load_named(self, name):
        if name:
            for suffix in (".py", ".m"):
                path = paths.DASHBOARDS / f"{name}{suffix}"
                if path.is_file():
                    self.set_model(codefile.read(path))
                    return

    def refresh_picker(self):
        self.picker.clear()
        self.picker.addItem("Saved dashboards...", "")
        for file in sorted(paths.DASHBOARDS.glob("*.py")) + sorted(paths.DASHBOARDS.glob("*.m")):
            self.picker.addItem(file.stem, file.stem)

    def add_page(self):
        self.model["pages"].append({"name": f"Page {len(self.model['pages']) + 1}", "widgets": []})
        self.rebuild()

    def remove_page(self):
        if len(self.model["pages"]) <= 1:
            raise ValueError("A dashboard needs at least one page.")
        del self.model["pages"][self.tabs.currentIndex()]
        self.rebuild()

    def default_model(self):
        """Built from whichever channels the setup has."""
        channels = self.engine.channels
        ids = [c.id for c in channels.list if c.role == "measurement" and c.kind != "manual"]
        signals = [c.id for c in channels.list if c.role != "measurement"]

        def w(kind, title, ids_, row, col, row_span, col_span):
            return {"type": kind, "title": title, "channels": ids_, "windowS": 60, "row": row, "col": col, "rowSpan": row_span, "colSpan": col_span}
        items = [w("gauge", channels.get(i).name, [i], 1, 3 * k + 1, 3, 3) for k, i in enumerate(ids[:4])]
        if ids:
            second = ids[min(1, len(ids) - 1)]
            items += [w("trend", channels.get(ids[0]).name, [ids[0]], 4, 1, 3, 4), w("trend", channels.get(second).name, [second], 4, 5, 3, 4), w("numeric", "Measurements", ids[:5], 4, 9, 3, 4)]
        items.append(w("alarms", "Active alarms", [], 7, 1, 2, 8))
        if signals:
            items.append(w("status", "Equipment signals", signals, 7, 9, 2, 4))
        second_page = ([w("xy", "XY plot", ids[:2], 1, 1, 4, 6)] if len(ids) >= 2 else []) + [w("table", "All channels", ids, 1, 7, 8, 6)]
        return {"name": "Default", "pages": [{"name": "Overview", "widgets": items}, {"name": "Data", "widgets": second_page}]}


STATE_COLORS = {"online": "green", "reconnecting": "yellow", "failed": "red", "offline": "grey"}


class DevicesPage(Page):
    title = "Devices"
    hint = "A port or device being found is only a candidate. The verdict column says what is actually known."

    def __init__(self, app):
        super().__init__(app)
        self.candidates = []
        self.root.addLayout(hbox(self.b("Discover hardware", self.discover, primary=True), self.b("Configure selected", self.configure_selected),
                                 self.b("Add device manually", lambda: dialogs.DeviceWizard(self.app).exec()), self.b("Remove device", self.remove_device), self.b("Test device", self.test_device)))
        self.found = DataTable(["Driver", "Interface", "Address", "Vendor", "Model", "Serial", "Confidence", "Verdict", "What this means"])
        self.root.addWidget(self.found, 1)
        self.root.addWidget(heading("Configured devices"))
        self.devices = DataTable(["Id", "Driver", "State", "Vendor / model", "Identification evidence", "Reconnects", "Last error"])
        self.root.addWidget(self.devices, 1)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(100)
        self.root.addWidget(self.log)

    def discover(self):
        self.candidates, skipped = discovery.scan(self.engine.registry, self.engine.matrix)
        self.found.set_rows([[c["driver"], c["interface"], c["address"], c["vendor"], c["model"], c["serial"], c["confidence"], c["verdict"]["code"], c["verdict"]["message"]] for c in self.candidates])
        self.log.setPlainText("\n".join(["Interfaces that could not be scanned on this host:"] + skipped))

    def configure_selected(self):
        dialogs.DeviceWizard(self.app, self.candidates[self.selected_or_fail(self.found, "a discovered candidate")]).exec()

    def _selected_session(self):
        return self.engine.devices.sessions[self.selected_or_fail(self.devices, "a device")]

    def remove_device(self):
        self.app.require_stopped()
        self.engine.devices.remove(self._selected_session().id)
        self.app.configuration_changed()

    def test_device(self):
        self.app.require_stopped()
        report = discovery.connection_test(self.engine.registry, self._selected_session().config)
        self.log.setPlainText("\n".join(f'[{"ok" if s["ok"] else "FAIL"}] {s["step"]}: {s["detail"]}' for s in report["steps"]))

    def refresh(self):
        sessions = self.engine.devices.sessions
        self.devices.set_rows([[s.id, s.config["driver"], s.state, f'{opt(s.identity, "vendor", "")} {opt(s.identity, "model", "")}'.strip(), opt(s.identity, "evidence", ""),
                                s.reconnect_count, s.last_error] for s in sessions], {2: [STATE_COLORS[s.state] for s in sessions]})


class ChannelsPage(Page):
    title = "Channels"

    def __init__(self, app):
        super().__init__(app)
        self.root.addLayout(hbox(self.b("Add channel", self.add, primary=True), self.b("Edit selected", self.edit), self.b("Remove selected", self.remove), self.b("Define unit", self.define_unit),
                                 self.b("Import from device", self.import_from_device), self.b("Cross-channel rules", self.cross_rules)))
        self.table = DataTable(["Id", "Name", "Kind", "Role", "Sensor", "Source", "Quantity", "Unit", "Range", "Rate (Hz)", "Warn", "Critical limit", "Cal.", "Rules", "On"])
        self.root.addWidget(self.table, 1)
        self.table.doubleClicked.connect(guard(self, self.edit))

    def add(self):
        self.app.require_stopped()
        dialogs.ChannelEditor(self.app).exec()

    def _selected(self):
        return self.engine.channels.list[self.selected_or_fail(self.table, "a channel")]

    def edit(self):
        self.app.require_stopped()
        dialogs.ChannelEditor(self.app, self._selected()).exec()

    def remove(self):
        self.app.require_stopped()
        self.engine.channels.remove(self._selected().id)
        self.app.configuration_changed()

    def define_unit(self):
        values = forms.ask(self, "Define unit", [("Quantity (existing or new)", "Pressure"), ("Unit symbol", ""), ("Factor to base", "1"), ("Offset to base", "0")])
        if values:
            self.engine.units.define(values[0], values[1], float(values[2]), float(values[3]))

    def cross_rules(self):
        self.app.require_stopped()
        dialogs.CrossRulesEditor(self.app).exec()

    def import_from_device(self):
        """Create channels for what a connected device exposes."""
        self.app.require_stopped()
        sessions = self.engine.devices.sessions
        if not sessions:
            raise ValueError("Add a device first.")
        values = forms.ask(self, "Import from device", [("Device id", sessions[0].id)])
        if not values:
            return
        session = self.engine.devices.get(values[0])
        session.driver.note_time(self.engine.clock.now())
        session.driver.connect()
        try:
            listed = session.driver.discover_channels()
        finally:
            session.driver.disconnect()
        for item in listed:
            channel_id = re.sub(r"\W", "_", f'{session.id}_{item["hw"]}')
            if self.engine.channels.has(channel_id):
                continue
            channel = Channel(id=channel_id, name=item["hw"], device_id=session.id, hw_channel=item["hw"], sample_rate_hz=number(item.get("sampleRateHz")))
            if self.engine.units.is_known(item.get("unit", "")):
                channel.unit, channel.quantity = item["unit"], self.engine.units.quantity_of(item["unit"])
            self.engine.channels.add(channel)
        self.app.configuration_changed()

    def refresh(self):
        rows = []
        for c in self.engine.channels.list:
            source = f"{c.device_id}/{c.hw_channel}" if c.kind == "hardware" else c.expression
            rows.append([c.id, c.name, c.kind, c.role, c.sensor_type, source, c.quantity, c.unit, range_text(c.range_min, c.range_max), number_text(c.sample_rate_hz),
                         range_text(c.warn_low, c.warn_high), range_text(c.crit_low, c.crit_high), c.calibration.type, len(c.rules), "yes" if c.enabled else "no"])
        self.table.set_rows(rows)


_STATE_TEXT = {"active_unack": "Active, needs acknowledging", "active_ack": "Active, acknowledged", "cleared_unack": "Cleared, needs acknowledging", "cleared_latched": "Cleared, needs reset"}


class AlarmsPage(Page):
    title = "Alarms"

    def __init__(self, app):
        super().__init__(app)
        self.keys = []
        self.who = QLineEdit()
        self.who.setPlaceholderText("Operator name")
        self.who.setMaximumWidth(200)
        self.root.addLayout(hbox(self.b("Acknowledge", self.ack, primary=True), self.b("Acknowledge all", self.ack_all), self.b("Reset latched", self.reset), self.who, self.b("Export events", self.export)))
        self.current = DataTable(["State", "Classification", "Severity", "Source", "Channel", "Value", "Expected", "Time (UTC)", "Status", "Count", "Reason"])
        self.current.itemSelectionChanged.connect(guard(self, self.show_detail))
        self.root.addWidget(self.current, 3)
        self.detail = QPlainTextEdit("Select an alarm for its diagnostic information.", readOnly=True)
        self.detail.setMaximumHeight(130)
        self.root.addWidget(self.detail)
        self.root.addWidget(heading("Alarm history"))
        self.history = DataTable(["Time (UTC)", "Event", "State", "Classification", "Key", "Value", "Detail"])
        self.root.addWidget(self.history, 3)

    def operator(self):
        return self.who.text().strip() or "operator"

    def _key(self):
        return self.keys[self.selected_or_fail(self.current, "an alarm")]

    def ack(self):
        self.engine.alarms.acknowledge(self._key(), self.operator(), self.engine.clock.now())

    def ack_all(self):
        self.engine.alarms.acknowledge_all(self.operator(), self.engine.clock.now())

    def reset(self):
        if not self.engine.alarms.reset(self._key(), self.operator(), self.engine.clock.now()):
            raise ValueError("Only an acknowledged alarm whose condition has cleared can be reset.")

    def export(self):
        import pandas as pd
        path = paths.EXPORTS / f"{new_id('alarm_events')}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.engine.alarms.history).to_csv(path, index=False)
        self.detail.setPlainText(f"Exported to {path}")

    def show_detail(self):
        row = self.current.selected_row()
        if row is None or row >= len(self.keys):
            return
        c = self.engine.alarms.conditions[self.keys[row]]
        self.detail.setPlainText(f'Reason: {c["reason"]}\nExpected: {c["expected"]}\nRecommended action: {c["recommendation"]}\nEquipment: {c["equipment"]}\n'
                                 f'Supporting measurements: {c["supporting"]}\nActivated: {iso_time(c["activatedT"])}, occurrences: {c["occurrences"]}')

    def refresh(self):
        current = self.engine.alarms.current()
        self.keys = [c["key"] for c in current]
        self.current.set_rows([[c["color"].upper(), alarms.LABELS[c["classification"]], c["severity"], c["source"], c["channel"], number_text(c["value"]), c["expected"], iso_time(c["activatedT"]),
                                _STATE_TEXT[c["state"]], c["occurrences"], c["reason"]] for c in current], {0: [c["color"] for c in current]})
        events = list(reversed(self.engine.alarms.history[-200:]))
        self.history.set_rows([[e["time"], e["event"], e["color"].upper(), e["classification"], e["key"], number_text(e["value"]), e["detail"]] for e in events], {2: [e["color"] for e in events]})


class DiagnosticsPage(Page):
    title = "Diagnostics"
    hint = "Each kind of health is reported on its own. A working link says nothing about the instrument or the rig."

    def __init__(self, app):
        super().__init__(app)
        self.overall = QLabel()
        self.equipment = QLabel()
        self.equipment.setWordWrap(True)
        grid = QGridLayout()
        grid.addWidget(heading("Overall system status"), 0, 0)
        grid.addWidget(heading("Equipment health"), 0, 1)
        grid.addWidget(self.overall, 1, 0)
        grid.addWidget(self.equipment, 1, 1)
        grid.addWidget(heading("Communication health"), 2, 0)
        grid.addWidget(heading("Instrument health"), 2, 1)
        self.comm = DataTable(["Device", "Comm", "Detail", "Packets ok", "Packet errors", "Reconnects"])
        self.instrument = DataTable(["Device", "Instrument", "Evidence"])
        grid.addWidget(self.comm, 3, 0)
        grid.addWidget(self.instrument, 3, 1)
        self.root.addLayout(grid, 1)
        self.root.addWidget(heading("Measurement quality"))
        self.quality = DataTable(["Channel", "Latest", "Age (s)", "Quality", "Flags"])
        self.root.addWidget(self.quality, 2)
        self.root.addWidget(heading("Application"))
        self.software = QPlainTextEdit(readOnly=True)
        self.software.setMaximumHeight(70)
        self.root.addWidget(self.software)

    def refresh(self):
        h = compute_health(self.engine)
        names = {"green": "green", "yellow": "yellow", "red": "red", "no_data": "grey"}
        self.overall.setText(h["overall"].replace("_", " ").upper())
        self.overall.setStyleSheet(pill_style(names[h["overall"]]))
        self.equipment.setText(f'{h["equipment"]["state"]}: {h["equipment"]["detail"]}')
        d = h["devices"]
        self.comm.set_rows([[x["device"], x["comm"], x["commDetail"], x["packetsOk"], x["packetErrors"], x["reconnects"]] for x in d],
                           {1: [{"ok": "green", "degraded": "yellow", "lost": "red", "offline": "grey"}[x["comm"]] for x in d]})
        self.instrument.set_rows([[x["device"], x["instrument"], x["instrumentDetail"]] for x in d],
                                 {1: [{"ok": "green", "degraded": "yellow", "fault": "red", "not_assessed": "grey"}.get(x["instrument"], "") for x in d]})
        c = h["channels"]
        self.quality.set_rows([[x["channel"], f'{x["value"]:.5g}', number_text(round(x["age"], 3)) if not math.isnan(x["age"]) else "", x["quality"], x["flags"]] for x in c],
                              {3: [{"good": "green", "suspect": "yellow", "no_data": "grey"}[x["quality"]] for x in c]})
        e = self.engine
        self.software.setPlainText(f"Ticks: {e.tick_count}   last: {e.last_tick_s * 1e3:.1f} ms   max: {e.max_tick_s * 1e3:.1f} ms\n"
                                   f"Loop errors: {e.loop_errors}   {e.last_loop_error}\nPlugin problems: {'; '.join(e.registry.problems)}")


class EnginePage(Page):
    title = "Engine"
    hint = "Compression ratio values always show how they are known. The application never assumes an engine has variable compression."

    def __init__(self, app):
        super().__init__(app)
        body = QHBoxLayout()
        self.root.addLayout(body, 1)
        panel = QGroupBox("Engine data")
        panel.setMaximumWidth(380)
        form = QFormLayout(panel)
        cfg = opt(self.engine.setup, "engine", {})
        geometry = Geometry.from_dict(cfg.get("geometry"))
        cr = opt(cfg, "compressionRatio", {})
        cmap = opt(cfg, "map", {})
        field_of = lambda value: QLineEdit(number_text(value))
        self.bore, self.stroke, self.conrod = field_of(geometry.bore_mm), field_of(geometry.stroke_mm), field_of(geometry.con_rod_mm)
        self.cylinders, self.strokes, self.clearance = field_of(geometry.cylinders), field_of(geometry.strokes_per_cycle), field_of(geometry.clearance_cm3)
        self.swept = QLabel("--")
        self.mode = QComboBox()
        self.mode.addItems(["unknown", "fixed", "measured", "manual", "calculated"])
        self.mode.setCurrentText(opt(cr, "mode", "unknown"))
        self.nominal, self.min_cr, self.max_cr = field_of(number(cr.get("nominal"))), field_of(number(cr.get("min"))), field_of(number(cr.get("max")))
        ids = ["(none)"] + self.engine.channels.ids()

        def pick(key):
            combo = QComboBox()
            combo.addItems(ids)
            value = opt(cmap, key, "(none)")
            combo.setCurrentText(value if value in ids else "(none)")
            return combo
        self.cr_channel, self.rpm, self.torque, self.power = pick("crChannel"), pick("rpm"), pick("torque"), pick("power")
        for label, control in (("Bore (mm)", self.bore), ("Stroke (mm)", self.stroke), ("Con-rod (mm)", self.conrod), ("Cylinders", self.cylinders), ("Strokes per cycle", self.strokes),
                               ("Clearance (cm3)", self.clearance), ("Swept volume", self.swept), ("Compression ratio source", self.mode), ("Nominal / fixed CR", self.nominal), ("Minimum CR", self.min_cr),
                               ("Maximum CR", self.max_cr), ("CR channel", self.cr_channel), ("Speed channel", self.rpm), ("Torque channel", self.torque), ("Power channel", self.power)):
            form.addRow(label, control)
        form.addRow(hbox(self.b("Apply", self.apply, primary=True), self.b("Enter manual CR", self.manual_entry)))
        body.addWidget(panel)
        right = QVBoxLayout()
        body.addLayout(right, 1)
        self.live = heading("Compression ratio: unknown", "pageTitle")
        right.addWidget(self.live)
        self.axis = QComboBox()
        self.axis.addItems(["Time", "Speed", "Torque", "Brake power"])
        self.note = QLabel()
        self.note.setObjectName("hint")
        right.addLayout(hbox(QLabel("Plot against"), self.axis, self.b("Load recorded run", self.load_recorded), self.b("Back to live", self.show_live), self.note))
        tokens = THEMES[app.theme]
        self.plot = pg.PlotWidget(background=tokens["surface"])
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "Compression ratio", color=tokens["muted"])
        self.curve = self.plot.plot([], [], pen=pg.mkPen(tokens["series"][0], width=2))
        right.addWidget(self.plot, 1)
        self.manager = CompressionRatio()
        self.recorded = None
        self.apply()

    def apply(self):
        n = lambda edit, label: forms.number_of(edit.text(), label)
        geometry = Geometry(n(self.bore, "Bore"), n(self.stroke, "Stroke"), n(self.conrod, "Con-rod"), n(self.cylinders, "Cylinders"), n(self.strokes, "Strokes"), n(self.clearance, "Clearance"))
        channel = "" if self.cr_channel.currentText() == "(none)" else self.cr_channel.currentText()
        config = {"mode": self.mode.currentText(), "nominal": n(self.nominal, "Nominal"), "min": n(self.min_cr, "Minimum"), "max": n(self.max_cr, "Maximum"), "channelId": channel}
        self.manager = CompressionRatio(config, geometry)
        swept = geometry.swept_cm3()
        self.swept.setText(f"{swept:.1f} cm3 per cylinder" if math.isfinite(swept) else "unknown (enter bore and stroke)")
        self.engine.setup["engine"] = {"geometry": geometry.to_dict(), "compressionRatio": config,
                                       "map": {"crChannel": self.cr_channel.currentText(), "rpm": self.rpm.currentText(), "torque": self.torque.currentText(), "power": self.power.currentText()}}

    def manual_entry(self):
        values = forms.ask(self, "Manual compression ratio", [("Compression ratio", ""), ("Note", "")])
        if values:
            t, value = self.engine.clock.now(), float(values[0])
            self.manager.enter_manual(value, t, values[1])
            if self.engine.recorder:
                self.engine.recorder.event({"t": t, "time": iso_time(t), "event": "cr_manual", "value": value, "provenance": "manual", "detail": values[1]})

    def load_recorded(self):
        folder = QFileDialog.getExistingDirectory(self, "Select a run folder", str(paths.WORKSPACE))
        if folder:
            others = {name: combo.currentText() for name, combo in (("rpm", self.rpm), ("torque", self.torque), ("power", self.power)) if combo.currentText() != "(none)"}
            if not others:
                raise ValueError("Select at least the speed channel.")
            self.recorded = self.manager.series(RunReader(folder), others)

    def show_live(self):
        self.recorded = None

    def refresh(self):
        if self.recorded is not None and len(self.recorded):
            self._plot_recorded()
            return
        result = self.manager.current(lambda channel: self.engine.latest(channel)[0])
        self.live.setText(f'Compression ratio: unknown ({result["provenance"]})' if math.isnan(result["value"]) else f'Compression ratio: {result["value"]:.2f} ({result["provenance"]})')
        channel = self.cr_channel.currentText()
        if self.mode.currentText() != "measured" or channel == "(none)":
            self.note.setText("Plots need a measured compression ratio channel.")
            self.curve.setData([], [])
            return
        self.note.setText("Measured, latest 120 s.")
        t, cr, _ = self.engine.history(channel, 120)
        if self.axis.currentText() == "Time":
            self.curve.setData(t - self.engine.clock.now(), cr)
            self.plot.setLabel("bottom", "Seconds before now")
            return
        source = {"Speed": self.rpm, "Torque": self.torque, "Brake power": self.power}[self.axis.currentText()].currentText()
        self.plot.setLabel("bottom", self.axis.currentText())
        if source == "(none)":
            self.note.setText("Select that channel on the left.")
            return
        tx, x, _ = self.engine.history(source, 120)
        if tx.size >= 2 and t.size:
            order = np.argsort(tx)
            self.curve.setData(x[order][np.clip(np.searchsorted(tx[order], t, side="right") - 1, 0, tx.size - 1)], cr)

    def _plot_recorded(self):
        table = self.recorded
        self.note.setText(f'Recorded run. Provenance: {table["provenance"].iloc[0]}')
        column = {"Speed": "rpm", "Torque": "torque", "Brake power": "power"}.get(self.axis.currentText())
        if column is None:
            self.curve.setData((table["t"] - table["t"].iloc[0]).to_numpy(), table["compression_ratio"].to_numpy())
        elif column in table:
            self.curve.setData(table[column].to_numpy(), table["compression_ratio"].to_numpy())
        else:
            self.note.setText("That channel was not selected when the run was loaded.")


def _all_run_folders():
    found = {file.parent for pattern in ("manifest.py", "manifest.m") for file in paths.WORKSPACE.rglob(pattern) if ".venv" not in file.parts}
    return sorted(found)


class DataPage(Page):
    title = "Data"

    def __init__(self, app):
        super().__init__(app)
        columns = QHBoxLayout()
        self.root.addLayout(columns, 1)
        left = QGroupBox("Export")
        form = QFormLayout(left)
        self.runs = QListWidget()
        self.runs.setMaximumHeight(120)
        self.runs.currentItemChanged.connect(guard(self, self.load_run))
        self.channels = QListWidget()
        self.channels.setSelectionMode(QListWidget.MultiSelection)
        self.from_s, self.to_s = QDoubleSpinBox(maximum=1e9), QDoubleSpinBox(maximum=1e9)
        self.content = QComboBox()
        self.content.addItems(["processed", "raw", "both"])
        self.flags = QCheckBox(checked=True)
        self.format = QComboBox()
        self.format.addItems(list(exchange.DATA_FORMATS) + list(exchange.FIGURE_FORMATS))
        self.result = QLabel(wordWrap=True)
        for label, control in (("Run", self.runs), ("Channels", self.channels), ("From (s, run time)", self.from_s), ("To (s, 0 = end)", self.to_s), ("Content", self.content),
                               ("Include quality flags", self.flags), ("Format", self.format)):
            form.addRow(label, control)
        form.addRow(hbox(self.b("Export", self.do_export, primary=True), self.b("Refresh runs", self.list_runs)))
        form.addRow(self.result)
        columns.addWidget(left, 1)
        right = QGroupBox("Import")
        box = QVBoxLayout(right)
        self.path = QLineEdit()
        self.path.setPlaceholderText("File path")
        box.addLayout(hbox(self.path, self.b("Browse", self.browse), self.b("Read columns", self.read_columns), stretch_end=False))
        self.time_column = QComboBox()
        self.time_column.addItem("(none)")
        self.time_kind = QComboBox()
        self.time_kind.addItems(["posix", "seconds", "datetime"])
        self.rate = QDoubleSpinBox(minimum=1e-6, maximum=1e7, value=1)
        box.addLayout(hbox(QLabel("Time column"), self.time_column, self.time_kind, QLabel("Rate if no time (Hz)"), self.rate))
        self.map_fields = [field("column", "Column"), field("use", "Import", "bool"), field("id", "Channel id"), field("name", "Name"), field("unit", "Unit"), field("quantity", "Quantity")]
        self.mapping = SpecTable(self.map_fields, [], 0)
        box.addWidget(self.mapping, 1)
        box.addLayout(hbox(self.b("Import", self.do_import, primary=True)))
        self.import_result = QLabel(wordWrap=True)
        box.addWidget(self.import_result)
        columns.addWidget(right, 1)
        self.reader = None
        guard(self, self.list_runs)()      # a damaged run folder must not block startup

    def list_runs(self):
        self.runs.clear()
        for folder in _all_run_folders():
            item = QListWidgetItem(str(folder.relative_to(paths.WORKSPACE)))
            item.setData(Qt.UserRole, str(folder))
            self.runs.addItem(item)
        if self.runs.count():
            self.runs.setCurrentRow(self.runs.count() - 1)

    def load_run(self):
        item = self.runs.currentItem()
        if item is None:
            return
        self.reader = RunReader(item.data(Qt.UserRole))
        self.channels.clear()
        self.channels.addItems(self.reader.channel_ids())
        self.channels.selectAll()

    def do_export(self):
        if self.reader is None:
            raise ValueError("Select a run first.")
        t0, value = self.reader.start_t, None
        if self.from_s.value() > 0 or self.to_s.value() > 0:
            value = (t0 + self.from_s.value(), t0 + self.to_s.value() if self.to_s.value() > 0 else math.inf)
        ids = [item.text() for item in self.channels.selectedItems()]
        path = paths.EXPORTS / f"{self.reader.folder.name}.{self.format.currentText()}"
        if self.format.currentText() in exchange.FIGURE_FORMATS:
            exchange.export_figure(self.reader, path, ids[:8], value, self.reader.folder.name)
        else:
            exchange.export(self.reader, path, ids, value, self.content.currentText(), self.flags.isChecked())
        self.result.setText(f"Wrote {path}")

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import", str(paths.WORKSPACE), "Data files (*.csv *.txt *.xlsx *.xls *.json *.mat *.h5 *.hdf5 *.parquet)")
        if path:
            self.path.setText(path)
            self.read_columns()

    def read_columns(self):
        table = exchange.preview(self.path.text())
        self.time_column.clear()
        self.time_column.addItems(["(none)"] + [str(c) for c in table.columns])
        import pandas as pd
        numeric = [str(c) for c in table.columns if pd.api.types.is_numeric_dtype(table[c])]
        self.mapping.set_specs([{"column": c, "use": True, "id": re.sub(r"\W", "_", c), "name": c, "unit": "-", "quantity": "Dimensionless"} for c in numeric], 0)

    def do_import(self):
        channels = [spec for spec in self.mapping.specs() if spec["use"]]
        mapping = {"channels": channels}
        if self.time_column.currentText() == "(none)":
            mapping["sampleRateHz"] = self.rate.value()
        else:
            mapping.update(time=self.time_column.currentText(), timeKind=self.time_kind.currentText())
        destination = paths.RUNS / new_id("import")
        exchange.ingest(self.path.text(), mapping, destination)
        self.import_result.setText(f"Imported to {destination}")
        self.list_runs()


class ExperimentsPage(Page):
    title = "Experiments"

    def __init__(self, app):
        super().__init__(app)
        self.store = app.experiments
        columns = QHBoxLayout()
        self.root.addLayout(columns, 1)

        panel = QGroupBox("Experiment")
        box = QVBoxLayout(panel)
        self.list = QListWidget()
        self.list.setMaximumHeight(110)
        self.list.currentItemChanged.connect(guard(self, self.show))
        box.addWidget(self.list)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Experiment name")
        box.addWidget(self.name)
        self.sheets = QTabWidget()
        self.fields = {"engines": [field("name", "Engine"), field("notes", "Notes")], "fuels": [field("name", "Fuel"), field("lhvMJkg", "LHV (MJ/kg)", "number"), field("notes", "Notes")],
                       "methods": [field("name", "Test method"), field("notes", "Notes")], "parameters": [field("name", "Parameter"), field("value", "Value")]}
        self.tables = {}
        for key, fields_ in self.fields.items():
            self.tables[key] = SpecTable(fields_, [], 8)
            self.sheets.addTab(self.tables[key], key.capitalize())
        box.addWidget(self.sheets, 1)
        box.addLayout(hbox(self.b("Create new", self.create, primary=True), self.b("Save changes", self.save_changes)))
        columns.addWidget(panel, 11)

        panel = QGroupBox("Test run")
        box = QVBoxLayout(panel)
        form = QFormLayout()
        self.engine_choice, self.fuel_choice, self.method_choice = QComboBox(), QComboBox(), QComboBox()
        for combo in (self.engine_choice, self.fuel_choice, self.method_choice):
            combo.addItem("(none)")
        self.operator = QLineEdit()
        for label, control in (("Engine", self.engine_choice), ("Fuel", self.fuel_choice), ("Method", self.method_choice), ("Operator", self.operator)):
            form.addRow(label, control)
        box.addLayout(form)
        box.addWidget(heading("Test conditions"))
        self.condition_fields = [field("name", "Condition"), field("value", "Value")]
        self.conditions = SpecTable(self.condition_fields, [], 8)
        box.addWidget(self.conditions, 1)
        box.addLayout(hbox(self.b("Start recording run", self.start_run, primary=True), self.b("Stop recording", self.engine.stop_recording)))
        self.status = QLabel("Not recording", wordWrap=True)
        box.addWidget(self.status)
        columns.addWidget(panel, 9)

        panel = QGroupBox("Report")
        box = QVBoxLayout(panel)
        self.run_list = QListWidget()
        self.run_list.setMaximumHeight(80)
        box.addWidget(self.run_list)
        self.report_title = QLineEdit("Test report")
        self.from_s, self.to_s = QDoubleSpinBox(maximum=1e9), QDoubleSpinBox(maximum=1e9)
        self.to_s.setToolTip("0 means the end of the run")
        box.addLayout(hbox(QLabel("Title"), self.report_title, stretch_end=False))
        box.addLayout(hbox(QLabel("From (s)"), self.from_s, QLabel("To (s)"), self.to_s, stretch_end=False))
        box.addWidget(heading("Sections appear in this order"))
        self.section_fields = [field("section", "Section", "choice", ["", "summary", "channels", "statistics", "plot", "alarms", "text"]), field("include", "Use", "bool"), field("heading", "Heading"),
                               field("channels", "Channels"), field("units", "Units (a=psi)"), field("body", "Text")]
        self.sections = SpecTable(self.section_fields, [{"section": s, "include": True} for s in ("summary", "channels", "statistics", "plot", "alarms")], 5)
        box.addWidget(self.sections, 1)
        self.template_name = QLineEdit()
        self.template_name.setPlaceholderText("Template name")
        self.template_picker = QComboBox()
        self.template_picker.activated.connect(guard(self, self.load_template))
        box.addLayout(hbox(self.template_name, self.b("Save", self.save_template), self.template_picker, stretch_end=False))
        box.addLayout(hbox(self.b("Build PDF report", self.report, primary=True)))
        columns.addWidget(panel, 12)
        self.populate()
        self.list_templates()

    def populate(self):
        self.list.blockSignals(True)
        self.list.clear()
        for experiment_id in self.store.list():
            item = QListWidgetItem(str(opt(self.store.load(experiment_id), "name", experiment_id)))
            item.setData(Qt.UserRole, experiment_id)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(self.list.count() - 1)
        self.list.blockSignals(False)
        self.show()

    def _experiment_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def show(self):
        experiment_id = self._experiment_id()
        self.run_list.clear()
        if experiment_id is None:
            return
        spec = self.store.load(experiment_id)
        self.name.setText(str(opt(spec, "name", "")))
        for key, table in self.tables.items():
            table.set_specs(to_list(opt(spec, key, [])), 8)
        for combo, key in ((self.engine_choice, "engines"), (self.fuel_choice, "fuels"), (self.method_choice, "methods")):
            combo.clear()
            combo.addItems(["(none)"] + [str(r["name"]) for r in to_list(opt(spec, key, []))])
        for folder in self.store.runs(experiment_id):
            item = QListWidgetItem(folder.name)
            item.setData(Qt.UserRole, str(folder))
            self.run_list.addItem(item)
        if self.run_list.count():
            self.run_list.setCurrentRow(self.run_list.count() - 1)

    def read_form(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError("Enter a name for the experiment.")
        return {"name": name, **{key: table.specs() for key, table in self.tables.items()}}

    def create(self):
        self.store.create(self.read_form())
        self.populate()

    def save_changes(self):
        if self._experiment_id() is None:
            raise ValueError("Select an experiment first.")
        self.store.save({**self.store.load(self._experiment_id()), **self.read_form()})
        self.populate()

    def start_run(self):
        if self._experiment_id() is None:
            raise ValueError("Create or select an experiment first.")
        if not self.engine.running:
            raise ValueError("Start monitoring first.")
        self.store.start_run(self.engine, self._experiment_id(), {"engine": self.engine_choice.currentText(), "fuel": self.fuel_choice.currentText(), "method": self.method_choice.currentText(),
                                                                  "operator": self.operator.text(), "conditions": self.conditions.specs()})
        self.show()

    def list_templates(self):
        self.template_picker.clear()
        self.template_picker.addItem("Load template...", "")
        for file in sorted(paths.TEMPLATES.glob("*.py")):
            self.template_picker.addItem(file.stem, file.stem)

    def save_template(self):
        name = self.template_name.text().strip()
        if not name:
            raise ValueError("Enter a template name.")
        experiment.save_template({"title": self.report_title.text(), "rows": self.sections.specs()}, name)
        self.list_templates()

    def load_template(self):
        name = self.template_picker.currentData()
        if name:
            template = experiment.load_template(name)
            self.report_title.setText(template["title"])
            self.sections.set_specs(to_list(template["rows"]), 5)

    def report(self):
        item = self.run_list.currentItem()
        if item is None:
            raise ValueError("Select a run first.")
        reader = RunReader(item.data(Qt.UserRole))
        value = None
        if self.from_s.value() > 0 or self.to_s.value() > 0:
            value = [reader.start_t + self.from_s.value(), reader.start_t + self.to_s.value() if self.to_s.value() > 0 else math.inf]
        path = paths.EXPORTS / f"{reader.folder.name}_report.pdf"
        experiment.build_report(reader, forms.template_from_rows(self.report_title.text(), self.sections.specs(), value), path, self.engine.units)
        self.status.setText(f"Report: {path}")

    def refresh(self):
        if self.engine.recorder:
            self.status.setText(f"Recording: {self.engine.recorder.sample_count} samples to {self.engine.recorder.folder}")
        elif not self.status.text().startswith("Report"):
            self.status.setText("Not recording")


class SimulationPage(Page):
    title = "Simulation"
    hint = "Rehearse faults on the simulated rig. Each scenario here is part of the automated test suite."

    def __init__(self, app):
        super().__init__(app)
        self.root.addLayout(hbox(self.b("Inject selected", self.inject, primary=True), self.b("Clear injected faults", self.clear)))
        self.table = DataTable(["Scenario", "Description", "Expected state", "Expected class", "Expected alarms"])
        self.table.set_rows([[s["id"], s["title"], s["color"].upper(), ", ".join(alarms.LABELS[c] for c in s["classes"]), ", ".join(s["keys"])] for s in sim.SCENARIOS],
                            {2: [s["color"] for s in sim.SCENARIOS]})
        self.root.addWidget(self.table, 1)
        self.log = QLabel("Requires a running simulator device.")
        self.log.setObjectName("hint")
        self.root.addWidget(self.log)

    def _simulator(self):
        for session in self.engine.devices.sessions:
            if hasattr(session.driver, "inject") and self.engine.running:
                return session
        raise ValueError("Start monitoring with a simulator device first.")

    def inject(self):
        scenario = sim.SCENARIOS[self.selected_or_fail(self.table, "a scenario")]
        sim.inject(self.engine, scenario, self._simulator().id, self.engine.clock.now())
        self.log.setText(f'Injected: {scenario["title"]}. Expected state: {scenario["color"].upper()}')

    def clear(self):
        self._simulator().driver.clear_faults()
        self.log.setText("Faults cleared. Latched alarms still need acknowledging.")


_MATRIX_COLORS = {"bench_validated": "green", "loopback_verified": "blue", "implemented": "yellow", "simulated": "grey", "planned": "grey", "deprecated": "grey", "unsupported": "red"}


class CompatibilityPage(Page):
    title = "Compatibility"
    hint = "A row only reaches bench_validated with recorded evidence. Changing a driver major version flags it for revalidation."

    def __init__(self, app):
        super().__init__(app)
        self.root.addLayout(hbox(self.b("Add lab equipment", self.add_device, primary=True), self.b("Record bench validation", self.validate), self.b("Check matrix", self.check), self.b("Write matrix document", self.write_doc)))
        self.root.addWidget(heading("Interfaces (live availability on this computer)"))
        self.interfaces = DataTable(["Interface", "Driver", "Status", "This host", "Notes"])
        self.root.addWidget(self.interfaces, 2)
        self.root.addWidget(heading("Laboratory equipment"))
        self.equipment = DataTable(["Id", "Manufacturer", "Model", "Category", "Interface", "Driver", "Status", "Last validated", "Notes"])
        self.root.addWidget(self.equipment, 1)
        self.log = QLabel()
        self.log.setObjectName("hint")
        self.log.setWordWrap(True)
        self.root.addWidget(self.log)

    def add_device(self):
        v = forms.ask(self, "Add lab equipment", [("Id", ""), ("Manufacturer", ""), ("Model", ""), ("Category (DAQ, sensor, dyno, ...)", ""), ("Interface", "RS-232"), ("Driver id", "stream"), ("Status: implemented, planned or unsupported", "implemented"), ("Notes", "")])
        if v:
            self.engine.matrix.add_device(dict(zip(("id", "manufacturer", "model", "category", "interface", "driver", "status", "notes"), v)))
            self.engine.matrix.save()

    def validate(self):
        row = self.engine.matrix.device_rows(self.engine.registry)[self.selected_or_fail(self.equipment, "a device row")]
        version = self.engine.registry.entry(row["Driver"])["manifest"]["version"] if self.engine.registry.has(row["Driver"]) else ""
        v = forms.ask(self, f'Bench validation: {row["Id"]}', [("Date", date.today().isoformat()), ("Validated by", ""), ("Firmware", ""), ("Driver version", version), ("Evidence (run id, certificate, reference used)", ""), ("Result: pass or fail", "pass")])
        if v:
            self.engine.matrix.record_validation(row["Id"], dict(zip(("date", "by", "firmware", "driverVersion", "evidence", "result"), v)))
            self.engine.matrix.save()

    def check(self):
        problems = self.engine.matrix.validate(self.engine.registry)
        self.log.setText("\n".join(problems) or "Matrix agrees with the installed drivers.")

    def write_doc(self):
        self.engine.matrix.write_markdown(self.engine.registry)
        self.log.setText("Wrote docs/HARDWARE_COMPATIBILITY.md")

    def refresh(self):
        interfaces = self.engine.matrix.interface_rows(self.engine.registry)
        self.interfaces.set_rows([list(r.values()) for r in interfaces], {2: [_MATRIX_COLORS.get(r["Status"], "") for r in interfaces]})
        devices = self.engine.matrix.device_rows(self.engine.registry)
        self.equipment.set_rows([list(r.values()) for r in devices], {6: [_MATRIX_COLORS.get(r["Status"].split(" ")[0], "") for r in devices]})
