"""Application folders. Everything derives from this file's location, so the
Python/ folder can be moved or copied anywhere and nothing is written outside it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
PLUGINS = ROOT / "plugins" / "drivers"
DOCS = ROOT / "docs"
WORKSPACE = ROOT / "workspace"
SETUPS = WORKSPACE / "setups"
DASHBOARDS = WORKSPACE / "dashboards"
PROFILES = WORKSPACE / "device_profiles"
RUNS = WORKSPACE / "runs"
EXPERIMENTS = WORKSPACE / "experiments"
TEMPLATES = WORKSPACE / "report_templates"
EXPORTS = WORKSPACE / "exports"
LOGS = WORKSPACE / "logs"
TMP = WORKSPACE / "tmp"


def ensure_workspace() -> None:
    for folder in (SETUPS, DASHBOARDS, PROFILES, RUNS, EXPERIMENTS, TEMPLATES, EXPORTS, LOGS, TMP):
        folder.mkdir(parents=True, exist_ok=True)
