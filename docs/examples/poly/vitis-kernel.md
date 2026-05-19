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

The HLS kernel is implemented in [`examples/poly/poly.cpp`](https://github.com/sdrangan/pysilicon/blob/main/examples/poly/poly.cpp). It declares one AXI-Lite control bundle covering coefficients, status registers, and the `ap_ctrl_hs` `return` port. The function signature:

```cpp
void poly(hls::stream<axis_word_t>& in_stream,
          hls::stream<axis_word_t>& out_stream,
          const float coeffs[4],
          ap_uint<1>& halted,
          ap_uint<8>& error_code,
          ap_uint<16>& tx_id_status);
```

The interface pragmas wire the scalars and the `return` port to the same AXI-Lite bundle as the coefficient array:

```cpp
#pragma HLS INTERFACE axis port=in_stream
#pragma HLS INTERFACE axis port=out_stream
#pragma HLS INTERFACE s_axilite port=coeffs       bundle=control
#pragma HLS INTERFACE s_axilite port=halted       bundle=control
#pragma HLS INTERFACE s_axilite port=error_code   bundle=control
#pragma HLS INTERFACE s_axilite port=tx_id_status bundle=control
#pragma HLS INTERFACE s_axilite port=return       bundle=control
```

The loop body:

- reads a `PolyCmdHdr` from the input stream;
- if `cmd_type == END`, breaks out of the loop and returns cleanly;
- on TLAST framing errors latches `halted` / `error_code` / `tx_id_status` and breaks (no stream flush — the host re-launches via platform reset);
- otherwise emits a `PolyRespHdr`, processes `nsamp` lane-packed samples through Horner evaluation using the `s_axilite` `coeffs` argument, emits the result stream, and validates the sample-burst framing the same way.

When the function returns, Vitis asserts `ap_done`, the host reads the regmap status (`halted` / `error_code` / `tx_id_status`), and — if anything other than `NO_ERROR` is reported — re-launches the kernel by asserting `ap_rst_n` via the platform reset controller and then writing a fresh `ap_start`.

---

Go to [implementing the Vitis HLS testbench](./vitis-tb.md)