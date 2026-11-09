"""Table-based editors and small UI helpers shared by the pages. Lists of
settings are edited in tables, so nothing is typed as code. The conversions are
free of graphics so they can be tested directly."""
from __future__ import annotations

import math
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QWidget)

from ..util import opt
from .theme import STATUS

NOT_SET = "-"


def field(name, label, kind="text", choices=()) -> dict:
    """kind: text, number, bool or choice."""
    return {"name": name, "label": label, "kind": kind, "choices": list(choices)}


def number_text(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{value:g}"


def range_text(lo, hi) -> str:
    if math.isnan(lo) and math.isnan(hi):
        return ""
    if math.isnan(lo):
        return f"<= {hi:g}"
    if math.isnan(hi):
        return f">= {lo:g}"
    return f"{lo:g} to {hi:g}"


def number_of(text, what) -> float:
    """Blank is NaN (not set). Anything else must be a number."""
    text = str(text).strip()
    if not text:
        return math.nan
    try:
        return float(text)
    except ValueError:
        raise ValueError(f'{what}: "{text}" is not a number.') from None


def number_list(text, what) -> list[float]:
    return [number_of(part, what) for part in re.split(r"[,\s]+", str(text).strip()) if part]


def to_rows(specs, fields, spare) -> list[list]:
    rows = []
    for spec in list(specs) + [{}] * spare:
        row = []
        for f in fields:
            value = spec.get(f["name"])
            if f["kind"] == "bool":
                row.append(bool(value))
            elif f["kind"] == "number":
                row.append(number_text(None if value is None else float(value)))
            elif f["kind"] == "choice":
                row.append(str(value) if value not in (None, "") else NOT_SET)
            else:
                row.append(", ".join(map(str, value)) if isinstance(value, list) else ("" if value is None else str(value)))
        rows.append(row)
    return rows


def _is_blank(value, f) -> bool:
    text = str(value).strip()
    return not text or (f["kind"] == "choice" and text == NOT_SET)


def from_rows(rows, fields) -> list[dict]:
    """Rows whose first column is blank are ignored."""
    specs = []
    for r, row in enumerate(rows, start=1):
        if _is_blank(row[0], fields[0]):
            continue
        spec = {}
        for f, value in zip(fields, row):
            if f["kind"] == "bool":
                spec[f["name"]] = bool(value)
            elif not _is_blank(value, f):
                spec[f["name"]] = number_of(value, f"Row {r}, {f['label']}") if f["kind"] == "number" else str(value).strip()
        specs.append(spec)
    return specs


def template_from_rows(title, specs, time_range) -> dict:
    """Report template from the sections table."""
    sections = []
    for row in specs:
        if not row["include"]:
            continue
        section = {"type": row["section"]}
        if "channels" in row:
            section["ids"] = [part for part in re.split(r"[,\s]+", row["channels"]) if part]
        if "units" in row:
            section["units"] = _unit_map(row["units"])
        if "heading" in row:
            section["heading"] = row["heading"]
        elif row["section"] == "text":
            section["heading"] = "Notes"
        if row["section"] == "text":
            section["body"] = opt(row, "body", "")
        sections.append(section)
    return {"title": title, "timeRange": time_range, "sections": sections}


def _unit_map(text) -> dict:
    """ "oil_press=psi, rpm=rev/s" to a dict of display units."""
    result = {}
    for pair in text.split(","):
        parts = [p.strip() for p in pair.split("=")]
        if len(parts) != 2:
            raise ValueError(f'Write units as channel=unit, not "{pair.strip()}".')
        result[parts[0]] = parts[1]
    return result


def report(widget, message: str) -> None:
    """Tell the operator an action failed. Tests read last_error instead."""
    window = widget.window()
    window.setProperty("last_error", message)
    if not QApplication.instance().property("edaq_test"):
        QMessageBox.warning(window, "Operation failed", message)


def guard(widget, action):
    """Wrap a UI callback so a failure shows an alert instead of vanishing."""
    def run(*_):
        try:
            action()
        except Exception as error:
            report(widget, str(error))
    return run


def button(text, action=None, primary=False, owner=None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if action is not None:
        b.clicked.connect(guard(owner, action) if owner is not None else action)
    return b


def heading(text, name="sectionTitle") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label


def hbox(*items, stretch_end=True, margins=False) -> QHBoxLayout:
    layout = QHBoxLayout()
    if not margins:
        layout.setContentsMargins(0, 0, 0, 0)
    for item in items:
        if isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    if stretch_end:
        layout.addStretch(1)
    return layout


def ask(parent, title, prompts):
    """Small modal form. prompts: (label, default). Returns values or None."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    form = QFormLayout(dialog)
    edits = [QLineEdit(str(default)) for _, default in prompts]
    for (label, _), edit in zip(prompts, edits):
        form.addRow(label, edit)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    return [edit.text() for edit in edits] if dialog.exec() == QDialog.Accepted else None


class DataTable(QTableWidget):
    """Read-only table refreshed in place, keeping scroll and selection."""

    def __init__(self, columns, stretch=None):
        super().__init__(0, len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.verticalHeader().hide()
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setShowGrid(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        if stretch is not None:
            header.setSectionResizeMode(stretch, QHeaderView.Stretch)
        self.verticalHeader().setDefaultSectionSize(28)
        self._sized_for = -1

    def set_rows(self, rows, colors=None) -> None:
        """colors: {column: [palette name or "" per row]}."""
        colors = colors or {}
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = self.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.setItem(r, c, item)
                item.setText("" if value is None else str(value))
                color = colors[c][r] if c in colors else ""
                if color:
                    item.setBackground(QBrush(QColor(STATUS[color][0])))
                    item.setForeground(QBrush(QColor(STATUS[color][1])))
                else:
                    item.setData(Qt.BackgroundRole, None)
                    item.setData(Qt.ForegroundRole, None)
        if len(rows) != self._sized_for:        # fit columns when rows change, not on every refresh
            self._sized_for = len(rows)
            self.resizeColumnsToContents()

    def selected_row(self):
        return self.currentRow() if self.selectionModel().hasSelection() else None


class SpecTable(QTableWidget):
    """Editable table of specs, with blank rows for new entries."""

    def __init__(self, fields, specs=(), spare=6):
        super().__init__(0, len(fields))
        self.fields = fields
        self.setHorizontalHeaderLabels([f["label"] for f in fields])
        self.verticalHeader().hide()
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.verticalHeader().setDefaultSectionSize(30)
        self.set_specs(specs, spare)

    def set_specs(self, specs, spare=6) -> None:
        rows = to_rows(list(specs), self.fields, spare)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (value, f) in enumerate(zip(row, self.fields)):
                self.removeCellWidget(r, c)
                self.setItem(r, c, None)
                if f["kind"] == "choice":
                    combo = QComboBox()
                    choices = [NOT_SET if not choice else choice for choice in f["choices"]]
                    combo.addItems(choices if value in choices else choices + [value])
                    combo.setCurrentText(value)
                    self.setCellWidget(r, c, combo)
                elif f["kind"] == "bool":
                    self.setCellWidget(r, c, QCheckBox(checked=value))
                else:
                    self.setItem(r, c, QTableWidgetItem(value))

    def rows(self) -> list[list]:
        result = []
        for r in range(self.rowCount()):
            row = []
            for c, f in enumerate(self.fields):
                if f["kind"] == "choice":
                    row.append(self.cellWidget(r, c).currentText())
                elif f["kind"] == "bool":
                    row.append(self.cellWidget(r, c).isChecked())
                else:
                    item = self.item(r, c)
                    row.append(item.text() if item else "")
            result.append(row)
        return result

    def specs(self) -> list[dict]:
        return from_rows(self.rows(), self.fields)
