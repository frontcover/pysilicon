---
title: Vitis Kernel Implementation
parent: Polynomial Accelerator
nav_order: 2
---

# Vitis Kernel implementation

## Overview

On the Vitis HLS side, the kernel runs a persistent `while (true)` loop driven by an `ap_ctrl_hs` AXI-Lite control register. The host configures coefficients in the AXI-Lite register map, writes `ap_start`, and the kernel processes streamed commands until it sees an `END` header (clean exit) or hits an error (halt).

Generated schema headers make the protocol code much simpler. The kernel reads and writes typed protocol objects directly:

- `PolyCmdHdr` — command header with `cmd_type` (`DATA` / `END`), `tx_id`, `nsamp`
- `PolyRespHdr` — per-transaction response header echoing `tx_id`
- `CoeffArray` — polynomial coefficient array (read from the AXI-Lite regmap)

The accelerator's control/status block is described in Python on `PolyAccelComponent` using `VitisRegMap`:

- `halted` (`R`, 1 bit) — `1` if the kernel halted on error
- `error` (`R`, 8 bits) — last error code (`PolyError` enum)
- `tx_id` (`R`, 16 bits) — `tx_id` of the offending transaction
- `coeffs` (`RW`, 4 × Float32) — polynomial coefficients

The matching C++ kernel signature exposes the same fields as `s_axilite` arguments.

## Implementation code

The HLS kernel is implemented in [`examples/poly/poly.cpp`](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly.cpp). It declares one AXI-Lite control bundle covering coefficients, status registers, and the `ap_ctrl_hs` `return` port. The loop body:

- reads a `PolyCmdHdr` from the input stream;
- if `cmd_type == END`, breaks out of the loop and returns;
- on TLAST framing errors latches `halted`/`error`/`tx_id` and returns;
- otherwise emits a `PolyRespHdr`, processes `nsamp` lane-packed samples through Horner evaluation, and emits the result stream.

When the function returns, Vitis asserts `ap_done`, the host reads the regmap status, and re-launches via the platform reset controller if it needs to clear `halted`.

---

Go to [implementing the Vitis HLS testbench](./vitis-tb.md)