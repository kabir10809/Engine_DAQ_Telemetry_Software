"""Regenerate docs/HARDWARE_COMPATIBILITY.md after a matrix change."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edaq.hw.matrix import CompatibilityMatrix
from edaq.hw.registry import DriverRegistry

registry, matrix = DriverRegistry(), CompatibilityMatrix()
problems = matrix.validate(registry)
if problems:
    raise SystemExit("Fix the matrix first:\n" + "\n".join(problems))
matrix.write_markdown(registry)
print("Matrix document updated.")
