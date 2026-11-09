"""Dashboard widgets and the grid geometry behind them. All types read only from
the engine, so the dashboard cannot affect acquisition."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import alarms
from ..util import opt
from .forms import DataTable, heading
from .theme import STATUS, THEMES

ROWS, COLS = 8, 12
TYPES = ("numeric", "gauge", "trend", "xy", "table", "alarms", "status")


def moved(w, row, col) -> dict:
    return {**w, "row": min(max(round(row), 1), ROWS - w["rowSpan"] + 1), "col": min(max(round(col), 1), COLS - w["colSpan"] + 1)}


def resized(w, row_span, col_span) -> dict:
    return {**w, "rowSpan": min(max(round(row_span), 1), ROWS - w["row"] + 1), "colSpan": min(max(round(col_span), 1), COLS - w["col"] + 1)}


def overlap(a, b) -> bool:
    return a["row"] < b["row"] + b["rowSpan"] and b["row"] < a["row"] + a["rowSpan"] \
        and a["col"] < b["col"] + b["colSpan"] and b["col"] < a["col"] + a["colSpan"]


def has_overlap(widgets) -> bool:
    return any(overlap(a, b) for i, a in enumerate(widgets) for b in widgets[i + 1:])


def placed(w, existing) -> dict:
    """Put w in the first free position, or fail if none fits."""
    for r in range(1, ROWS - w["rowSpan"] + 2):
        for c in range(1, COLS - w["colSpan"] + 2):
            candidate = {**w, "row": r, "col": c}
            if not any(overlap(candidate, e) for e in existing):
                return candidate
    raise ValueError("No free space for this widget on the page. Add a page or resize widgets.")


class GaugeFace(QWidget):
    """Arc gauge with warning and critical bands."""

    def __init__(self, low, high, bands, tokens):
        super().__init__()
        self.low, self.high, self.bands, self.tokens = low, high, bands, tokens
        self.value = math.nan
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(70)

    def _angle(self, value) -> float:
        fraction = (min(max(value, self.low), self.high) - self.low) / (self.high - self.low)
        return 225 - 270 * fraction

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 18
        arc = QRectF((self.width() - side) / 2, (self.height() - side) / 2 + 4, side, side)
        track = max(side * 0.075, 5)
        painter.setPen(QPen(QColor(self.tokens["grid"]), track, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc, 225 * 16, -270 * 16)
        for lo, hi, color in self.bands:
            start = self._angle(lo)
            painter.setPen(QPen(QColor(STATUS[color][0]), track, Qt.SolidLine, Qt.FlatCap))
            painter.drawArc(arc, int(start * 16), int((self._angle(hi) - start) * 16))
        if not math.isnan(self.value):
            angle = math.radians(self._angle(self.value))
            center = arc.center()
            radius = side / 2 - track
            painter.setPen(QPen(QColor(self.tokens["text"]), 2.5, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(center, center + type(center)(radius * math.cos(angle), -radius * math.sin(angle)))
            painter.setBrush(QColor(self.tokens["text"]))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(center, 4, 4)


class Widget(QFrame):
    def __init__(self, spec, engine, theme):
        super().__init__()
        self.setObjectName("card")
        self.spec, self.engine, self.tokens = spec, engine, THEMES[theme]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addWidget(heading(str(spec["title"]), "cardTitle"))
        self.body = QVBoxLayout()
        layout.addLayout(self.body, 1)
        self.ids = [i for i in opt(spec, "channels", []) if engine.channels.has(i)] if isinstance(opt(spec, "channels", []), list) \
            else ([spec["channels"]] if engine.channels.has(spec["channels"]) else [])
        getattr(self, f"_build_{spec['type']}")()

    def refresh(self):
        getattr(self, f"_refresh_{self.spec['type']}")()

    def _channel(self, channel_id):
        return self.engine.channels.get(channel_id)

    def _build_numeric(self):
        grid = QGridLayout()
        self.values = []
        for k, channel_id in enumerate(self.ids):
            grid.addWidget(QLabel(self._channel(channel_id).name), k, 0)
            value = heading("--", "bigValue")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, k, 1)
            self.values.append(value)
        self.body.addLayout(grid)

    def _refresh_numeric(self):
        for channel_id, label in zip(self.ids, self.values):
            value, _, flags = self.engine.latest(channel_id)
            unit = self._channel(channel_id).unit
            label.setText("--" if math.isnan(value) else f"{value:.4g} {unit}" + (" (flagged)" if flags else ""))

    def _build_gauge(self):
        if not self.ids:
            raise KeyError(f"Channel {self.spec.get('channels')} no longer exists.")
        c = self._channel(self.ids[0])
        low, high = (0.0, 100.0) if math.isnan(c.range_min) or math.isnan(c.range_max) else (c.range_min, c.range_max)
        bands = []
        if not math.isnan(c.warn_high):
            bands.append((c.warn_high, c.crit_high if not math.isnan(c.crit_high) else high, "yellow"))
        if not math.isnan(c.crit_high):
            bands.append((c.crit_high, high, "red"))
        if not math.isnan(c.warn_low):
            bands.append((c.crit_low if not math.isnan(c.crit_low) else low, c.warn_low, "yellow"))
        if not math.isnan(c.crit_low):
            bands.append((low, c.crit_low, "red"))
        self.face = GaugeFace(low, high, bands, self.tokens)
        self.body.addWidget(self.face, 1)
        self.reading = heading("no data", "bigValue")
        self.reading.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.reading)

    def _refresh_gauge(self):
        value, _, flags = self.engine.latest(self.ids[0])
        self.face.value = value
        self.face.update()
        self.reading.setText("no data" if math.isnan(value) else f"{value:.4g} {self._channel(self.ids[0]).unit}" + (" (flagged)" if flags else ""))

    def _plot(self):
        plot = pg.PlotWidget(background=self.tokens["surface"])
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(False, False)
        plot.hideButtons()
        plot.showGrid(x=True, y=True, alpha=0.25)
        for side in ("left", "bottom"):
            axis = plot.getAxis(side)
            axis.setPen(self.tokens["grid"])
            axis.setTextPen(self.tokens["muted"])
        self.body.addWidget(plot, 1)
        return plot

    def _build_trend(self):
        self.plot = self._plot()
        self.plot.setLabel("bottom", "Seconds before now", color=self.tokens["muted"])
        if len(self.ids) >= 2:
            self.plot.addLegend(offset=(8, 4), labelTextColor=self.tokens["muted"])
        series = self.tokens["series"]
        self.lines = [self.plot.plot([], [], pen=pg.mkPen(series[k % len(series)], width=2),
                                     name=f"{self._channel(i).name} [{self._channel(i).unit}]") for k, i in enumerate(self.ids)]
        if len(self.ids) == 1:
            self.plot.setLabel("left", self._channel(self.ids[0]).unit, color=self.tokens["muted"])
        self.flagged = pg.ScatterPlotItem(symbol="x", size=9, pen=pg.mkPen(STATUS["red"][0], width=2), brush=None)
        self.plot.addItem(self.flagged)

    def _refresh_trend(self):
        window = float(opt(self.spec, "windowS", 60))
        now = self.engine.clock.now()
        fx, fy = [], []
        for channel_id, line in zip(self.ids, self.lines):
            t, v, flags = self.engine.history(channel_id, window)
            step = max(1, t.size // 1500)
            line.setData(t[::step] - now, v[::step], connect="finite")
            bad = flags != 0
            fx.append(t[bad] - now)
            fy.append(v[bad])
        self.flagged.setData(np.concatenate(fx) if fx else [], np.concatenate(fy) if fy else [])
        self.plot.setXRange(-window, 0, padding=0)

    def _build_xy(self):
        self.plot = self._plot()
        x, y = self._channel(self.ids[0]), self._channel(self.ids[1])
        self.plot.setLabel("bottom", f"{x.name} [{x.unit}]", color=self.tokens["muted"])
        self.plot.setLabel("left", f"{y.name} [{y.unit}]", color=self.tokens["muted"])
        self.line = pg.ScatterPlotItem(size=5, pen=None, brush=self.tokens["series"][0])
        self.plot.addItem(self.line)

    def _refresh_xy(self):
        window = float(opt(self.spec, "windowS", 60))
        tx, vx, _ = self.engine.history(self.ids[0], window)
        ty, vy, _ = self.engine.history(self.ids[1], window)
        if tx.size >= 2 and ty.size >= 2:
            order = np.argsort(tx)
            index = np.clip(np.searchsorted(tx[order], ty, side="right") - 1, 0, tx.size - 1)
            self.line.setData(vx[order][index], vy)

    def _build_table(self):
        self.table = DataTable(["Channel", "Value", "Unit", "Quality"], stretch=0)
        self.body.addWidget(self.table, 1)

    def _refresh_table(self):
        rows, colors = [], []
        for channel_id in self.ids:
            value, _, flags = self.engine.latest(channel_id)
            quality, color = ("no data", "grey") if math.isnan(value) else (("flagged", "yellow") if flags else ("good", "green"))
            rows.append([self._channel(channel_id).name, f"{value:.5g}", self._channel(channel_id).unit, quality])
            colors.append(color)
        self.table.set_rows(rows, {3: colors})

    def _build_alarms(self):
        self.table = DataTable(["State", "Class", "Source", "Reason"], stretch=3)
        self.body.addWidget(self.table, 1)

    def _refresh_alarms(self):
        current = self.engine.alarms.current()
        self.table.set_rows([[c["color"].upper(), alarms.LABELS[c["classification"]], c["source"], c["reason"]] for c in current],
                            {0: [c["color"] for c in current]})

    def _build_status(self):
        grid = QGridLayout()
        self.lamps = []
        for k, channel_id in enumerate(self.ids):
            grid.addWidget(QLabel(self._channel(channel_id).name), k, 0)
            lamp = QLabel("no data")
            lamp.setAlignment(Qt.AlignCenter)
            grid.addWidget(lamp, k, 1)
            self.lamps.append(lamp)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def _refresh_status(self):
        from .theme import pill_style
        for channel_id, lamp in zip(self.ids, self.lamps):
            channel = self._channel(channel_id)
            value, when, flags = self.engine.latest(channel_id)
            text, color = "no data", "grey"
            if not math.isnan(when) and self.engine.clock.now() - when < 5:
                if channel.role != "measurement" and value == channel.active_value:
                    text, color = "ASSERTED", "red"
                elif flags:
                    text, color = "flagged", "yellow"
                else:
                    text, color = "clear" if channel.role != "measurement" else "ok", "green"
            lamp.setText(text)
            lamp.setStyleSheet(pill_style(color, 4))
