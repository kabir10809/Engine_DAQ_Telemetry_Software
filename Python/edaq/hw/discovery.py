"""Finds candidate hardware and runs connection diagnostics.

A discovered port or device is only a candidate. Compatibility comes from the
matrix verdict and from connection_test receiving real data.
"""
from __future__ import annotations

import math
import time

from ..util import opt


def scan(registry, matrix):
    """Ask every available driver for candidates and attach a verdict."""
    found, skipped = [], []
    for entry in registry.entries:
        if not entry["available"]:
            skipped.append(f'{entry["id"]}: {entry["reason"]}')
            continue
        try:
            for candidate in entry["cls"].discover():
                candidate["verdict"] = matrix.classify(candidate)
                found.append(candidate)
        except Exception as error:
            skipped.append(f'{entry["id"]}: discovery failed: {error}')
    return found, skipped


def connection_test(registry, config, sample_window_s=2.0) -> dict:
    """Stepwise diagnostics for one device configuration. protocolVerified is
    true only when the device actually delivered parsable samples."""
    report = {"steps": [], "protocolVerified": False, "identity": {}, "channels": []}

    def step(name, ok, detail=""):
        report["steps"].append({"step": name, "ok": ok, "detail": str(detail)})

    entry = registry.entry(config["driver"])
    step("Driver available on this host", entry["available"], entry["reason"])
    if not entry["available"]:
        return report
    driver = entry["cls"]()
    try:
        driver.configure(opt(config, "settings", {}))
        step("Settings accepted", True)
        started = time.perf_counter()
        driver.connect()
        step("Connect", True, f"{(time.perf_counter() - started) * 1e3:.0f} ms")
    except Exception as error:
        step("Connect", False, error)
        return report
    try:
        report["identity"] = driver.identify()
        step("Identify", True, report["identity"].get("evidence", "No evidence given."))
        report["channels"] = driver.discover_channels()
        step("Channel discovery", True, f'{len(report["channels"])} channels')
        count, bad = 0, 0
        t0 = time.time()
        started = time.perf_counter()
        while time.perf_counter() - started < sample_window_s and count == 0:
            time.sleep(0.05)
            for block in driver.read(t0 + time.perf_counter() - started):
                count += block.t.size
                bad += sum(not math.isfinite(x) for x in block.raw)
        report["protocolVerified"] = count > bad
        step("Receive samples", count > bad, f"{count} samples, {bad} non-finite")
        status = driver.status()
        step("Instrument status", status["instrument"] != "fault", f'{status["instrument"]}: {status["instrumentDetail"]}')
    except Exception as error:
        step("Acquisition", False, error)
    finally:
        driver.disconnect()
    return report
