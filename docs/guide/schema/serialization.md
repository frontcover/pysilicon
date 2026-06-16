---
title: Serialization
parent: Data Schemas
nav_order: 8
---

# Serialization & Deserialization

Every schema knows how to **serialize** itself — convert its value to and from a flat sequence of
fixed-width words — and the *same* packing rule is generated for Python (simulation, golden vectors) and for
C++ (the synthesizable kernel), so the two agree **bit-for-bit**.

This page is the **methods reference**: the calls for moving a single schema, and an array of schemas, over
each interface. For the *why* (the lane loop in depth, with-vs-without pipelining, the pragmas, storage
modes), see the linked vectorization pages.

## The channel and the word width

Data moves over a **channel** — an AXI-Stream, an `m_axi` memory port, a FIFO — of some fixed width
`word_bw`. Bits pack **LSB-first, no padding**, so the layout is independent of `word_bw` — only where the
word *boundaries* fall changes. A value of `B` bits occupies `n_words = ⌈B / word_bw⌉` words.

For an **array**, several elements share a word: `pf = ⌊word_bw / element_bits⌋` of them, each a **lane**. So
`word_bw` is a *parallelism* knob (process `pf` lanes/cycle), not just a transfer-time knob. The two geometry
constants:

```cpp
au::pf<WORD_BW>()            // elements per word (lanes)
au::get_nwords<WORD_BW>(N)   // words occupied by N elements
```

## (1) A single schema — across interfaces

The generated struct has **one read/write pair per interface**, each templated on the channel width `W`
(`= word_bw`). The schema's total bit width is the compile-time constant **`<Schema>::bitwidth`** (`B`), and
a value spans `⌈B/W⌉` words.

| Interface | Read | Write |
|---|---|---|
| Packed integer | `unpack_from_uint(u)` | `pack_to_uint()` |
| Memory (`m_axi`) | `read_array<W>(words)` | `write_array<W>(words)` |
| FIFO stream | `read_stream<W>(s)` | `write_stream<W>(s)` |
| AXI4-Stream | `read_axi4_stream<W>(s, tl)` | `write_axi4_stream<W>(s, /*tlast=*/...)` |

```cpp
#include "include/poly_cmd_hdr.h"
PolyCmdHdr hdr; hdr.tx_id = 42; hdr.nsamp = 1024;
hdr.write_axi4_stream<32>(out_stream, /*tlast=*/false);   // serialize onto a stream

PolyCmdHdr rx; streamutils::tlast_status tl;
rx.read_axi4_stream<32>(in_stream, tl);                   // rx == hdr, bit-for-bit
```

Switching the channel width is a one-constant change to `W`; the packing rule (and agreement with the Python
golden) is invariant.

- **Argument types.** `words` is an `ap_uint<W> words[nwords]` array; `s` is the channel's `hls::stream`.
- **Word count.** Size `words` with **`<Schema>::nwords<W>()`** (`= ⌈B/W⌉`).
- **Packed-integer limit.** `pack_to_uint` / `unpack_from_uint` move the whole schema as one **`ap_uint<B>`**.
  Vitis HLS caps `ap_uint` at **8192 bits**, so for `B > 8192` the packed form is unavailable — use the
  memory or stream methods instead.

## (2) An array of schemas — across interfaces

Array packing is generated as `<element>_array_utils::` free functions, keyed on the **element type**. The
caller works entirely in **element coordinates** — the methods do the word/lane mapping, the alignment, and
the partial-word ends internally, so there is **no manual `÷PF` or `& (PF-1)`** in kernel code.

### Geometry

- **`pf<W>()`** — elements per word; **`0`** when the element is wider than the word.
- **`lane_capacity<W>()`** — `= max(1, pf)`, the **lane-buffer size and loop step** (call it `LW`): it is `pf`
  in the vectorized regime and `1` in the wide-element regime.
- **`get_nwords<W>(N)`** — words occupied by `N` elements (`1` for one vectorized word; `⌈elem/W⌉` for one
  wide element).

### The lane methods — the sequential workhorse (all interfaces)

Each call reads/writes the **next `LW = max(1, pf)` elements**, regardless of regime:

| Read | Write | Channel |
|---|---|---|
| `read_array_lane<W>(src, dst, n)` | `write_array_lane<W>(src, dst, n)` | memory — a word pointer |
| `read_stream_lane<W>(s, dst, n)` | `write_stream_lane<W>(src, s, n)` | FIFO stream |
| `read_axi4_stream_lane<W>(s, dst, n, tl)` | `write_axi4_stream_lane<W>(src, s, tlast, n)` | AXI4-Stream (`TLAST`) |

- **`dst` is a buffer of length `LW`.** One call moves `LW` elements:
  - **`pf ≥ 1` (`LW = pf`):** one word/beat → `pf` lanes; **`n`** (`1 ≤ n ≤ LW`) is the **valid count** — `pf`
    for a full word, fewer only for the final partial one.
  - **`pf = 0` (`LW = 1`, wide element):** one element spanning `⌈elem/W⌉` words/beats → `dst[0]`; **`n` is
    ignored** (it is always 1).
- **Memory** is positioned by the caller: after each call advance the pointer by `get_nwords<W>(LW)` words
  (`1` when `pf ≥ 1`, `⌈elem/W⌉` when `pf = 0`). **Streams** sequence themselves — nothing to advance.

### Random range (memory only) — `read_array_slice`

For an **arbitrary element range** `[i0, i1)` — random access, a mid-array start, a strided row — use
`read_array_slice`; it locates `i0`'s word for you, so the kernel never writes `i0/PF`:

- **`read_array_slice<W>(words, i0, i1, out)`** — read elements `[i0, i1)` into `out[0 .. i1-i0)`.
  Division-free (a running offset + a wrapping lane counter, correct for any `pf`), handling partial-word ends
  *and* wide elements internally. The whole array is `[0, N)`; overload **`read_array_slice<W>(words, out)`**
  for a statically-sized `out`. Write: **`write_array_slice<W>(in, words, i0, i1)`** — unaligned ends
  read-modify-write the shared boundary words.
- `words` is an `ap_uint<W>*` — an **`m_axi` port** *or* a **local BRAM array**; word-indexed (`words[k]` =
  the k-th `W`-bit word, not a byte offset), lowering to bursts or local reads respectively, so one kernel
  body serves both.

### The canonical loop — one shape, both regimes, stream or memory

Step by `LW`; the lane method delivers `LW` elements whether they are `pf` lanes of a word or one wide element:

```cpp
namespace au = cfixed_array_utils;
static const int PF = au::pf<WORD_BW>();              // 0 if element wider than word
static const int LW = au::lane_capacity<WORD_BW>();   // = max(1, PF): elements per step and per buffer

au::value_type x_lane[LW], y_lane[LW];
#pragma HLS ARRAY_PARTITION variable=x_lane complete dim=1
#pragma HLS ARRAY_PARTITION variable=y_lane complete dim=1
const int WPU = au::get_nwords<WORD_BW>(LW);          // words per step (memory only; streams self-advance)
const ap_uint<WORD_BW>* xp = x_words;                 // running pointers (memory only)
ap_uint<WORD_BW>*       yp = y_words;

for (int i = 0; i < N; i += LW) {
#pragma HLS PIPELINE II=1                              // II=1 when PF>=1; HLS relaxes it for wide elements
    const int n = (N - i < LW) ? (N - i) : LW;        // valid this step
    au::read_array_lane<WORD_BW>(xp, x_lane, n);       // LW elements in   (read_stream_lane(s_in, ...) for a stream)
    for (int k = 0; k < LW; ++k) {
#pragma HLS UNROLL
        if (k < n) y_lane[k] = f(x_lane[k]);          // one element per lane
    }
    au::write_array_lane<WORD_BW>(y_lane, yp, n);      // LW elements out
    xp += WPU; yp += WPU;                             // memory only — drop for streams
}
```

The regime **falls out of `LW`** — no branch, no `i0/PF`, no `PF == 0` special case:

- **`PF ≥ 1`:** `LW = PF`, `WPU = 1`, `II = 1` → `PF` elements per cycle.
- **`PF = 0`:** `LW = 1`, `WPU = ⌈elem/W⌉`; each step pulls one wide element, so HLS relaxes `II` to `WPU` —
  one element per `WPU` cycles, the honest cost of a wide element.

For a **stream**, drop `xp`/`yp`/`WPU` and use `read_stream_lane(s_in, …)` / `write_stream_lane(…, s_out, …)`
(or the `axi4_stream` variants with `TLAST`) — the stream sequences itself; everything else is identical. The
three pragmas vectorize the body: `ARRAY_PARTITION complete` (lanes in parallel registers), `UNROLL` (`LW`
parallel compute copies), `PIPELINE II=1`.

**Rule of thumb.** Sequential processing (elementwise, accumulation) → the lane loop above. An arbitrary range
or random start → `read_array_slice`. *If you find yourself writing `i0/PF` or special-casing `PF == 0` in a
kernel, reach for `read_array_slice` (range) or `lane_capacity` (loop) instead — those exist precisely so the
kernel never does that arithmetic.*

## See also

- [Vitis: raw arrays](../vectorization/vitis_raw.md) — the lane loop in depth: **with vs without
  pipelining**, the pragma reasoning, and wide-element (`pf = 0`) handling.
- [Vitis: struct arrays](../vectorization/vitis_struct.md) / [complex](../vectorization/vitis_complex.md) —
  storage modes and complex elements, with worked examples.
- [Code Generation](./codegen.md) — the files and classes that get generated.
