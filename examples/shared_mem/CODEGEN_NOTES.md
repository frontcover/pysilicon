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
