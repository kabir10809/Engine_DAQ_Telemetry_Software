"""Discovers driver plugins by scanning folders for concrete Driver subclasses.

A broken plugin is recorded in problems and never prevents the application
from starting.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from .. import paths
from .driver import Driver, availability

REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "interfaces", "platforms", "requires", "configSchema")


class DriverRegistry:
    def __init__(self, folders=None):
        self.entries: list[dict] = []
        self.problems: list[str] = []
        self.scan(folders if folders is not None else [paths.PLUGINS])

    def scan(self, folders) -> None:
        for folder in map(Path, folders):
            if folder.is_dir():
                for file in sorted(folder.rglob("*.py")):
                    if file.name != "__init__.py":
                        self._try_register(file)

    def entry(self, driver_id) -> dict:
        for item in self.entries:
            if item["id"] == driver_id:
                return item
        raise KeyError(f'No driver plugin with id "{driver_id}" is installed.')

    def has(self, driver_id) -> bool:
        return any(item["id"] == driver_id for item in self.entries)

    def create(self, driver_id) -> Driver:
        item = self.entry(driver_id)
        if not item["available"]:
            raise RuntimeError(f'Driver "{driver_id}" cannot run here: {item["reason"]}')
        return item["cls"]()

    def ids(self) -> list[str]:
        return [item["id"] for item in self.entries]

    def _try_register(self, file: Path) -> None:
        try:
            name = f"edaq_plugin_{file.parent.name}_{file.stem}"
            spec = importlib.util.spec_from_file_location(name, file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, Driver) and cls is not Driver and not inspect.isabstract(cls) and cls.__module__ == name:
                    self._add(cls, file)
        except Exception as error:   # a bad plugin of any kind must not stop the application
            self.problems.append(f"{file.name}: {error}")

    def _add(self, cls, file) -> None:
        manifest = cls.manifest()
        missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
        if missing:
            raise ValueError(f"manifest is missing: {', '.join(missing)}")
        if self.has(manifest["id"]):
            raise ValueError(f'duplicate driver id "{manifest["id"]}"')
        available, reason = availability(manifest)
        self.entries.append({"id": manifest["id"], "cls": cls, "file": str(file), "manifest": manifest,
                             "available": available, "reason": reason})
