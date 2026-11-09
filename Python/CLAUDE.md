# Project rules: Engine DAQ Telemetry (Python build)

A long-term instrumentation product. It must keep working as sensors and DAQ manufacturers
change. Follow these rules in every change, along with `CONTRIBUTING.md`.

## Independence
- This folder is a complete application with its own docs, tests and config. It must never
  need `../MATLAB` or MATLAB itself. `tests/test_platform.py` enforces this.
- It should stay a functional mirror of the MATLAB build: same features, scenarios and
  classifications. A change to behaviour should be made in both.

## Self-contained
- `launch_engine_daq.py` creates `.venv` on first run, using only the standard library. All
  Python work here uses `.venv/bin/python`, never the system Python.
- Add libraries to `requirements.txt` (needed to run), `requirements-hardware.txt` (one
  driver each; a failure only disables that driver) or `requirements-dev.txt`.
- No writes outside this folder.

## No JSON
Never use JSON for configuration, settings, setups, logs or anything the application stores.
Use `edaq.codefile`. Users edit settings through forms and tables, never by typing code. The
only exceptions are JSON as a user-selected data export/import format and the text protocol
of an instrument that sends JSON over a connection.

## Hardware compatibility matrix (required)
`config/hardware_compatibility.py` records what works and how far each claim has been verified.
- Adding or changing a driver means updating the matrix in the same change.
- Bump a driver's `version` when behaviour changes. A new major version flags earlier bench
  validations for revalidation.
- `implemented` means never executed; `loopback_verified` needs a passing test in
  `tests/test_driver_loopback.py`; `bench_validated` needs a validation record from a
  physical instrument.
- After editing run `tools/update_matrix_doc.py`. Never hand-edit
  `docs/HARDWARE_COMPATIBILITY.md`.

## Licence
GNU GPL v3 (`LICENSE`). Any new dependency must have a GPL-3.0-compatible licence.

## Before finishing
```bash
.venv/bin/python -m pytest tests
```
