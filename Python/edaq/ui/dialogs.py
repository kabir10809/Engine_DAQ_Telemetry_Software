"""Modal editors: widget, layout, channel, cross-channel rules, device wizard
and the setup library. Replacing or deleting a setup takes a second press."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QLabel, QLineEdit, QListWidget,
                               QPlainTextEdit, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import alarms, codefile, paths, validation
from ..channels.channel import Channel
from ..hw import discovery
from ..util import opt, to_list
from . import widgets as W
from .forms import SpecTable, button, field, from_rows, guard, heading, hbox, number_list, number_of, number_text
from .theme import THEMES


class WidgetEditor(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle("Add widget")
        self.resize(420, 460)
        form = QFormLayout(self)
        self.type = QComboBox()
        self.type.addItems(W.TYPES)
        self.title = QLineEdit("New widget")
        self.channels = QListWidget()
        self.channels.setSelectionMode(QListWidget.MultiSelection)
        self.channels.addItems(page.app.engine.channels.ids())
        self.window_s = QSpinBox()
        self.window_s.setRange(1, 3600)
        self.window_s.setValue(60)
        form.addRow("Type", self.type)
        form.addRow("Title", self.title)
        form.addRow("Channels", self.channels)
        form.addRow("Window (s)", self.window_s)
        form.addRow(button("Add", self.add, primary=True, owner=self))

    def add(self):
        kind = self.type.currentText()
        chosen = [item.text() for item in self.channels.selectedItems()]
        if kind == "xy" and len(chosen) != 2:
            raise ValueError("An XY plot needs exactly two channels.")
        if kind == "gauge" and len(chosen) != 1:
            raise ValueError("A gauge shows exactly one channel.")
        if kind != "alarms" and not chosen:
            raise ValueError("Select at least one channel.")
        existing = self.page.page_widgets()
        spec = {"type": kind, "title": self.title.text(), "channels": chosen, "windowS": self.window_s.value(), "row": 1, "col": 1, "rowSpan": 2, "colSpan": 4}
        self.page.set_widgets(existing + [W.placed(spec, existing)])
        self.accept()


class LayoutCanvas(QWidget):
    """Drag a block to move it; drag its corner to resize."""
    HANDLE = 14

    def __init__(self, widgets_, tokens):
        super().__init__()
        self.widgets, self.tokens = widgets_, tokens
        self.selected = None
        self.drag = None
        self.setMinimumSize(720, 480)

    def _cell(self):
        return self.width() / W.COLS, self.height() / W.ROWS

    def _rect(self, w):
        cw, ch = self._cell()
        return QRectF((w["col"] - 1) * cw + 2, (w["row"] - 1) * ch + 2, w["colSpan"] * cw - 4, w["rowSpan"] * ch - 4)

    def paintEvent(self, _):
        p = QPainter(self)
        cw, ch = self._cell()
        p.setPen(QPen(QColor(self.tokens["grid"])))
        for c in range(W.COLS + 1):
            p.drawLine(QPointF(c * cw, 0), QPointF(c * cw, self.height()))
        for r in range(W.ROWS + 1):
            p.drawLine(QPointF(0, r * ch), QPointF(self.width(), r * ch))
        for k, w in enumerate(self.widgets):
            rect = self._rect(w)
            p.setPen(QPen(QColor(self.tokens["accent"] if k == self.selected else self.tokens["border"]), 2))
            p.setBrush(QColor(self.tokens["selection"] if k == self.selected else self.tokens["raised"]))
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(QColor(self.tokens["text"]))
            p.drawText(rect.adjusted(8, 6, -8, -6), Qt.AlignTop | Qt.AlignLeft, f'{w["type"]}\n{w["title"]}')
            p.fillRect(QRectF(rect.right() - self.HANDLE, rect.bottom() - self.HANDLE, self.HANDLE, self.HANDLE), QColor(self.tokens["accent"]))

    def press_at(self, point):
        for k in reversed(range(len(self.widgets))):
            rect = self._rect(self.widgets[k])
            if rect.contains(point):
                self.selected = k
                resize = point.x() > rect.right() - self.HANDLE and point.y() > rect.bottom() - self.HANDLE
                self.drag = {"mode": "resize" if resize else "move", "k": k, "start": point, "origin": self.widgets[k]}
                break
        self.update()

    def move_to(self, point):
        if self.drag:
            cw, ch = self._cell()
            dc, dr = (point.x() - self.drag["start"].x()) / cw, (point.y() - self.drag["start"].y()) / ch
            w = self.drag["origin"]
            self.widgets[self.drag["k"]] = W.moved(w, w["row"] + dr, w["col"] + dc) if self.drag["mode"] == "move" \
                else W.resized(w, w["rowSpan"] + dr, w["colSpan"] + dc)
            self.update()

    def mousePressEvent(self, event):
        self.press_at(event.position())

    def mouseMoveEvent(self, event):
        self.move_to(event.position())

    def mouseReleaseEvent(self, _):
        self.drag = None


class LayoutEditor(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle("Edit layout: drag to move, corner to resize")
        layout = QVBoxLayout(self)
        self.canvas = LayoutCanvas(page.page_widgets(), THEMES[page.app.theme])
        layout.addWidget(self.canvas, 1)
        layout.addLayout(hbox(button("Delete selected", self.remove_selected, owner=self), button("Apply", self.apply, primary=True, owner=self)))

    def remove_selected(self):
        if self.canvas.selected is not None:
            del self.canvas.widgets[self.canvas.selected]
            self.canvas.selected = None
            self.canvas.update()

    def apply(self):
        if W.has_overlap(self.canvas.widgets):
            raise ValueError("Widgets overlap. Move them apart first.")
        self.page.set_widgets(self.canvas.widgets)
        self.accept()


RULE_FIELDS = [field("type", "Rule", "choice", ["", "roc", "stuck", "noise", "outlier", "drift", "interval"]), field("windowS", "Window (s)", "number"),
               field("maxRate", "Max rate", "number"), field("maxStd", "Max noise", "number"), field("tolerance", "Tolerance", "number"),
               field("k", "Outlier k", "number"), field("minScale", "Min scale", "number"), field("maxSlope", "Max slope", "number"),
               field("maxFraction", "Max fraction", "number"), field("persistenceS", "Persistence (s)", "number"), field("clearDelayS", "Clear delay (s)", "number"),
               field("severity", "Severity", "choice", ["", "warning", "critical"]), field("classification", "Classification", "choice", [""] + list(alarms.CLASSES)),
               field("recommendation", "Recommendation")]


class ChannelEditor(QDialog):
    """Every channel property, grouped into tabs."""

    def __init__(self, app, existing=None):
        super().__init__(app)
        self.app, self.existing = app, existing
        self.setWindowTitle("Channel")
        self.resize(860, 620)
        engine = app.engine
        channel = existing or Channel(id="new_channel", name="New channel")
        s = channel.to_dict()
        if existing is None:
            s["id"] = ""
        self.s = s
        self.controls = {}
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        general = self._grid(tabs, "General")
        self._row(general, 0, 0, "Identifier", "id", self._text(s["id"]))
        self._row(general, 0, 1, "Display name", "name", self._text(s["name"]))
        self._row(general, 1, 0, "Kind", "kind", self._choice(s["kind"], ["hardware", "derived", "manual"]))
        self._row(general, 1, 1, "Role", "role", self._choice(s["role"], ["measurement", "fault_signal", "interlock"]))
        self._row(general, 2, 0, "Sensor type", "sensorType", self._text(s["sensorType"]))
        self._row(general, 2, 1, "Equipment", "equipment", self._text(s["equipment"]))
        self._row(general, 3, 0, "Device", "deviceId", self._choice(s["deviceId"], [d.id for d in engine.devices.sessions], editable=True))
        self._row(general, 3, 1, "Hardware channel", "hwChannel", self._text(s["hwChannel"]))
        self.controls["expression"] = self._text(s["expression"])
        self.controls["expression"].setPlaceholderText("For derived channels, e.g. 2*pi*rpm*torque/60000")
        general.addWidget(QLabel("Formula"), 4, 0)
        general.addWidget(self.controls["expression"], 4, 1, 1, 3)
        self.critical = QCheckBox("Critical (losing it is red)", checked=s["critical"])
        self.enabled_box = QCheckBox("Enabled", checked=s["enabled"])
        general.addWidget(self.critical, 5, 1)
        general.addWidget(self.enabled_box, 5, 3)
        self.controls["kind"].currentTextChanged.connect(lambda *_: self._kind_changed())

        scale = self._grid(tabs, "Measurement and limits")
        quantities = list(dict.fromkeys([s["quantity"]] + engine.units.quantities()))
        self._row(scale, 0, 0, "Quantity", "quantity", self._choice(s["quantity"], quantities))
        self._row(scale, 0, 1, "Unit", "unit", self._choice(s["unit"], engine.units.units_for(s["quantity"])))
        self.controls["quantity"].currentTextChanged.connect(lambda *_: self._quantity_changed())
        self._row(scale, 1, 0, "Raw signal unit", "rawUnit", self._text(s["rawUnit"]))
        layout = [("Sample rate (Hz)", "sampleRateHz"), ("Sensor range: low", "rangeMin"), ("Sensor range: high", "rangeMax"), ("Accuracy", "accuracy"),
                  ("Resolution", "resolution"), ("Warning low", "warnLow"), ("Warning high", "warnHigh"), ("Critical low", "critLow"), ("Critical high", "critHigh"), ("Asserted level", "activeValue")]
        for k, (label, key) in enumerate(layout, start=3):
            self._row(scale, k // 2, k % 2, label, key, self._text(number_text(float(s[key]))))

        cal = s["calibration"]
        page = QWidget()
        tabs.addTab(page, "Calibration")
        box = QVBoxLayout(page)
        grid = QGridLayout()
        box.addLayout(grid)
        self._row(grid, 0, 0, "Type", "calType", self._choice(cal["type"], ["none", "linear", "polynomial", "table"]))
        self._row(grid, 0, 1, "Coefficients", "calCoefficients", self._text(", ".join(f"{c:g}" for c in cal["coefficients"])))
        self.controls["calCoefficients"].setToolTip("Linear: gain, offset. Polynomial: constant term first.")
        self._row(grid, 1, 0, "Calibrated raw: low", "calRawMin", self._text(number_text(cal["rawMin"])))
        self._row(grid, 1, 1, "Calibrated raw: high", "calRawMax", self._text(number_text(cal["rawMax"])))
        self._row(grid, 2, 0, "Certificate", "calCertificate", self._text(cal["certificate"]))
        self._row(grid, 2, 1, "Calibrated by", "calBy", self._text(cal["calibratedBy"]))
        self._row(grid, 3, 0, "Calibrated on", "calOn", self._text(cal["calibratedOn"]))
        self._row(grid, 3, 1, "Due on", "calDue", self._text(cal["dueOn"]))
        box.addWidget(heading("Table calibration points (raw and value)"))
        self.point_fields = [field("raw", "Raw", "number"), field("value", "Value", "number")]
        self.points = SpecTable(self.point_fields, [{"raw": a, "value": b} for a, b in zip(cal["tableRaw"], cal["tableValue"])], 8)
        box.addWidget(self.points, 1)

        page = QWidget()
        tabs.addTab(page, "Validation rules")
        box = QVBoxLayout(page)
        hint = QLabel("One rule per row. Fill in only what the rule needs. Range, limit, timeout and calibration checks are added automatically.")
        hint.setObjectName("hint")
        box.addWidget(hint)
        self.rules = SpecTable(RULE_FIELDS, s["rules"], 6)
        box.addWidget(self.rules, 1)

        root.addLayout(hbox(button("Save", self.save, primary=True, owner=self)))
        if existing is not None:
            self.controls["id"].setReadOnly(True)
        self._kind_changed()

    @staticmethod
    def _text(value):
        return QLineEdit(str(value))

    @staticmethod
    def _choice(value, items, editable=False):
        combo = QComboBox()
        combo.addItems(list(dict.fromkeys(list(items) + ([value] if value or not items else []))))
        combo.setEditable(editable)
        combo.setCurrentText(value)
        return combo

    @staticmethod
    def _grid(tabs, title):
        page = QWidget()
        tabs.addTab(page, title)
        grid = QGridLayout(page)
        grid.setRowStretch(9, 1)
        return grid

    def _row(self, grid, row, column, label, key, control):
        grid.addWidget(QLabel(label), row, column * 2)
        grid.addWidget(control, row, column * 2 + 1)
        self.controls[key] = control

    def value(self, key) -> str:
        c = self.controls[key]
        return c.currentText() if isinstance(c, QComboBox) else c.text()

    def _kind_changed(self):
        kind = self.value("kind")
        self.controls["deviceId"].setEnabled(kind == "hardware")
        self.controls["hwChannel"].setEnabled(kind == "hardware")
        self.controls["expression"].setEnabled(kind == "derived")

    def _quantity_changed(self):
        self.controls["unit"].clear()
        self.controls["unit"].addItems(self.app.engine.units.units_for(self.value("quantity")))

    def save(self):
        out = dict(self.s)
        for key in ("id", "name", "kind", "role", "sensorType", "equipment", "deviceId", "hwChannel", "expression", "quantity", "unit", "rawUnit"):
            out[key] = self.value(key).strip()
        for key, label in (("sampleRateHz", "Sample rate"), ("rangeMin", "Sensor range low"), ("rangeMax", "Sensor range high"), ("accuracy", "Accuracy"), ("resolution", "Resolution"),
                           ("warnLow", "Warning low"), ("warnHigh", "Warning high"), ("critLow", "Critical low"), ("critHigh", "Critical high"), ("activeValue", "Asserted level")):
            out[key] = number_of(self.value(key), label)
        out["critical"], out["enabled"] = self.critical.isChecked(), self.enabled_box.isChecked()
        points = from_rows(self.points.rows(), self.point_fields)
        out["calibration"] = {"type": self.value("calType"), "coefficients": number_list(self.value("calCoefficients"), "Coefficients"),
                              "tableRaw": [p["raw"] for p in points], "tableValue": [opt(p, "value", math.nan) for p in points],
                              "rawMin": number_of(self.value("calRawMin"), "Calibrated raw low"), "rawMax": number_of(self.value("calRawMax"), "Calibrated raw high"),
                              "certificate": self.value("calCertificate"), "calibratedOn": self.value("calOn"), "dueOn": self.value("calDue"), "calibratedBy": self.value("calBy")}
        out["rules"] = self.rules.specs()
        channel = Channel.from_dict(out)
        validation.rules_for_channel(channel)      # reject bad rules before saving
        engine = self.app.engine
        if self.existing is not None:
            engine.replace_channel(channel)
        else:
            engine.channels.add(channel)
            engine.rebuild()
        self.app.configuration_changed()
        self.accept()


class CrossRulesEditor(QDialog):
    FIELDS = [field("id", "Rule id"), field("expression", "Condition"), field("description", "Description"),
              field("classification", "Classification", "choice", [""] + list(alarms.CLASSES)), field("severity", "Severity", "choice", ["", "warning", "critical"]),
              field("persistenceS", "Persistence (s)", "number"), field("clearDelayS", "Clear delay (s)", "number"), field("equipment", "Equipment"), field("recommendation", "Recommendation")]

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Cross-channel rules")
        self.resize(1100, 480)
        layout = QVBoxLayout(self)
        hint = QLabel("Each rule fires while its condition is true, for example: rpm < 50 && torque > 20. Use channel identifiers in the condition.")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        self.table = SpecTable(self.FIELDS, [r.spec() for r in app.engine.cross_rules], 5)
        layout.addWidget(self.table, 1)
        layout.addLayout(hbox(button("Save", self.save, primary=True, owner=self)))

    def save(self):
        self.app.engine.set_cross_rules(self.table.specs())
        self.app.configuration_changed()
        self.accept()


class DeviceWizard(QDialog):
    """Guided setup. The form is generated from the driver's manifest, so new
    plugins get a wizard with no UI changes."""

    def __init__(self, app, candidate=None):
        super().__init__(app)
        self.app, self.candidate = app, candidate
        self.registry = app.engine.registry
        self.setWindowTitle("Device configuration wizard")
        self.resize(820, 720)
        root = QVBoxLayout(self)
        top = QFormLayout()
        self.picker = QComboBox()
        for entry in self.registry.entries:
            self.picker.addItem(f'{entry["id"]}  -  {entry["manifest"]["name"]}' + ("" if entry["available"] else "  [unavailable]"), entry["id"])
        self.device_id = QLineEdit("device_1")
        top.addRow("Driver", self.picker)
        top.addRow("Device id", self.device_id)
        root.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)
        self.log = QPlainTextEdit("Run a connection test before relying on this device.")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        root.addWidget(self.log)
        root.addLayout(hbox(button("Test connection", self.test, owner=self), button("Save profile", self.save_profile, owner=self), button("Load profile", self.load_profile, owner=self),
                            button("Add to setup", self.add, primary=True, owner=self)))
        self.inputs: dict = {}
        self.schema: list = []
        if candidate:
            self.picker.setCurrentIndex(self.picker.findData(candidate["driver"]))
        self.picker.currentIndexChanged.connect(guard(self, self.build_form))
        self.build_form()

    def build_form(self):
        entry = self.registry.entry(self.picker.currentData())
        if not entry["available"]:
            self.log.setPlainText(f'Unavailable on this host: {entry["reason"]}')
        self.schema = to_list(entry["manifest"]["configSchema"])
        self.inputs = {}
        form_widget = QWidget()
        form = QGridLayout(form_widget)
        form.setColumnStretch(1, 1)
        for k, f in enumerate(self.schema):
            kind, default = f["type"], f.get("default", "")
            form.addWidget(QLabel(f["label"]), k, 0)
            if kind.startswith("choice:"):
                control = QComboBox()
                control.addItems(kind.split(":", 1)[1].split(","))
                control.setCurrentText(str(default))
            elif kind == "bool":
                control = QCheckBox(checked=bool(default))
            elif kind.startswith("table:"):
                columns = kind.split(":", 1)[1].split(",")
                control = SpecTable([field(c, c) for c in columns], [], 6)
                control.setMinimumHeight(190)
            else:
                control = QLineEdit(str(default).strip())
            form.addWidget(control, k, 1)
            note = QLabel(f.get("help", ""))
            note.setObjectName("hint")
            note.setWordWrap(True)
            note.setMaximumWidth(250)
            form.addWidget(note, k, 2)
            self.inputs[f["name"]] = control
        form.setRowStretch(len(self.schema), 1)
        self.scroll.setWidget(form_widget)
        if self.candidate and self.candidate["confidence"] == "port_only" and "port" in self.inputs:
            self.inputs["port"].setText(self.candidate["address"])

    def read_config(self) -> dict:
        settings = {}
        for f in self.schema:
            control = self.inputs[f["name"]]
            if isinstance(control, SpecTable):
                settings[f["name"]] = [self._typed_row(row) for row in control.specs()]
            elif isinstance(control, QComboBox):
                settings[f["name"]] = control.currentText()
            elif isinstance(control, QCheckBox):
                settings[f["name"]] = control.isChecked()
            elif f["type"] == "number":
                settings[f["name"]] = number_of(control.text(), f["label"])
            else:
                settings[f["name"]] = control.text()
        return {"id": self.device_id.text(), "name": self.device_id.text(), "driver": self.picker.currentData(), "settings": settings,
                "autoReconnect": True, "silenceTimeoutS": 10}

    @staticmethod
    def _typed_row(row: dict) -> dict:
        typed = {}
        for key, value in row.items():
            if value in ("true", "false"):
                typed[key] = value == "true"
            else:
                try:
                    typed[key] = value if key == "field" else float(value)
                except ValueError:
                    typed[key] = value
        return typed

    def test(self):
        report = discovery.connection_test(self.registry, self.read_config())
        lines = [f'[{"ok" if s["ok"] else "FAIL"}] {s["step"]}: {s["detail"]}' for s in report["steps"]]
        lines.append("Data received. This confirms the link and framing only, not measurement accuracy." if report["protocolVerified"] else "Protocol NOT verified.")
        self.log.setPlainText("\n".join(lines))

    def save_profile(self):
        name = "".join(ch if ch.isalnum() else "_" for ch in self.device_id.text())
        codefile.write(paths.PROFILES / f"{name}.py", self.read_config())
        self.log.setPlainText(f"Saved profile {name}")

    def load_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Device profile", str(paths.PROFILES), "Profiles (*.py *.m)")
        if not path:
            return
        config = codefile.read(path)
        self.picker.setCurrentIndex(self.picker.findData(config["driver"]))
        self.device_id.setText(config["id"])
        self.build_form()
        for name, value in config["settings"].items():
            control = self.inputs.get(name)
            if isinstance(control, SpecTable):
                control.set_specs(to_list(value), 6)
            elif isinstance(control, QComboBox):
                control.setCurrentText(str(value))
            elif isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif control is not None:
                control.setText(number_text(value) if isinstance(value, float) else str(value))
        self.log.setPlainText(f'Loaded profile {config["id"]}')

    def add(self):
        self.app.engine.add_device(self.read_config())
        self.app.configuration_changed()
        self.accept()


class SetupLibraryDialog(QDialog):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.library = app, app.library
        self.setWindowTitle("Setups")
        self.resize(520, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(heading("Saved setups"))
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._picked)
        layout.addWidget(self.list, 1)
        self.name = QLineEdit(str(opt(app.engine.setup, "name", "")).replace(" ", "_"))
        self.name.setPlaceholderText("Setup name")
        self.name.textEdited.connect(lambda *_: self._disarm())
        layout.addWidget(self.name)
        layout.addLayout(hbox(button("Save", self.save, primary=True, owner=self), button("Load", self.load, owner=self), button("Delete", self.remove, owner=self), button("Open file", self.open_file, owner=self)))
        self.status = QLabel("Monitoring must be stopped to load a setup.")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.armed = ""
        self.refresh_list()

    def refresh_list(self, preferred=""):
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems(self.library.list())
        matches = self.list.findItems(preferred, Qt.MatchExactly) if preferred else []
        if matches:
            self.list.setCurrentItem(matches[0])
        elif self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)

    def _picked(self, text):
        self._disarm()
        self.name.setText(text)

    def _disarm(self):
        self.armed = ""

    def _confirmed(self, action, message) -> bool:
        if self.armed == action:
            self.armed = ""
            return True
        self.armed = action
        self.status.setText(message)
        return False

    def _selected(self) -> str:
        if self.list.currentItem() is None:
            raise ValueError("Select a saved setup first.")
        return self.list.currentItem().text()

    def save(self):
        if not self.name.text().strip():
            raise ValueError("Enter a name for this setup.")
        name = self.library.normalize(self.name.text())
        exists = self.library.exists(name)
        if exists and not self._confirmed(f"save:{name}", f'"{name}" already exists. Press Save again to replace it.'):
            return
        self.app.save_setup_as(name, exists)
        self.refresh_list(name)
        self.status.setText(f'Saved "{name}".')

    def load(self):
        self.app.apply_setup(self.library.read(self._selected()))
        self.accept()

    def remove(self):
        name = self._selected()
        if self._confirmed(f"delete:{name}", f'Press Delete again to permanently delete "{name}".'):
            self.library.remove(name)
            self.refresh_list()
            self.status.setText(f'Deleted "{name}".')

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open setup file", str(self.library.folder), "Setups (*.py *.m)")
        if path:
            self.app.apply_setup(codefile.read(path))
            self.accept()
