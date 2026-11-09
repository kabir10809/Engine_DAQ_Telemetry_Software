#!/usr/bin/env python3
"""Start Engine DAQ Telemetry, creating its private environment on first run.

Run this file with any Python 3.10 or newer, on any computer. It uses only the
standard library until the environment exists. Everything it installs goes in
.venv beside this file, including pip's cache and temporary files, and that
folder is rebuilt automatically if it was copied from another computer.

    python launch_engine_daq.py               start the application
    python launch_engine_daq.py --setup-only  prepare the environment and stop
    python launch_engine_daq.py --rebuild     recreate the environment first
    python launch_engine_daq.py --dev         also install the test tools
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv"
STAMP = ENV / ".edaq-installed"
HARDWARE_REPORT = ENV / ".edaq-hardware-report.txt"
CORE = ROOT / "requirements.txt"
HARDWARE = ROOT / "requirements-hardware.txt"
DEV = ROOT / "requirements-dev.txt"
MINIMUM_PYTHON = (3, 10)


def say(message: str) -> None:
    print(f"[Engine DAQ] {message}", flush=True)


def stop(message: str) -> None:
    print(f"\n[Engine DAQ] Could not prepare the environment.\n{message}\n", file=sys.stderr, flush=True)
    raise SystemExit(1)


def env_python() -> Path:
    return ENV / "Scripts" / "python.exe" if os.name == "nt" else ENV / "bin" / "python"


def running_in_env() -> bool:
    return Path(sys.prefix).resolve() == ENV.resolve()


def contained_environ() -> dict:
    """Keep pip's cache and temporary files inside the project."""
    temporary = ENV / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environ = dict(os.environ)
    environ.update(PIP_CACHE_DIR=str(ENV / "pip-cache"), PIP_DISABLE_PIP_VERSION_CHECK="1",
                   TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
    return environ


def env_is_usable() -> bool:
    """False when missing, damaged, or copied from another computer or path."""
    python = env_python()
    if not python.exists():
        return False
    try:
        result = subprocess.run([str(python), "-c", "import sys, pip; print(sys.prefix)"],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == ENV.resolve()


def create_env() -> None:
    if ENV.exists():
        say("Removing an environment that cannot be used on this computer.")
        shutil.rmtree(ENV)
    say(f"Creating the private environment in {ENV}")
    try:
        venv.EnvBuilder(with_pip=True).create(ENV)
    except Exception as error:
        hint = ""
        if sys.platform.startswith("linux"):
            hint = "\nOn Debian or Ubuntu, this usually means the python3-venv package is missing."
        stop(f"Python could not create a virtual environment: {error}{hint}")


def fingerprint(include_dev: bool) -> str:
    digest = hashlib.sha256(f"{sys.version_info[:2]}".encode())
    for file in (CORE, HARDWARE) + ((DEV,) if include_dev else ()):
        digest.update(file.read_bytes())
    return digest.hexdigest()


def pip(*arguments: str):
    return subprocess.run([str(env_python()), "-m", "pip", *arguments], env=contained_environ())


def requirement_lines(file: Path) -> list[str]:
    lines = (line.strip() for line in file.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def install(include_dev: bool) -> None:
    say("Installing the application's libraries. The first run needs internet access and may take a few minutes.")
    if pip("install", "-r", str(CORE)).returncode != 0:
        stop("Installing the required libraries failed. Check the internet connection and the messages above, then run this again.")
    if include_dev and pip("install", "-r", str(DEV)).returncode != 0:
        stop("Installing the test tools failed.")
    # One at a time: a hardware library that will not install here only disables its own driver.
    failed = []
    for requirement in requirement_lines(HARDWARE):
        if pip("install", requirement).returncode != 0:
            failed.append(requirement)
    HARDWARE_REPORT.write_text("\n".join(failed) + "\n", encoding="utf-8")
    if failed:
        say("Not installed, so those drivers will show as unavailable: " + ", ".join(failed))


def prepare(rebuild: bool, include_dev: bool) -> None:
    if sys.version_info[:2] < MINIMUM_PYTHON:
        stop(f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer is required; this is {sys.version.split()[0]}.")
    if rebuild or not env_is_usable():
        create_env()
    wanted = fingerprint(include_dev)
    installed = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    # A plain launch keeps an environment that also has the test tools installed.
    if installed != wanted and installed != fingerprint(True):
        install(include_dev)
        STAMP.write_text(wanted + "\n", encoding="utf-8")


def relaunch(arguments: list[str]) -> int:
    environ = dict(os.environ, MPLCONFIGDIR=str(ENV / "matplotlib"))
    return subprocess.call([str(env_python()), str(Path(__file__).resolve()), *arguments], env=environ)


def main() -> int:
    arguments = sys.argv[1:]
    setup_only = "--setup-only" in arguments
    if running_in_env() and not ("--rebuild" in arguments):
        if setup_only:
            return 0
        sys.path.insert(0, str(ROOT))
        from edaq.ui.app import main as run_application
        return run_application([a for a in arguments if a != "--dev"])
    prepare("--rebuild" in arguments, "--dev" in arguments)
    if setup_only:
        say("Environment ready.")
        return 0
    return relaunch([a for a in arguments if a != "--rebuild"])


if __name__ == "__main__":
    raise SystemExit(main())
