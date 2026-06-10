---
title: "Vitis: raw arrays"
parent: Vectorization
nav_order: 10
---

# Vectorized arrays in Vitis — `raw` storage

Just as each [data schema](../schema/codegen.md) has a bit-exact Vitis C++ counterpart, so does each
`DataArray`: Waveflow generates the include files and helper methods that let a synthesizable kernel
manipulate the array — pack it, unpack it, process it lane-by-lane — using the *same* layout the Python
model uses.

A `DataArray` lowers to C++ in one of two **storage modes** (see [Data Arrays](../schema/dataarrays.md)).
This page covers **`raw` mode** — a flat C array — the one you reach for when you want explicit, per-cycle
control of the vectorized loop. The [`struct`](./vitis_struct.md) mode (a wrapper with methods) and the
[`complex`](./vitis_complex.md) element are covered separately.

## Code generation in `raw` mode

The packing helpers are generated from the array's **element type** — and that element can be *any*
`DataSchema` scalar (`IntField`, `FixedField`, `FloatField`, `ComplexField`). You name the element type
and the channel widths you intend to support; everything else follows. For a 32-bit float element:

```python
from waveflow.build.build import BuildConfig, BuildDag
from waveflow.build.streamutils import StreamUtilsStep
from waveflow.hw.arrayutils import ArrayUtilsStep
from waveflow.hw.dataschema import FloatField

Float32 = FloatField.specialize(32)

cfg = BuildConfig(root_dir=project_dir)
dag = BuildDag()
dag.add(StreamUtilsStep(output_dir="include"))      # ArrayUtilsStep depends on this
dag.add(ArrayUtilsStep(Float32, [32, 64]))          # element type, supported word widths
dag.run(cfg)
```

**Artifact:**  The above code builds two files:
-  `include/float32_array_utils.h` containing the class definition and helpers to be used in synthesizable code
- `float32_array_utils_tb.h` for helpers that may not be synthesizable (like file I/O) that can the testbench. 

The first file declares the `float32_array_utils::` namespace with `pf<>`, the bulk `read_array`/`write_array`, the
per-word `read_array_elem`/`write_array_elem`, and the stream variants.

The defining property of `raw` mode: **the generated code is keyed on the element type, not on a particular
array.** There is no per-array struct — the same `float32_array_utils` helpers serve *every* `raw` array of
`float` elements, of any length. (The [`struct`](./vitis_struct.md) mode is the opposite: it generates a
wrapper type per array.)

## Declaring vectors in C++

Once the header files are generated, any Vitis C++ function can write code using these helpers.
Th Vitis HLS equivalent of the `raw` array is simply a flat C array of the element's `value_type`. (On the Python side this is
`cpp_storage="raw"`, which requires `static=True` and a 1-D shape — see
[Data Arrays](../schema/dataarrays.md).) In a kernel you declare it directly:

```cpp
#include "float32_array_utils.h"
namespace au = float32_array_utils;

static const int N = 256;
au::value_type x[N];     // == float x[256]
```

Using `au::value_type` rather than a hard-coded `float` keeps the declaration tied to the schema: change the
element type in Python, regenerate, and the C++ follows.

## Packing factors

As discussed in the section on [serialization](../schema/serialization.md),  arrays must be transferred over **channels**, such as AXI4-streams, or memory-mapped interfaces.  These channels may have **word bitwidth**, typically denoted `word_bw`, that may be larger or smaller than the bitwidth of each element of the array.  The generated include files provide methods to **serialize** and **deserialize** arrays of elements over channels of any width. 

Given a `word_bw`, two dual quantities describe how the elements line up with the words:

- the **packing factor** — `pf = ⌊word_bw / element_bits⌋` — how many whole elements fit in one word; and
- the **words per element** — `words_per_elem = ⌈element_bits / word_bw⌉` — how many words one element spans.

Exactly one of them exceeds 1 (both are 1 when `word_bw == element_bits`). A **wide channel**
(`word_bw ≥ element_bits`) packs `pf` elements per word — the vectorized case below — while a **narrow
channel** (`word_bw < element_bits`) spreads each element across `words_per_elem` words.

As an example, suppose a data structure is a 2D point:

```python
Float32 = FloatField.specialize(bitwidth=32)
class Point2D(DataList):
    elements = {
        "x":  {"schema": Float32},
        "y":  {"schema": Float32}
    }
```

This structure will have a `bitwidth = 64`.
After we generate the include file `point_2d.hpp`:  We can obtain the parameters:


```cpp
#include "point_2d_array_utils.h"
namespace point2d = point2d_array_utils;
static constexpr int PF      = point2d::pf<WORD_BW>();           // WORD_BW / 64 (0 if WORD_BW < 64)
static constexpr int elem_bw = point2d::value_bitwidth;         // 64
static constexpr int wpe     = point2d::get_nwords<WORD_BW>(1);  // ceil(64 / WORD_BW): words per element
```

Each `pf` element slot in a word is a **lane**. When `pf ≥ 1`, a loop that touches all `pf` lanes per cycle
does `pf` elements of work per cycle — so **`WORD_BW` is the throughput lever**. For the 64-bit `Point2D`:

| `WORD_BW` | `pf = ⌊WORD_BW/64⌋` | `words_per_elem = ⌈64/WORD_BW⌉` |
|---|---|---|
| 32  | 0 — element spans 2 words | 2 |
| 64  | 1 | 1 |
| 128 | 2 | 1 |
| 256 | 4 | 1 |
| 512 | 8 | 1 |

At `WORD_BW = 32` the element is wider than the word, so `pf` is 0 and each `Point2D` occupies two words;
from `WORD_BW = 64` up, whole elements fit per word. (For the schema-level view — `n_words` and the
per-transfer cycle cost — see [Serialization](../schema/serialization.md).)



## Serialization and deserialization of a full vector

Conceptually this is the same serialization described for [data schemas](../schema/serialization.md): bits
to and from `ap_uint<WORD_BW>` words. For an array, the **bulk** helpers move the whole array in one call:

```cpp
static const int NWORDS = au::get_nwords<WORD_BW>(N);   // packed words for N elements
ap_uint<WORD_BW> words[NWORDS];  
float x[N];
au::read_array<WORD_BW>(words, x, N);    // words: const ap_uint<WORD_BW>*  → x[0..N)
// ... compute on x ...
au::write_array<WORD_BW>(x, words, N);   // x → words
```

The packed word count is `get_nwords<WORD_BW>(N) = ⌈N · element_bits / WORD_BW⌉` — the array-utils helper
that mirrors the schema-level `n_words`. The whole-array helpers are pipelined: they retire `pf` elements
per cycle when `WORD_BW ≥ element_bits`, and one element per `words_per_elem` cycles when the element is
wider than the word.

Random access into the packed words depends on how the element lines up with the word, so there is no
single index formula:

- **`WORD_BW ≥ element_bits`** (and a multiple of it): element `i` is lane `i % pf` of word `⌊i / pf⌋`. A
  slice that starts on a word boundary (`start % pf == 0`) is then just a pointer offset:

  ```cpp
  const int pf = au::pf<WORD_BW>();
  au::read_array<WORD_BW>(words + start / pf, x, len);   // elements [start, start+len), start % pf == 0
  ```

- **`element_bits > WORD_BW`**: element `i` occupies words `[i · words_per_elem, (i+1) · words_per_elem)`.

When `WORD_BW` is not a multiple of `element_bits`, elements straddle word boundaries — compute the bit
offset `i · element_bits` and locate the word yourself rather than relying on a per-element word index.

Use the whole-array helpers when you want the array resident and don't need cycle-level scheduling (a
coefficient table loaded once, say). When you *do* want throughput, drop to the lane primitives below.

## Serialization and deserialization of a lane

The lane primitives expose one word — `pf` lanes — at a time, which is what you unroll over. This is the
pattern for the **vectorized regime** (`WORD_BW ≥ element_bits`, so `pf ≥ 1`); for a wide element (`pf = 0`)
the loop below is invalid — use the bulk `read_array` above. Walk the array in steps of `pf`, read a word
into a **partitioned** lane array, `UNROLL` the compute across the lanes, and write the word back. Here the
kernel computes `y[i] = x[i] * x[i]`:

```cpp
#include "float32_array_utils.h"
namespace au = float32_array_utils;
static const int PF = au::pf<WORD_BW>();   // >= 1 in this regime (WORD_BW >= element_bits)

int iword = 0;                             // word index — incremented, so no per-iteration divide
for (int i = 0; i < N; i += PF) {
#pragma HLS PIPELINE II=1
    float lane[PF];
#pragma HLS ARRAY_PARTITION variable=lane complete dim=1     // lanes in parallel registers
    const int n = (N - i < PF) ? (N - i) : PF;               // tail: last (partial) word

    au::read_array_elem<WORD_BW>(&x_words[iword], lane, n);  // one word → pf lanes
    for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
        if (k < n) lane[k] = lane[k] * lane[k];              // one element per lane
    }
    au::write_array_elem<WORD_BW>(lane, &y_words[iword], n); // pf lanes → one word
    ++iword;
}
```

The three pragmas are what vectorize the loop:

- **`ARRAY_PARTITION variable=lane complete`** — splits the lane array into `pf` registers so every lane is
  live in the same cycle (otherwise it is a BRAM and the `UNROLL` serializes on memory ports).
- **`UNROLL`** — instantiates `pf` parallel copies of the compute.
- **`PIPELINE II=1`** — issues one word (so `pf` elements) per cycle.

The `iword` counter is a small but real win: advancing it by one word per iteration gives the address
`&x_words[iword]` rather than `x_words + i / PF`, avoiding a per-iteration hardware divide (and the matching
`y_words[iword]` for the output).

Widen `WORD_BW` and `PF` rises with it; the same loop retires more elements per cycle. That is the
bus-width → throughput relationship made concrete (and what the VMAC `mem_dwidth` sweep measures).

### Streams instead of memory

For an AXI-Stream port the shape is identical, but the lane read/write use the stream variants, which also
carry `TLAST`:

```cpp
au::read_axi4_stream_elem<WORD_BW>(s_in,  lane, n);                 // pf lanes off the stream
au::write_axi4_stream_elem<WORD_BW>(s_out, lane, /*tlast=*/last, n);
```

`examples/stream_inband/poly_evaluate_impl.tpp` is the worked stream example;
`examples/vmac/vmac_compute_impl.tpp` is the worked `m_axi` example.

## See also

- [Serialization](../schema/serialization.md) — the schema-level packing model and `word_bw`.
- [Data Arrays](../schema/dataarrays.md) — declaring `DataArray`, `struct` vs `raw`.
- [`vitis_struct.md`](./vitis_struct.md) — the same packing behind a generated struct's methods.
- [`vitis_complex.md`](./vitis_complex.md) — complex elements (wireless).
