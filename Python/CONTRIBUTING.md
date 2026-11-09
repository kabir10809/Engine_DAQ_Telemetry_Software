# Contributing

Thanks for helping. This software watches real engine test rigs, so accuracy matters more
than features.

## Setting up

```bash
python launch_engine_daq.py --dev --setup-only
.venv/bin/python -m pytest tests
```

## Rules for every change

- **Monitoring only.** No code path may stop, load or control the rig. Drivers read; they
  never write outputs.
- **Raw data is never altered or discarded.** Flag it instead.
- **Health needs evidence.** Unknown stays unknown. A live link says nothing about the
  instrument.
- **A suspicious measurement is never a confirmed equipment fault.** Colours are decided only
  in `edaq.alarms.color_of`.
- **Alarms are never silently suppressed.**
- **No JSON for anything the application stores.** Use `edaq.codefile`.
- **Nothing is written outside this folder.**
- New fault behaviour goes in the scenario table in `edaq/sim.py`, which is also the test table.

## Adding hardware support

Follow [docs/DRIVER_GUIDE.md](docs/DRIVER_GUIDE.md). Every driver needs a row in
`config/hardware_compatibility.py`, or the tests fail. Then run `tools/update_matrix_doc.py`.

Only claim what was checked. `loopback_verified` needs a passing test against a software
endpoint. `bench_validated` needs a recorded test on a physical instrument, compared against
a reference. If you have tested a device, a validation record is one of the most useful
contributions you can make.

## Changing what the operator sees

Update `docs/user_guide/user_guide.tex` and rebuild with `tools/build_user_guide.py`, which
needs `pdflatex`.
