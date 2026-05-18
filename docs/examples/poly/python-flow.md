---
title: Python Flow
parent: Polynomial Accelerator
nav_order: 1
---
# Python Flow

The Python side of the example lives in [examples/poly/poly_demo.py](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly_demo.py). It follows the same structure as the regression in [tests/hw/test_dataschema_poly.py](https://github.com/sdrangan/pysilicon/blob/main/tests/hw/test_dataschema_poly.py): define schemas, build test inputs, run a golden model, emit generated headers, and write binary vectors.

## Step 1: Define the schemas

The first task is to define the data structures that represent the accelerator inputs and outputs. In PySilicon, these structures are specified using `DataSchema` classes. The script [examples/poly/poly.py](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly.py) defines the following schema classes:

- `PolyCmdType` — `IntEnum` of command tags (`DATA = 0`, `END = 1`)
- `PolyCmdHdr` for the streamed command header (type, transaction ID, sample count)
- `PolyRespHdr` for the per-transaction response header (echoed transaction ID)
- `CoeffArray` and `PolyErrorField` as reusable building blocks

Status and configuration that used to live in per-transaction headers/footers — coefficients in, `error`/`nsamp_read` out — have moved off the AXI-Stream path and onto an AXI-Lite register map (`VitisRegMap`) declared on `PolyAccelComponent`. See [Step 4](#step-4-run-the-python-golden-model) for the launch protocol.

For example, the command header schema is defined in Python as:

```python
class PolyCmdType(IntEnum):
    DATA = 0
    END  = 1

class CoeffArray(DataArray):
    element_type = Float32
    static = True
    max_shape = (4,)

class PolyCmdHdr(DataList):
    elements = {
        "cmd_type": {"schema": PolyCmdTypeField, "description": "DATA or END"},
        "tx_id":    {"schema": TxIdField,        "description": "Transaction ID"},
        "nsamp":    {"schema": NsampField,       "description": "Sample count (0 for END)"},
    }
```

This example illustrates how interface data structures can be described in compact, declarative Python syntax. Each field has a well-defined type and bit width, and that definition is preserved across both the Python model and the generated Vitis HLS implementation.

## Step 2: Auto-generate the include files

PySilicon can auto-generate an include file such as `poly_cmd_hdr.h` for each `DataSchema` class. Each generated header defines a Vitis HLS C++ data structure together with serialization and deserialization support.

In this example, header generation is driven by a list of schema classes and a call to the `gen_include()` method for each one:

```python
SCHEMA_CLASSES = [
    PolyErrorField,
    PolyCmdTypeField,
    CoeffArray,
    PolyCmdHdr,
    PolyRespHdr,
]
```

The `GenCppStep` in [poly_build.py](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly_build.py) walks this list and invokes `DataSchemaStep` for each class, emitting one header per schema into `include/`.

This step turns the Python schema definitions into hardware-facing C++ interface code without manually rewriting the same structures in a second language.

Later, this flow can be integrated into a more explicit incremental build process, but the example already demonstrates the core idea: define the interface once in Python and reuse it in the generated HLS headers.

## Step 3: Build the golden-model inputs

`BuildInputsStep` in [poly_build.py](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly_build.py) writes four binary files into `data/`:

- `coeffs.bin` — the polynomial coefficient vector `[1, -2, -3, 4]`, written to the regmap before launch
- `data_cmd_hdr.bin` — a DATA command header (`cmd_type = DATA`, `tx_id = 42`, `nsamp`)
- `samp_in_data.bin` — `nsamp` input samples spanning `[0, 1]`
- `end_cmd_hdr.bin` — an END command header (`cmd_type = END`, `nsamp = 0`) that terminates the kernel's persistent loop after the DATA transaction

## Step 4: Run the Python golden model

`PySimStep` instantiates `PolyAccelComponent` + `PolyTB`, wires them with `connect()` (two streams plus a `DirectMMIF` to the AXI-Lite slave), and runs the SimPy simulation:

- The testbench writes `coeffs` to the regmap, then writes `1` to `ap_start`.
- `VitisRegMapMMIFSlave` spawns `PolyAccelComponent.on_start`, a `while True` loop that reads command headers.
- The testbench streams the DATA cmd_hdr followed by `nsamp` samples; the kernel echoes `tx_id` into `PolyRespHdr` and streams `nsamp` evaluated samples back.
- The testbench then streams the END cmd_hdr; the kernel breaks the loop and returns.
- On error the kernel sets `halted = 1`, `error = <code>`, `tx_id = <offending txn>` in the regmap and returns; the testbench reads them back over AXI-Lite at end-of-simulation.

## Step 5: Write the simulation results

`PySimStep` writes the simulation outputs to `results/sim/`:

- `resp_hdr.bin` — the per-transaction response header
- `samp_out.bin` — the evaluated samples
- `regmap_status.json` — `{ "halted": ..., "error": ..., "tx_id": ... }` snapshot of the regmap

`ValidateCSimStep` compares these against the Vitis C-simulation outputs and asserts both runs report `halted = 0` and `error = NO_ERROR`.

## Running the script

Activate the `pysilicon` virtual environment, then run:

```powershell
python -m examples.poly.poly_build --through validate_timing
```

To run through the full pipeline including the Vitis C-simulation and synthesis steps (requires a local Vitis install):

```powershell
python -m examples.poly.poly_build
```

---

Go to [implementing the Vitis HLS Kernel](./vitis-kernel.md)
