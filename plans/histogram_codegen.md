# Histogram m_axi Codegen: make `shared_mem` codegen-driven

## Goal

Regenerate the histogram accelerator's Vitis HLS kernel **and** testbench from a
Python `HistAccel` HwComponent — closing the loop the increment toy was built to
de-risk. The deliverable is the `shared_mem` example (renamed from `histogram`)
as a **full-flow, codegen-driven, genuinely interesting** accelerator: Python
model → SimPy sim → generated kernel/TB → C-sim → C-synth → cosim → burst +
timing extraction.

Today the histogram is **half there**: it has a rich numpy model
(`HistogramAccel`), real schemas (`HistCmd`/`HistResp`), a hand-written
`hist.cpp`/`hist_tb.cpp`, and a 1046-line cosim/burst/timing harness (`HistTest`)
— but the kernel is **hand-authored, not generated**. This plan supplies the
missing piece: the synthesizable `HistAccel(HwComponent)` and the codegen that
emits `hist.cpp`/`hist_tb.cpp` from it.

This is part of [plans/example_rename.md](example_rename.md) (Phase 3). Run the
`git mv histogram → shared_mem` first, then this.

## Why this is the right next step (and what's new)

The increment work proved the m_axi codegen path end-to-end (signature, pragmas,
`array_utils` read/write lowering, `MemComponent`→array TB lowering, cosim,
bursts). Histogram is the **real** vehicle that proves it on something worth
showing — and it stresses **three things increment never did**:

1. **Multiple distinct memory buffers at independent addresses.** `HistCmd`
   carries `data_addr`, `bin_edges_addr`, and `cnt_addr` — three separate
   `MemAddr` fields, three separate `read_array`/`write_array` lowerings into the
   same `m_axi` bundle. Increment had one address.
2. **Two different element types over m_axi.** Float32 input data + edges,
   uint32 output counts (`hist.hpp` includes both `float32_array_utils.h` and
   `uint32_array_utils.h`). Increment was uint32-only.
3. **Validation → status response.** `ndata`/`nbins` bounds checks select a
   `HistError` enum into `HistResp.status` before any memory op. Increment's
   status was trivial.

Expect the codegen to need extension for (1)–(3); each is called out as its own
phase so the new capability is isolated and tested. The hand-written
`hist.cpp`/`hist_tb.cpp` are the **diff targets** — exactly as histogram was the
diff target for increment.

## Reference reading
- [examples/histogram/hist.hpp](../examples/histogram/hist.hpp) /
  [hist.cpp](../examples/histogram/hist.cpp) — kernel diff target (signature
  `void hist(in_stream, out_stream, mem)`, `max_ndata`/`max_nbins` bounds, the
  three-buffer read/compute/write).
- [examples/histogram/hist_tb.cpp](../examples/histogram/hist_tb.cpp) — TB diff
  target (three `MemMgr` allocs, populate data+edges, call, read-back counts).
- [examples/histogram/hist_demo.py](../examples/histogram/hist_demo.py) —
  `HistCmd`/`HistResp`/`HistError`, the `HistogramAccel.compute_hist` golden, and
  `HistTest` (csim/csynth/cosim/`extract_aximm_bursts`/timing — REUSE this
  harness; do not rebuild it).
- [examples/increment/incr.py](../examples/increment/incr.py) +
  `incr_build.py` + `incr_transform_impl.cpp` — the HwComponent + build-DAG +
  compute-hook pattern to mirror.
- [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py),
  [hwcodegen.py](../pysilicon/build/hwcodegen.py),
  [hw/hwstmt.py](../pysilicon/hw/hwstmt.py) — the m_axi codegen to extend for
  multi-buffer / multi-type / status.

## Design decisions (settled — do NOT re-litigate)

1. **Kernel signature stays `void hist(in_stream, out_stream, mem)`** — one
   `m_axi` bundle, three logical buffers addressed within it (mirrors
   [hist.hpp:32](../examples/histogram/hist.hpp#L32)). No separate bundles.
2. **Datapath compute is a hand-written hook, codegen owns the I/O scaffolding**
   — as increment split the `+1` into `incr_transform_impl.cpp`. The binning
   (searchsorted/bincount → a comparison + accumulate loop) lives in a
   `hist_compute_impl.cpp` hook; codegen emits the signature, pragmas, the three
   `array_utils` reads/writes, the local buffers, and the status/response. HLS
   can't return arrays by value, so the hook works in place on local buffers.
3. **Local buffer sizing from `max_ndata`/`max_nbins` HwParams** — `static
   float buf_data[MAX_NDATA]`, `static float buf_edges[MAX_NBINS]`, `static
   ap_uint<32> buf_counts[MAX_NBINS]`. Same compile-time-bound rule as increment
   (decision 5 there); fail loudly if a buffered read has no resolvable max.
4. **Validation lowers to an early-return status path.** `ndata`/`nbins` checks
   → set `HistResp.status` + skip memory ops, before the reads. Keep the Python
   `HistAccel.run_proc` and the generated C++ structurally parallel so the diff
   against `hist.cpp` is legible.
5. **Validate the generated kernel against the existing hand-written one** by
   diff + by passing the SAME `HistTest` cosim/burst suite. The hand-written
   `hist.cpp`/`hist_tb.cpp` are kept as diff targets until the generated versions
   pass, then the generated output becomes canonical (hand-written retired or
   moved to a `reference/` note).
6. **Reuse `HistTest` wholesale.** Its csim/csynth/cosim/`extract_aximm_bursts`/
   timing machinery is the validation harness — wire the generated build into it,
   don't reimplement.

## Working convention
- One commit per phase, in order; push after each. Single PR (the overhaul PR),
  multiple commits, each prefixed `shared_mem:`.
- After every phase: `pytest tests/hw/ tests/build/ tests/examples/ -k "not vitis"`
  green (note, don't fix, the pre-existing poly.hpp failure).
- Vitis HLS is installed here — the csim/cosim phases genuinely run; verify
  empirically, watch for soft-skips.
- Each codegen phase **diffs its output against the hand-written `hist.cpp`/
  `hist_tb.cpp`** and records the diff in a sandbox note.
- If multi-buffer or multi-type m_axi lowering needs a codegen change bigger than
  an additive extension (i.e. it touches shared `hwgen`/`hwcodegen` structurally),
  STOP and confirm the approach before plowing in.

## Phases

### Phase 1: `HistAccel(HwComponent)` + SimPy sim parity
Build the synthesizable `HistAccel`: stream slave `s_in` (HistCmd), stream master
`m_out` (HistResp), `MMIFMaster m_mem`, `max_ndata`/`max_nbins` HwParams. Its
`run_proc`: get cmd → validate (status) → `read_array(Float32, ndata, data_addr)`
→ `read_array(Float32, nbins-1, bin_edges_addr)` → `compute()` hook → `write_array
(Uint32, nbins, cnt_addr)` → respond. Assert it reproduces `HistogramAccel`'s
golden over a SimPy `DirectMMIF` + `MemComponent`.
**Commit:** `shared_mem: HistAccel HwComponent + SimPy parity with the numpy golden`

### Phase 2: Multi-buffer / multi-type m_axi IR + lowering
Extend the m_axi codegen so a kernel can issue **several** `MMArrayRead/WriteStmt`
against the same bundle at different `MemAddr` fields, with **different element
types** (float32 + uint32). Verify the extractor produces three array stmts with
the right (port, elem_type, count, addr) and that `to_cpp` lowers each to the
correct `array_utils::{read,write}_array<bw>` with `byte_addr_to_word_index`.
**Commit:** `hwcodegen: multi-buffer, multi-type m_axi array access (hist)`

### Phase 3: Kernel codegen → diff `hist.cpp`
Generate `shared_mem/gen/hist.cpp`/`hist.hpp`: signature + pragmas (decision 1),
three local buffers (decision 3), the three lowered array ops, the validation→
status path (decision 4), and the `compute()` hook include. Diff against the
hand-written `hist.cpp`; record it.
**Commit:** `shared_mem: m_axi histogram kernel codegen — diffed vs hand-written`

### Phase 4: C-sim green against the golden (milestone)
Build DAG (`shared_mem_build.py`, modeled on `incr_build.py` + `HistTest`):
schema/array/stream/memmgr steps, HlsCodegen (kernel), BuildInputs, PySim, CSim,
FunctionalVerify vs `HistogramAccel`. Hand-written or generated TB is fine here;
prove the **generated kernel** is functionally correct under C-sim.
**Commit:** `shared_mem: build DAG — generated histogram kernel passes C-sim`

### Phase 5: Testbench codegen → diff `hist_tb.cpp`
Generate the TB from a `HistTBHls`: three `MemMgr` allocs (data, edges, counts)
in allocation order, populate input buffers, kernel call with the `mem` pointer,
read-back counts → file for FunctionalVerify. Diff against hand-written
`hist_tb.cpp`; re-run C-sim with the generated TB.
**Commit:** `shared_mem: m_axi histogram testbench codegen — diffed vs hand-written`

### Phase 6: Cosim + burst + timing via `HistTest`
Wire the generated build into `HistTest`'s csynth/cosim/`extract_aximm_bursts`/
timing flow. Assert the generated kernel synthesizes, cosim passes, and the burst
report validates (read bursts for data+edges, write bursts for counts). Retire
the hand-written `hist.cpp`/`hist_tb.cpp` (or move to a `reference/` note) once
the generated versions are canonical.
**Commit:** `shared_mem: cosim + burst + timing validation for the generated histogram`

## Future / out of scope (capture, don't build)
- **Schema-typed m_axi access** (`read_schema`/`write_schema` over m_axi) if the
  binning is later expressed on structs rather than raw float/uint arrays.
- **Multiple m_axi bundles / separate read & write ports** — histogram uses one
  bundle by decision 1; richer memory topologies are a later codegen concern.
- **Promote the multi-buffer codegen back into a queue example** — the `mem_queue`
  vecunit (example_rename.md Future) reuses this multi-buffer m_axi work.
