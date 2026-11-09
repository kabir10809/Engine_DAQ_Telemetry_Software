import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edaq import paths, sim  # noqa: E402
from edaq.hw.registry import DriverRegistry  # noqa: E402

DriverRegistry()    # imports the plugin modules


@pytest.fixture
def folder():
    """A scratch folder inside the project; nothing is written outside it."""
    from edaq.util import new_id
    path = paths.TMP / f"test_{new_id('t')}"
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def tick(engine, seconds, step=0.1):
    for _ in range(round(seconds / step)):
        engine.clock.advance(step)
        engine.step()


@pytest.fixture
def started_engine():
    from edaq.channels.units import UnitRegistry
    from edaq.clock import Clock
    from edaq.engine import Engine
    engine = Engine(clock=Clock(1.8e9), units=UnitRegistry())
    engine.load_setup(sim.demo_setup())
    engine.start()
    return engine


@pytest.fixture
def recorded_run(started_engine, folder):
    from edaq.data.store import RunReader
    run = folder / "run"
    started_engine.start_recording(run, {"operator": "tester"})
    tick(started_engine, 3)
    started_engine.stop()
    return RunReader(run), run
