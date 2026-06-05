---
title: Shared Memory (histogram)
parent: Examples
nav_order: 4
has_children: false
---

# Shared Memory (histogram)

The **shared-memory** pattern moves the *data* off the control plane and into
memory: the accelerator reads its inputs and writes its outputs over an AXI4
memory-mapped (`m_axi`) master, while a dedicated AXI4-Stream still carries the
command and the status response. It is the next step in the
[examples progression](../) after in-band stream control — control stays on a
stream, but the payload now lives in shared memory addressed by pointers in the
command.

The vehicle is a **histogram accelerator** (`examples/shared_mem/`, computation
files keep the `hist` name): given a command carrying three buffer addresses, it
reads `ndata` float samples and `nbins-1` float bin edges from memory, bins the
samples, and writes `nbins` uint32 counts back to memory.

This makes it the first example to exercise, over one `m_axi` bundle:

- **Multiple distinct buffers** at independent addresses (`data_addr`,
  `bin_edges_addr`, `cnt_addr`).
- **Two element types** — `Float32` inputs/edges and `Uint32` counts.
- **Validation → status** — `ndata`/`nbins` bounds checks select a `HistError`
  into the response before any memory access.

Like the other full examples it walks all five stages — Python model → SimPy
simulation → code generation → C and RTL simulation → timing extraction — with
the kernel and testbench generated from the Python `HistAccel` component. The
minimal `m_axi` codegen reference is
[`examples/increment/`](https://github.com/sdrangan/pysilicon/tree/main/examples/increment).
