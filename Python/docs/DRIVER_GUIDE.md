# Writing a driver plugin

1. Copy `plugins/templates/template_driver.txt` to `plugins/drivers/<name>/<name>_driver.py`.
2. Rename the class and implement the methods below.
3. Add a row per declared interface to `config/hardware_compatibility.py`.
4. If it needs a library, add it to `requirements-hardware.txt` and list the import name in `requires`.
5. Run `tools/update_matrix_doc.py`, then the tests.

Restart the app and the driver appears in the wizard, with its form generated from the
manifest. No core file changes.

## Contract (`edaq.hw.driver.Driver`)

| Method | Rule |
|---|---|
| `manifest()` (static) | `id`, `name`, `version`, `interfaces`, `platforms` (`"any"`, `"windows"`, `"linux"`, `"macos"`), `requires` (import names), `configSchema` |
| `discover()` (static, optional) | Return candidates from `make_candidate`. Use `port_only` unless the device really answered |
| `connect()` | Raise on failure. Import optional libraries here, not at the top of the file |
| `identify()` | Report only what the device told you, and how, in `evidence` |
| `discover_channels()` | List of dicts: `hw`, `unit`, `sampleRateHz`, `description` |
| `read(t_now)` | Return `make_block(hw, t, raw)`. **Must not block** beyond a short timeout |
| `status()` | Start from `empty_status()` |
| `disconnect()` | Must be safe to call repeatedly |

## Rules

- **No output path.** No method may actuate anything. A test checks method names.
- **Instrument health needs evidence.** Set `instrument` to `"ok"` only on a real self-test or
  an empty error queue. Otherwise leave `not_assessed`.
- **Count bad frames** in `packetErrors` rather than hiding them.
- Decoding belongs in pure functions (see `edaq/hw/codecs.py`), so it can be tested with no hardware.

## `configSchema` field types

`string`, `number`, `bool`, `choice:a,b,c`, `table:col1,col2`.

## Moving up the matrix

| Status | Requires |
|---|---|
| `implemented` | Driver written |
| `loopback_verified` | A passing test in `tests/test_driver_loopback.py` |
| `bench_validated` | Compatibility page > Record bench validation, on a physical instrument, compared against a reference |
