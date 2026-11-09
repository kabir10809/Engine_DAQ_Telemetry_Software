# Hardware Compatibility Matrix

Generated from `config/hardware_compatibility.py`. Do not edit by hand.

| Status | Meaning |
|---|---|
| `unsupported` | Known not to work, or deliberately excluded. |
| `planned` | Intended but no driver exists yet. |
| `implemented` | Driver written against the vendor or toolbox API. Never exercised against a real or software endpoint. |
| `simulated` | Software model only. Proves nothing about real hardware. |
| `loopback_verified` | Driver ran end to end against a software endpoint (socket loopback, virtual CAN bus). Not tested on a physical instrument. |
| `bench_validated` | Verified on a physical instrument. Requires a passing validation record with date, operator, firmware, driver version and evidence. |
| `deprecated` | Still present but scheduled for removal. |

## Interfaces

| Interface | Driver | Status | Requires | Notes |
|---|---|---|---|---|
| USB | stream | implemented | any | USB devices that present a virtual COM port only. USBTMC goes through NI-VISA, NI USB DAQ through NI-DAQmx. Raw vendor USB needs a plugin or adapter. |
| USB-to-Serial | stream | loopback_verified | any | Same code path as RS-232, read through a pseudo-terminal. No physical adapter exercised. |
| RS-232 | stream | loopback_verified | any | Read through a pseudo-terminal in the test suite. No physical port exercised. |
| RS-485 | stream | implemented | any | Requires an adapter that handles half-duplex direction. |
| Modbus RTU | modbus | implemented | any, pymodbus, serial | Register decoding is unit tested. Bus transaction not exercised. |
| Modbus TCP | modbus | loopback_verified | any, pymodbus, serial | Read against a software Modbus TCP server in the test suite. Register decoding unit tested. |
| CAN | can | loopback_verified | any, can | Decoded from a python-can virtual bus. Listen-only by default where the interface supports it. |
| CAN FD | can | loopback_verified | any, can | Decoded from a python-can virtual bus. Listen-only by default where the interface supports it. |
| Ethernet | stream | loopback_verified | any | Same code path as the TCP and UDP loopback tests. Modbus TCP and OPC UA have their own rows. |
| TCP/IP | stream | loopback_verified | any | Socket loopback in the test suite, including split, malformed and closed connections. |
| UDP | stream | loopback_verified | any | Socket loopback in the test suite. |
| NI-DAQmx | ni_daqmx | implemented | windows, linux, nidaqmx | Needs NI driver software too. Never executed. |
| NI-VISA | visa | implemented | any, pyvisa | Uses NI-VISA when present, else PyVISA-py. Never executed. |
| GPIB | visa | implemented | any, pyvisa | Through a VISA GPIB resource string. Never executed. |
| OPC UA | opcua | loopback_verified | any, asyncua | Read against a local asyncua server in the test suite. |
| Vendor adapter | stream | loopback_verified | any | JSON lines verified over UDP loopback. A vendor SDK bridge in any language can emit these. Alternatively add a driver plugin. |
| Simulation | simulator | simulated | any | Engine rig model with fault injection. |

## Laboratory equipment

| Id | Manufacturer | Model | Category | Interface | Driver | Status | Last validated | Notes |
|---|---|---|---|---|---|---|---|---|
| sim_engine_rig | EngineDAQ | Simulated Engine Rig | Simulator | Simulation | simulator | simulated |  | Not real hardware. |
