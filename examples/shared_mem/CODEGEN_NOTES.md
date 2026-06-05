# shared_mem (histogram) codegen — diff vs hand-written `hist.cpp`/`hist.hpp`

Phase 3 generates `gen/hist.cpp` + `gen/hist.hpp` from the Python `HistAccel`
(`python -c "from hist import HistAccel; ..."` or the build DAG). The
hand-written `hist.cpp`/`hist.hpp` stay the **diff targets** until Phase 6 cosim
passes; they are not yet retired.

## What matches (the I/O scaffolding codegen owns)

- Kernel signature: `void hist(in_stream, out_stream, mem)` — two AXI4-Stream
  ports + one `m_axi` pointer; `m_axi` pragma `offset=slave bundle=gmem`, plus
  `ap_ctrl_hs port=return`.
- The three array ops lower identically: `float32_array_utils::read_array<32>` of
  data and edges, `uint32_array_utils::write_array<32>` of counts, each through
  `memmgr::byte_addr_to_word_index<32>(cmd.<addr>)`.
- Local buffers `static float data[...]`, `static float edges[...]`,
  `static ap_uint<32> counts[...]`.

## Expected differences (legible, by design)

- **Datapath is factored into hand-written hooks.** The generated kernel calls
  `hist_impl::validate(cmd)`, `hist_impl::compute(..., counts)`,
  `hist_impl::respond(m_out, tx_id, status)`; the hand-written `hist.cpp` inlines
  the bounds/alignment checks, the binning loop, and the response writes. The
  hooks (`hist_validate_impl.cpp`, `hist_compute_impl.cpp`,
  `hist_respond_impl.tpp`) encode the **same** logic the inline `hist.cpp` does —
  they are the datapath, so the diff is where a logic mismatch would show first.
- **Compute returns an array.** `counts = compute(...)` lowers to a declared
  `static ap_uint<32> counts[...]` + a void call passing `counts` as the trailing
  out-parameter (HLS can't return arrays by value).
- **Naming.** Generated uses the Python endpoint/param names (`s_in`/`m_out`/
  `m_mem`, buffers `data`/`edges`/`counts`) vs the hand-written `in_stream`/
  `out_stream`/`mem`, `data_buf`/`edge_buf`/`count_buf`. The literal widths are
  inlined (`<32>`) rather than via `mem_dwidth`; the depth is a summed constant
  `m_mem_depth = max_ndata + max_nbins + max_nbins` vs `max_mem_words`.
- **Header.** Generated uses `#pragma once`; constants are the buffer bounds it
  actually references (`max_ndata`, `max_nbins`, `m_mem_depth`). The edges/counts
  buffers are sized `max_nbins`/`32` (≥ the `nbins-1`/`nbins` runtime counts);
  the hand-written edge buffer is `max_nbins-1`. All bounds are safe upper limits.
- **Edges read is unconditional** (count `nbins-1`, which is `0` when `nbins==1`)
  rather than guarded by `if (nbins > 1)` — the extractor can't lower a `>`
  branch, and a zero-length burst is a no-op.

Functional equivalence is proven by Phase 4 (C-sim vs the `HistogramAccel`
golden) and Phase 6 (cosim + burst).

## Generated testbench (`gen/hist_tb.cpp` from `HistTBHls`)

The TB codegen lowers the same way the kernel does: counts like `nbins - 1` go
through the shared `_emit_ast_expr` lowerer (single source of truth), and each
`MemMgr::alloc` is clamped to `>= 1` word (a 0-word region is meaningless and
`alloc` rejects it; mirrors the SimPy `HistController`'s `max(nedges, 1)`).

### Known limitation — nbins-based validation isn't drivable through the generated TB

The C-sim/cosim coverage uses **`ndata == 0`** (INVALID_NDATA) as the
validation-failure case, **not `nbins == 0`**. The generated TB reads
`count = nbins - 1` edges *unconditionally* (it mirrors the kernel's guard-free
read; the extractor only lowers `==`/`!=` `CaseStmt`s, not the `if (nbins > 1)`
guard the hand-written `hist_csim_tb.cpp` uses). So:

- `nbins == 0` → edges count `-1`, which the file reader rejects (`n0 must be
  non-negative`).
- `nbins > max_nbins` → overruns the fixed `edges[max_nbins]` TB buffer.

`ndata == 0` cleanly exercises the validate→status / early-return / error-response
path and the `>= 1` alloc clamp (data count is 0). The **counts**-alloc clamp is
the same emitted expression (asserted by `test_tb_allocs_clamp_to_one_word`), it
just isn't hit at runtime through this TB.

**Future option (deferred, not a now-task):** the real fix is general `>`/`>=`
guard lowering — the deferred condition-IR work — which would let the TB express
`if (nbins > 1)`. A narrower stopgap would be to lower a clamped count like
`max(nbins - 1, 0)` so the edges read is always non-negative; that alone would
make `nbins == 0` drivable (the kernel rejects it before any real read anyway).
Neither is needed to prove the validation→status codegen path, which `ndata == 0`
already covers.
