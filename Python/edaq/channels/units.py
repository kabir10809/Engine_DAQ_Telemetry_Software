"""Quantities, units and affine conversions, extensible at run time.
Conversion between different quantities is refused."""
from __future__ import annotations

import math
from pathlib import Path

from .. import codefile, paths
from ..util import to_list


class UnitRegistry:
    def __init__(self, user_path=None):
        self.units: list[dict] = []
        for row in to_list(codefile.read(paths.CONFIG / "units.py")["units"]):
            self._append(row)
        self.user_path = Path(user_path) if user_path else None
        if self.user_path and self.user_path.is_file():
            for row in to_list(codefile.read(self.user_path)):
                self._append(row)

    def define(self, quantity, unit, factor, offset=0.0) -> None:
        """Add a unit; persists when a user file was configured."""
        if self.is_known(unit):
            raise ValueError(f'Unit "{unit}" already exists.')
        if not (math.isfinite(factor) and factor != 0 and math.isfinite(offset)):
            raise ValueError("Factor must be finite and non-zero; offset finite.")
        added = {"quantity": quantity, "unit": unit, "factor": factor, "offset": offset}
        self._append(added)
        if self.user_path:
            existing = to_list(codefile.read(self.user_path)) if self.user_path.is_file() else []
            codefile.write(self.user_path, existing + [added])

    def is_known(self, unit) -> bool:
        return any(row["unit"] == unit for row in self.units)

    def units_for(self, quantity) -> list[str]:
        return [row["unit"] for row in self.units if row["quantity"] == quantity]

    def quantities(self) -> list[str]:
        return list(dict.fromkeys(row["quantity"] for row in self.units))

    def quantity_of(self, unit) -> str:
        return self._lookup(unit)["quantity"]

    def convert(self, value, source, target):
        a, b = self._lookup(source), self._lookup(target)
        if a["quantity"] != b["quantity"]:
            raise ValueError(f'Cannot convert {source} ({a["quantity"]}) to {target} ({b["quantity"]}).')
        return (value * a["factor"] + a["offset"] - b["offset"]) / b["factor"]

    def _lookup(self, unit) -> dict:
        for row in self.units:
            if row["unit"] == unit:
                return row
        raise KeyError(f'Unknown unit "{unit}".')

    def _append(self, row) -> None:
        self.units.append({"quantity": row["quantity"], "unit": row["unit"],
                           "factor": float(row["factor"]), "offset": float(row["offset"])})
