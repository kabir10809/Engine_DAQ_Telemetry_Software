"""Live sample buffers and append-only run storage.

A run folder holds manifest (full configuration and calibration snapshot),
samples.bin (little-endian float64 records [t, channel, flags, raw, value]) and
events (alarms and operator entries, appended as they happen). Each write is
flushed, so a crash loses at most the sample in flight, and a truncated final
record is tolerated when reading. Runs recorded by the MATLAB build can be read
too.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .. import codefile
from ..util import to_list


class LiveBuffer:
    """Fixed-size ring of recent samples for one channel, for live validation
    windows and dashboards. The recorder holds the complete data."""

    def __init__(self, sample_rate_hz, seconds):
        self.sample_rate_hz = sample_rate_hz
        if math.isfinite(sample_rate_hz):
            self.capacity = int(min(max(math.ceil(sample_rate_hz * seconds), 1000), 200000))
        else:
            self.capacity = 20000
        self.count = 0
        self._head = 0                  # next write position
        self._t = np.full(self.capacity, math.nan)
        self._raw = self._t.copy()
        self._v = self._t.copy()
        self._flags = np.zeros(self.capacity, np.int64)

    def append(self, t, raw, v, flags) -> None:
        n = t.size
        if n == 0:
            return
        if n > self.capacity:
            t, raw, v, flags = t[-self.capacity:], raw[-self.capacity:], v[-self.capacity:], flags[-self.capacity:]
            n = self.capacity
        idx = (self._head + np.arange(n)) % self.capacity
        self._t[idx], self._raw[idx], self._v[idx], self._flags[idx] = t, raw, v, flags
        self._head = (self._head + n) % self.capacity
        self.count = min(self.capacity, self.count + n)

    def _latest_indices(self, n):
        return (self._head - n + np.arange(n)) % self.capacity

    def window(self, seconds, t_now):
        """(t, v, raw, flags) from the last `seconds`, oldest first."""
        n = self.count
        if math.isfinite(self.sample_rate_hz):
            n = min(n, math.ceil(seconds * self.sample_rate_hz * 2) + 10)
        idx = self._latest_indices(n)
        keep = self._t[idx] >= t_now - seconds
        idx = idx[keep]
        return self._t[idx], self._v[idx], self._raw[idx], self._flags[idx]

    def last(self):
        """(t, v, raw, flags) of the newest sample, NaN when empty."""
        if self.count == 0:
            return math.nan, math.nan, math.nan, 0
        k = (self._head - 1) % self.capacity
        return float(self._t[k]), float(self._v[k]), float(self._raw[k]), int(self._flags[k])

    def or_flags(self, n, flags) -> None:
        """Combine flags into the newest n samples."""
        if n:
            idx = self._latest_indices(n)
            self._flags[idx] |= np.asarray(flags, np.int64)


class EventFile:
    """Append-only event log stored as code, one statement per event."""

    def __init__(self, path):
        self.path = Path(path)
        codefile.write(self.path, [])

    def append(self, event: dict) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(codefile.append_line(event))


class RunRecorder:
    def __init__(self, folder, manifest: dict):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.manifest = manifest
        self.sample_count = 0
        codefile.write(self.folder / "manifest.py", manifest)
        self.events = EventFile(self.folder / "events.py")
        self._samples = (self.folder / "samples.bin").open("ab", buffering=0)

    def append(self, channel_index, t, raw, value, flags) -> None:
        n = t.size
        if n:
            records = np.column_stack((t, np.full(n, channel_index, float), flags, raw, value))
            self._samples.write(records.astype("<f8").tobytes())
            self.sample_count += n

    def event(self, event: dict) -> None:
        self.events.append(event)

    def close(self, end_t) -> None:
        if not self._samples.closed:
            self._samples.close()
            self.manifest["endT"] = end_t
            self.manifest["sampleCount"] = self.sample_count
            codefile.write(self.folder / "manifest.py", self.manifest)


def _first_existing(folder: Path, stem: str):
    for suffix in (".py", ".m"):
        if (folder / f"{stem}{suffix}").is_file():
            return folder / f"{stem}{suffix}"
    return None


def is_run_folder(folder: Path) -> bool:
    return _first_existing(folder, "manifest") is not None


class RunReader:
    def __init__(self, folder):
        self.folder = Path(folder)
        path = _first_existing(self.folder, "manifest")
        if path is None:
            raise FileNotFoundError(f"{folder} is not a recorded run.")
        self.manifest = codefile.read(path)
        self.channels = to_list(self.manifest["channels"])
        events = _first_existing(self.folder, "events")
        self.events = to_list(codefile.read(events, tolerate_truncation=True)) if events else []

    def channel_ids(self) -> list[str]:
        return [c["id"] for c in self.channels]

    def channel(self, channel_id) -> dict:
        return self.channels[self.channel_ids().index(channel_id)]

    @property
    def start_t(self) -> float:
        return float(self.manifest["startT"])

    def read(self, ids=None, t_range=None) -> dict:
        """{id: {t, raw, value, flags}}. t_range is optional (start, end) in POSIX seconds."""
        all_ids = self.channel_ids()
        ids = list(ids) if ids else all_ids
        unknown = sorted(set(ids) - set(all_ids))
        if unknown:
            raise KeyError(f"Run has no channel: {', '.join(unknown)}.")
        count = (self.folder / "samples.bin").stat().st_size // 40
        records = np.fromfile(self.folder / "samples.bin", dtype="<f8", count=count * 5).reshape(-1, 5)
        if t_range is not None:
            records = records[(records[:, 0] >= t_range[0]) & (records[:, 0] <= t_range[1])]
        data = {}
        for channel_id in ids:
            subset = records[records[:, 1] == all_ids.index(channel_id) + 1]
            data[channel_id] = {"t": subset[:, 0], "raw": subset[:, 3], "value": subset[:, 4], "flags": subset[:, 2]}
        return data
