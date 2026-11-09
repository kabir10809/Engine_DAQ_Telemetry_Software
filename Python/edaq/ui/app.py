"""Main window: persistent status banners over the application pages.

The display refreshes on its own slower timer, so drawing never sets the
acquisition rate.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import alarms, codefile, paths
from ..engine import Engine, SetupLibrary, restore_setup
from ..experiment import ExperimentStore
from ..sim import demo_setup
from ..util import iso_time, new_id, opt
from . import dialogs, pages, theme
from .forms import button, guard

ACQUISITION_HZ = 20
UI_PERIOD_MS = 400
_SETTINGS = paths.WORKSPACE / "ui_settings.py"
_PAGES = (("Dashboard", pages.DashboardPage), ("Devices", pages.DevicesPage), ("Channels", pages.ChannelsPage), ("Alarms", pages.AlarmsPage), ("Diagnostics", pages.DiagnosticsPage),
          ("Engine", pages.EnginePage), ("Data", pages.DataPage), ("Experiments", pages.ExperimentsPage), ("Simulation", pages.SimulationPage), ("Compatibility", pages.CompatibilityPage))
_STATE_TEXT = {"green": "GREEN: within configured limits", "yellow": "YELLOW: measurement anomaly or warning", "red": "RED: equipment or rig fault", "no_data": "NO DATA: not monitoring"}


class App(QMainWindow):
    def __init__(self, engine=None, persist=True, library=None, experiments=None, start_timers=True):
        super().__init__()
        paths.ensure_workspace()
        self.persist = persist
        self.library = library or SetupLibrary()
        self.experiments = experiments or ExperimentStore()
        self.theme = "dark"
        if persist and _SETTINGS.is_file():
            self.theme = opt(codefile.read(_SETTINGS), "theme", "dark")
        notice = ""
        self.engine = engine
        if self.engine is None:
            self.engine = Engine()
            self.engine.set_event_log(paths.LOGS / f"{new_id('events')}.py")
            notice = restore_setup(self.engine, paths.SETUPS / "current.py")
        self.setWindowTitle("Engine DAQ Telemetry")
        self.resize(1440, 900)
        self._apply_stylesheet()
        self._build_chrome()
        self._build_pages()
        self.refresh()
        self.acquisition = QTimer(self)
        self.acquisition.setInterval(round(1000 / ACQUISITION_HZ))
        self.acquisition.timeout.connect(self.engine.safe_step)
        self.display = QTimer(self)
        self.display.setInterval(UI_PERIOD_MS)
        self.display.timeout.connect(self._safe_refresh)
        if start_timers:
            self.display.start()
        if notice:
            QMessageBox.warning(self, "Saved setup not loaded", notice)

    def _apply_stylesheet(self):
        import pyqtgraph as pg
        tokens = theme.THEMES[self.theme]
        pg.setConfigOptions(background=tokens["surface"], foreground=tokens["muted"], antialias=True)
        QApplication.instance().setStyleSheet(theme.stylesheet(self.theme))

    def _build_chrome(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        topbar = QWidget()
        topbar.setObjectName("topbar")
        bar = QHBoxLayout(topbar)
        bar.setContentsMargins(14, 10, 14, 10)
        self.status = QLabel()
        self.status.setMinimumWidth(320)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.start_button = button("Start monitoring", self.toggle, primary=True, owner=self)
        self.record_button = button("Quick record", self.toggle_record, owner=self)
        bar.addWidget(self.status)
        bar.addWidget(self.info)
        bar.addStretch(1)
        library = lambda: dialogs.SetupLibraryDialog(self).exec()
        for widget in (self.start_button, self.record_button, button("Save setup", library, owner=self), button("Load setup", library, owner=self), button("Demo setup", self.use_demo, owner=self),
                       button("Light / dark", self.toggle_theme, owner=self)):
            bar.addWidget(widget)
        root.addWidget(topbar)
        self.red_banner = QPushButton()
        self.yellow_banner = QPushButton()
        for banner, color in ((self.red_banner, "red"), (self.yellow_banner, "yellow")):
            banner.setStyleSheet(theme.pill_style(color, 0))
            banner.clicked.connect(lambda *_: self.select_page("Alarms"))
            banner.hide()
            root.addWidget(banner)
        body = QHBoxLayout()
        body.setSpacing(0)
        root.addLayout(body, 1)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(170)
        self.stack = QStackedWidget()
        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(lambda *_: guard(self, self.refresh_active)())

    def _build_pages(self):
        selected = max(self.sidebar.currentRow(), 0)
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        while self.stack.count():
            old = self.stack.widget(0)
            self.stack.removeWidget(old)
            old.deleteLater()
        self.pages = {}
        for name, page_class in _PAGES:
            self.sidebar.addItem(name)
            self.pages[name] = page_class(self)
            self.stack.addWidget(self.pages[name])
        self.dashboard = self.pages["Dashboard"]
        self.sidebar.setCurrentRow(selected)
        self.stack.setCurrentIndex(selected)
        self.sidebar.blockSignals(False)

    def select_page(self, name):
        self.sidebar.setCurrentRow(list(self.pages).index(name))

    def require_stopped(self):
        if self.engine.running:
            raise RuntimeError("Stop monitoring before changing the configuration.")

    def configuration_changed(self):
        """Rebuild what depends on the channel set."""
        self.engine.rebuild()
        self.dashboard.rebuild()
        self._auto_save()
        self.refresh_active()

    def refresh(self):
        self._update_banners()
        self.refresh_active()

    def refresh_active(self):
        self.stack.currentWidget().refresh()

    def _safe_refresh(self):
        try:
            self.refresh()
        except Exception as error:      # a drawing failure must not stop the banners next time
            print(f"Display refresh failed: {error}", file=sys.stderr)

    def _update_banners(self):
        state = self.engine.overall_status()
        self.status.setText(_STATE_TEXT[state])
        self.status.setStyleSheet(theme.pill_style({"no_data": "grey"}.get(state, state)))
        current = self.engine.alarms.current()
        self._set_banner(self.red_banner, [c for c in current if c["color"] == "red"])
        self._set_banner(self.yellow_banner, [c for c in current if c["color"] != "red"])
        e = self.engine
        activity = "Not monitoring"
        if e.running:
            seconds = int(e.clock.now() - e.start_t)
            activity = f"Monitoring {seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
        if e.recorder:
            activity += f"  |  REC {e.recorder.sample_count} samples"
        self.info.setText(f'Setup: {opt(e.setup, "name", "unnamed")}\n{activity}')
        self.start_button.setText("Stop monitoring" if e.running else "Start monitoring")
        self.record_button.setText("Stop recording" if e.recorder else "Quick record")
        self.record_button.setStyleSheet(theme.pill_style("red") if e.recorder else "")

    @staticmethod
    def _set_banner(banner, items):
        banner.setVisible(bool(items))
        if items:
            first = items[0]
            subject = f'{first["equipment"]} / {first["source"]}' if first["equipment"] else first["source"]
            # First sentence only, so the time and subject are never cut off.
            banner.setText(f'{len(items)} active   |   {iso_time(first["activatedT"])}   |   {alarms.LABELS[first["classification"]]}: {subject}   |   {first["reason"].split(". ")[0]}')

    def start(self):
        self.engine.start()
        self.acquisition.start()

    def stop(self):
        self.acquisition.stop()
        self.engine.stop()

    def toggle(self):
        self.stop() if self.engine.running else self.start()

    def toggle_record(self):
        if self.engine.recorder:
            self.engine.stop_recording()
            return
        if not self.engine.running:
            raise RuntimeError("Start monitoring first.")
        self.engine.start_recording(paths.RUNS / new_id("run"), {"kind": "quick"})

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self._apply_stylesheet()
        self._build_pages()
        self.refresh()
        if self.persist:
            codefile.write(_SETTINGS, {"theme": self.theme})

    def save_setup_as(self, name, overwrite):
        self.library.save(name, self.engine.current_setup(), overwrite)
        self.engine.setup["name"] = name
        self._auto_save()

    def apply_setup(self, setup):
        """Switch setups. A setup that cannot load leaves the current one in place."""
        self.require_stopped()
        previous = self.engine.current_setup()
        try:
            self.engine.load_setup(setup)
        except Exception:
            self.engine.load_setup(previous)
            raise
        self._build_pages()
        self._auto_save()
        self.refresh()

    def use_demo(self):
        self.apply_setup(demo_setup())

    def _auto_save(self):
        if self.persist:
            codefile.write(paths.SETUPS / "current.py", self.engine.current_setup())

    def closeEvent(self, event):
        self.display.stop()
        self.stop()
        super().closeEvent(event)


def main(arguments=None) -> int:
    application = QApplication.instance() or QApplication(sys.argv[:1] + list(arguments or []))
    application.setApplicationName("Engine DAQ Telemetry")
    window = App()
    window.show()
    return application.exec()
