"""Experiments, defined independently of the hardware, and report building.

Each run stores its own frozen copy of the configuration and calibration, so
later edits to an experiment cannot alter past results. A report template is an
ordered list of sections, so a report has no fixed structure.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from . import codefile, paths
from .util import iso_time, new_id, opt, to_list


class ExperimentStore:
    def __init__(self, root=None):
        self.root = Path(root or paths.EXPERIMENTS)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, spec: dict) -> str:
        experiment_id = new_id("exp")
        codefile.write(self._spec_path(experiment_id), {**spec, "id": experiment_id, "schemaVersion": 1})
        return experiment_id

    def load(self, experiment_id) -> dict:
        return codefile.read(self._spec_path(experiment_id))

    def save(self, spec: dict) -> None:
        codefile.write(self._spec_path(spec["id"]), spec)

    def list(self) -> list[str]:
        return sorted(folder.name for folder in self.root.iterdir() if folder.is_dir())

    def start_run(self, engine, experiment_id, context: dict) -> Path:
        spec = self.load(experiment_id)
        run_id = new_id("run")
        folder = self.root / experiment_id / "runs" / run_id
        engine.start_recording(folder, {**context, "experimentId": experiment_id, "experimentName": opt(spec, "name", ""),
                                        "experimentSpec": spec, "runId": run_id})
        return folder

    def runs(self, experiment_id) -> list[Path]:
        base = self.root / experiment_id / "runs"
        return sorted(folder for folder in base.iterdir() if folder.is_dir()) if base.is_dir() else []

    def _spec_path(self, experiment_id) -> Path:
        return self.root / experiment_id / "experiment.py"


def _template_path(name) -> Path:
    return paths.TEMPLATES / f"{re.sub(r'\W', '_', str(name))}.py"


def save_template(template: dict, name) -> None:
    codefile.write(_template_path(name), template)


def load_template(name) -> dict:
    return codefile.read(_template_path(name))


LINES_PER_PAGE = 62
PAGE_SIZE = (8.27, 11.69)


def build_report(reader, template: dict, path, units=None) -> None:
    from matplotlib.backends.backend_pdf import PdfPages
    from .channels.units import UnitRegistry
    units = units or UnitRegistry()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    time_range = template.get("timeRange") or None
    lines: list[str] = []
    with PdfPages(path) as pdf:
        def flush():
            nonlocal lines
            if lines:
                _text_page(pdf, lines)
                lines = []

        def add(new_lines):
            for line in new_lines:
                if len(lines) >= LINES_PER_PAGE:
                    flush()
                lines.append(line)

        add([f"# {template['title']}", ""])
        for section in to_list(template["sections"]):
            kind = section["type"]
            if kind == "text":
                add([f"## {section['heading']}", str(section.get("body", "")), ""])
            elif kind == "summary":
                add(_summary_lines(reader))
            elif kind == "channels":
                add(_channel_lines(reader, section))
            elif kind == "statistics":
                add(_statistic_lines(reader, section, time_range, units))
            elif kind == "alarms":
                add(_alarm_lines(reader))
            elif kind == "plot":
                flush()
                _plot_page(pdf, reader, section, time_range)
            else:
                raise ValueError(f'Unknown report section "{kind}".')
        flush()


def _ids(reader, section) -> list[str]:
    return to_list(opt(section, "ids", reader.channel_ids()))


def _summary_lines(reader) -> list[str]:
    m = reader.manifest
    lines = ["## Run summary", f"Source:          {m.get('source', '')}", f"Start (UTC):     {iso_time(m.get('startT'))}",
             f"End (UTC):       {iso_time(m.get('endT'))}", f"Application:     {m.get('runtime', '')} {m.get('appVersion', '')}",
             f"Config SHA-256:  {opt(m, 'configSha256', 'n/a')}", ""]
    lines += [f"Device {d['id']}: driver {d['driver']} v{d['driverVersion']}" for d in to_list(m.get("devices"))]
    context = opt(m, "context", {})
    lines += [f"{key}: {value}" for key, value in context.items() if isinstance(value, (str, int, float)) and value != ""]
    lines += [f"Condition {c['name']}: {opt(c, 'value', '')}" for c in to_list(context.get("conditions"))]
    return lines + [""]


def _channel_lines(reader, section) -> list[str]:
    lines = ["## Channels and calibration", f"{'Channel':<20} {'Unit':<10} {'Sensor':<22} {'Cal. type':<14} Certificate / date"]
    for channel_id in _ids(reader, section):
        c = reader.channel(channel_id)
        cal = opt(c, "calibration", {})
        lines.append(f"{channel_id:<20} {c.get('unit', ''):<10} {opt(c, 'sensorType', ''):<22} {opt(cal, 'type', 'none'):<14} "
                     f"{opt(cal, 'certificate', '')} {opt(cal, 'calibratedOn', '')}")
    return lines + [""]


def _statistic_lines(reader, section, time_range, units) -> list[str]:
    ids = _ids(reader, section)
    data = reader.read(ids, time_range)
    display = opt(section, "units", {})
    lines = ["## Statistics", f"{'Channel':<20} {'Unit':<9} {'Mean':>10} {'Min':>10} {'Max':>10} {'Std':>10} {'Samples':>9} {'Flagged':>9}"]
    for channel_id in ids:
        v, unit = data[channel_id]["value"], reader.channel(channel_id).get("unit", "-")
        if channel_id in display:
            v, unit = units.convert(v, unit, display[channel_id]), display[channel_id]
        if v.size:
            lines.append(f"{channel_id:<20} {unit:<9} {np.nanmean(v):>10.4g} {np.nanmin(v):>10.4g} {np.nanmax(v):>10.4g} "
                         f"{np.nanstd(v, ddof=1) if v.size > 1 else 0:>10.4g} {v.size:>9d} {int((data[channel_id]['flags'] != 0).sum()):>9d}")
        else:
            lines.append(f"{channel_id:<20} {unit:<9} no samples")
    return lines + [""]


def _alarm_lines(reader) -> list[str]:
    events = [e for e in reader.events if "classification" in e]
    lines = ["## Alarm events"]
    lines += [f"{e['time']}  {e['event']:<12} {e['color']:<6} {e['key']}: {e['detail']}" for e in events]
    if not events:
        lines.append("No alarm events recorded.")
    return lines + [""]


def _text_page(pdf, lines) -> None:
    from matplotlib.figure import Figure
    figure = Figure(figsize=PAGE_SIZE)
    y, step = 0.975, 0.95 / LINES_PER_PAGE
    for line in lines:
        options = {"fontsize": 7.5, "family": "monospace"}
        if line.startswith("# "):
            line, options = line[2:], {"fontsize": 16, "weight": "bold"}
        elif line.startswith("## "):
            line, options = line[3:], {"fontsize": 11, "weight": "bold"}
        figure.text(0.05, y, line, va="top", **options)
        y -= step
    pdf.savefig(figure)


def _plot_page(pdf, reader, section, time_range) -> None:
    from matplotlib.figure import Figure
    ids = _ids(reader, section)
    data = reader.read(ids, time_range)
    figure = Figure(figsize=PAGE_SIZE, layout="constrained")
    axes = figure.subplots(len(ids), 1, sharex=True, squeeze=False)[:, 0]
    figure.suptitle(opt(section, "heading", "Measurements"))
    for ax, channel_id in zip(axes, ids):
        ax.plot(data[channel_id]["t"] - reader.start_t, data[channel_id]["value"], linewidth=0.8)
        ax.set_ylabel(f"{channel_id} [{reader.channel(channel_id).get('unit', '-')}]")
        ax.grid(True, alpha=0.4)
    axes[-1].set_xlabel("Time [s]")
    pdf.savefig(figure)
