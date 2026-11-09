"""Contract every hardware plugin implements.

Drop a subclass anywhere under plugins/drivers and the registry finds it; the
core never names a concrete driver. See docs/DRIVER_GUIDE.md.

The contract is acquisition-only on purpose. There is no write, output or
actuation method: this application must not stop, load or control the
dynamometer. read() must not block longer than a short timeout, because all
devices share one acquisition loop.
"""
from __future__ import annotations

import importlib.util
import platform
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class Block:
    """New samples for one hardware channel. Rates may differ per channel."""
    hw: str
    t: np.ndarray
    raw: np.ndarray


def make_block(hw, t, raw) -> list[Block]:
    return [Block(str(name), np.asarray(ti, float), np.asarray(ri, float)) for name, ti, ri in zip(hw, t, raw)]


def empty_status() -> dict:
    """Default status makes no health claim. A driver must set instrument to
    "ok" only on positive evidence from the device, never merely because
    communication is working."""
    return {"connected": False, "instrument": "not_assessed", "instrumentDetail": "Device reports no self-diagnostics.",
            "overload": False, "packetsOk": 0, "packetErrors": 0}


def make_candidate(driver, interface, address, vendor, model, serial, confidence, notes) -> dict:
    """confidence is port_only, device_identified or protocol_verified.
    Finding a port never implies protocol support."""
    return {"driver": driver, "interface": interface, "address": address, "vendor": vendor, "model": model,
            "serial": serial, "confidence": confidence, "notes": notes}


def this_platform() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, platform.system().lower())


def availability(manifest: dict) -> tuple[bool, str]:
    """Whether a driver can run on this host, and why not."""
    platforms = manifest["platforms"]
    if "any" not in platforms and this_platform() not in platforms:
        return False, f"Not supported on {this_platform()} (supported: {', '.join(platforms)})."
    for module in manifest["requires"]:
        if importlib.util.find_spec(module) is None:
            return False, f"Requires Python package {module}, not installed on this host."
    return True, "Available on this host."


class Driver(ABC):
    def __init__(self):
        self.settings: dict = {}
        self.last_error = ""
        self.time_hint = float("nan")   # acquisition time, set before connect and read

    @staticmethod
    @abstractmethod
    def manifest() -> dict:
        """id, name, version, interfaces, platforms, requires, configSchema."""

    @abstractmethod
    def connect(self): ...

    @abstractmethod
    def identify(self) -> dict:
        """vendor, model, serial, firmware, evidence."""

    @abstractmethod
    def discover_channels(self) -> list[dict]:
        """hw, unit, sampleRateHz, description."""

    @abstractmethod
    def read(self, t_now: float) -> list[Block]: ...

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def disconnect(self): ...

    @staticmethod
    def discover() -> list[dict]:
        """Optional: candidates from make_candidate."""
        return []

    def configure(self, settings: dict) -> None:
        """Merge user settings over the manifest schema defaults."""
        merged = dict(settings)
        manifest = self.manifest()
        for field in manifest["configSchema"]:
            name = field["name"]
            if merged.get(name) in (None, "", []):
                if "default" in field:
                    merged[name] = field["default"]
                elif field.get("required"):
                    raise ValueError(f'Driver {manifest["id"]} requires setting "{name}".')
        self.settings = merged

    def note_time(self, t: float) -> None:
        self.time_hint = t
