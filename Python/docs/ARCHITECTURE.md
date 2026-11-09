# Architecture

Each loop step in `edaq.engine.Engine.step`:

```
devices --> calibration --> derived channels --> validation --> alarms --> recorder
(plugins)                                                         |
                                                       UI reads on its own timer
```

| Module | Responsibility |
|---|---|
| `launch_engine_daq.py` | Creates `.venv` and starts the app, using only the standard library |
| `edaq.hw` | Driver contract, plugin registry, device sessions, discovery, compatibility matrix, decoders |
| `edaq.channels` | Channels, calibration, units, safe expression evaluator |
| `edaq.validation` | Per-sample flags and rule types |
| `edaq.alarms` | Classification and the alarm state machine |
| `edaq.engine` | The pipeline, the health model and the setup library |
| `edaq.data` | Live buffers, run recording, reading, import, export |
| `edaq.rig` | Geometry, performance relations, compression ratio with provenance |
| `edaq.experiment` | Experiments and report building |
| `edaq.sim` | Plant model, demo setup and the scenario table |
| `edaq.codefile` | Stored files as Python code, loaded without being run |
| `edaq.ui` | Window, theme, dashboard, pages. Reads from the engine, never drives it |

## Key decisions

- **Plugins are discovered, never named.** The core has no reference to a concrete driver.
- **Time comes from one clock.** A manual clock makes the whole pipeline deterministic, which
  is how all alert classifications are tested.
- **Raw data is immutable.** Validation writes a separate bitmask.
- **Colour is derived from class** in one function, so an anomaly cannot display as red.
- **A confirmed equipment fault needs a fault signal.** Measurement rules are refused that
  classification when they are built.
- **A critical limit on a suspect reading stays red**, labelled as suspect, because the software
  cannot prove the sensor wrong. Persistence keeps a single bad sample from reaching red.
- **Formulas use a parser and whitelist**, so a setup file cannot run arbitrary code.
- **Stored data is Python code, loaded without being executed.** `edaq.codefile` walks the
  syntax tree and accepts only literal values.
- **Run files are append-only** and flushed on every write, and survive a truncated tail.
- **Acquisition runs on the UI thread's timer**, like the MATLAB build. Drivers must not block.

## Alarm states

```
normal -> pending -> active_unack -> active_ack -> normal or cleared_latched
                          |
                          v
                     cleared_unack -> normal or cleared_latched -> reset -> normal
```

Red alarms latch by default. Reset is refused while the condition holds. A condition shorter
than its persistence period is logged as a transient.

## Adding a validation rule

Add a `Rule` subclass in `edaq/validation.py`, register it in `RULE_TYPES`, and add its
parameters to `RULE_FIELDS` in `edaq/ui/dialogs.py` so they appear in the channel editor.

## Theme

`edaq/ui/theme.py` holds every colour, in light and dark. Status colours mean only status.
Chart series use a separate fixed-order palette, so a series can never be mistaken for an alarm.
