# Engine DAQ Telemetry (Python build)

A hardware-agnostic instrumentation platform for engine test rigs. Monitoring only: it
never stops, loads or controls the rig, and it does not replace the rig's independent
safety system.

This folder is a complete application. It does not need MATLAB, or anything else in this
repository.

## Run it

You need Python 3.10 or newer. Then, on any computer:

| System | Start it with |
|---|---|
| Windows | double-click `run.bat` |
| Linux or macOS | `./run.sh` |
| Any | `python launch_engine_daq.py` |

The first run creates a private environment in `.venv`, installs everything there, and
starts the app. That needs internet access and a few minutes, and about 1.3 GB of disk. Later
runs start straight away and work offline. Nothing is installed outside this folder, and
if the folder is copied to another computer the environment rebuilds itself.

Tested on Linux with Python 3.14. Windows and macOS are supported by design but have not
been run yet.

It opens on a simulated rig. Press **Start monitoring**, then try the **Simulation** page to
inject faults. The full operator manual is [docs/user_guide.pdf](docs/user_guide.pdf).

## Adding your hardware

1. **Devices > Discover hardware.** A port being found says nothing about what is attached.
2. **Configure selected** or **Add device manually** opens the wizard. Run **Test connection**.
3. **Channels > Import from device** or **Add channel**. Set units, calibration, limits and rules.
4. **Compatibility > Add lab equipment**, and after checking against a reference,
   **Record bench validation**.

A device with no driver: see [docs/DRIVER_GUIDE.md](docs/DRIVER_GUIDE.md). No core code changes.

## Setups

Keep as many setups as you need, one per engine, rig or experiment. **Save setup** and
**Load setup** open a library where you can save under a name, load, or delete. Each is one
Python file in `workspace/setups/`, so setups are easy to back up or copy to another computer.

## Status colours

| State | Meaning |
|---|---|
| GREEN | Data is flowing and within configured limits. Not a claim that the equipment is healthy. |
| YELLOW | Suspicious measurement, communication warning or degraded instrument. |
| RED | Verified fault, loss of a critical sensor, or a configured critical limit. |
| NO DATA | Not monitoring. |

## Hardware support

See [docs/HARDWARE_COMPATIBILITY.md](docs/HARDWARE_COMPATIBILITY.md). As of this version
**no driver has been validated on a physical instrument**.

## Known limits

- All devices share one acquisition loop, up to 20 Hz, reading buffered data. A slow device
  delays the others. Check tick times on the Diagnostics page.
- Not built for crank-angle-resolved combustion pressure at high sample rates.
- Derived channels use the latest input values, at loop rate.
- Stream, Modbus, VISA and OPC UA timestamps are arrival times at the computer, not sensor times.
- `ISO 1585` power correction covers naturally aspirated spark-ignition engines only.
- Performance analysis across a recorded run and comparison between compression ratios are
  Python functions, not yet on a page (see the user guide).

## Stored files

Configuration, setups, dashboards, run details and logs are stored as Python code, not JSON.
Each file holds plain values you can open and read, and the app loads them without running
them. Everything is edited through forms in the app. JSON remains only as a data
export/import format and for instruments that send it over a connection.

## Tests

```bash
python launch_engine_daq.py --dev --setup-only
.venv/bin/python -m pytest tests
```

On Windows use `.venv\Scripts\python`. The suite runs every fault scenario and exercises the
drivers against software endpoints. None of it touches physical hardware.

## Documentation

- [docs/user_guide.pdf](docs/user_guide.pdf): operator manual
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it fits together
- [docs/DRIVER_GUIDE.md](docs/DRIVER_GUIDE.md): writing a hardware plugin
- [docs/FILE_FORMATS.md](docs/FILE_FORMATS.md): stored file formats
- [CONTRIBUTING.md](CONTRIBUTING.md): how to contribute

## Licence

Copyright (C) 2026 Kabir

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, version 3.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [LICENSE](LICENSE) file for details.
