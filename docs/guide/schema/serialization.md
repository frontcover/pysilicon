---
title: Serialization
parent: Data Schemas
nav_order: 8
---

# Serialization & Deserialization

A central property of Waveflow is that every schema knows how to **serialize** itself — to convert
its value to and from a flat sequence of fixed-width words — and that the *same* packing rule is
generated for Python (simulation, golden vectors) and for C++ (the synthesizable kernel). Because
both sides are generated from one definition, they agree bit-for-bit.

This page explains the packing model and the generated methods. For *what files and classes* are
generated, see [Code Generation](./codegen.md); for *vectorized arrays* in a kernel — the packing
factor, lanes, and the throughput loop — see the Vitis vectorization guide
([raw](../vectorization/vitis_raw.md) / [struct](../vectorization/vitis_struct.md) /
[complex](../vectorization/vitis_complex.md)).

## The channel and the word width

Hardware moves data over a **channel** — an AXI-Stream, an `m_axi` memory port, a FIFO — whose data
path is some fixed width `word_bw` (e.g. 32, 64, 512). Serialization lays a schema value out into
`ap_uint<word_bw>` words for that channel; deserialization reads it back.

Crucially, `word_bw` is a property of the **channel**, not of the data: it may be **larger or
smaller** than the schema. Bits are packed **least-significant-bit first, with no padding**, so the
layout is independent of `word_bw` — only where the word *boundaries* fall changes. The number of
words a value of `B` bits occupies is:

```
n_words = ceil(B / word_bw)
```

## Serializing a schema over an interface

Take the `PolyCmdHdr` command header from [Code Generation](./codegen.md):

```python
class PolyCmdHdr(DataList):
    elements = {
        "cmd_type": {"schema": PolyCmdTypeField, "description": "DATA or END"},   #  1 bit
        "tx_id":    {"schema": TxIdField,         "description": "Transaction ID"}, # 16 bits
        "nsamp":    {"schema": NsampField,        "description": "Sample count"},   # 16 bits
    }
```

The generated `poly_cmd_hdr.h` gives the struct a set of **templated serialize/deserialize methods**,
one pair per kind of interface. Each is templated on the channel width `word_bw`:

| Interface | Serialize / Deserialize | Backing channel |
|---|---|---|
| Packed integer | `pack_to_uint` / `unpack_from_uint` | the whole schema as one `ap_uint<bitwidth>` |
| Memory (`m_axi`) | `write_array<word_bw>` / `read_array<word_bw>` | an `ap_uint<word_bw>` array in external memory |
| FIFO stream | `write_stream<word_bw>` / `read_stream<word_bw>` | an `hls::stream<ap_uint<word_bw>>` |
| AXI4-Stream | `write_axi4_stream<word_bw>` / `read_axi4_stream<word_bw>` | a stream with `TLAST` framing |

For example, a producer serializes a header onto an AXI-Stream and a consumer deserializes it back —
bit-for-bit identical, and identical to what the Python model produces:

```cpp
#include "include/poly_cmd_hdr.h"
static const int WORD_BW = 32;

// producer: serialize a header onto the stream
PolyCmdHdr hdr;
hdr.cmd_type = PolyCmdType::DATA;
hdr.tx_id    = 42;
hdr.nsamp    = 1024;
hdr.write_axi4_stream<WORD_BW>(out_stream, /*tlast=*/false);

// consumer: deserialize it back off the stream
PolyCmdHdr rx;
streamutils::tlast_status tl;
rx.read_axi4_stream<WORD_BW>(in_stream, tl);   // rx == hdr
```

Switching the channel to a different width is a one-constant change to `WORD_BW`; the packing rule
(and the agreement with the Python golden) is invariant.

### How `word_bw` sets the transfer time

Because a stream moves one word per cycle, the channel width directly sets how long a value takes to
transfer. `PolyCmdHdr` is `B = 33` bits (1 + 16 + 16), so:

| `word_bw` | `n_words = ⌈33 / word_bw⌉` | cycles to transfer |
|---|---|---|
| 32 | 2 | 2 |
| 64 | 1 | 1 |

The 33rd bit spills past a 32-bit word, so a 32-bit channel needs two words while a 64-bit channel
fits the header in one — halving the transfer time. For wider schemas (or arrays) the saving scales
with the word count.

## Arrays add a second dimension: lanes

When the schema is a [`DataArray`](./dataarrays.md), serialization packs **multiple elements per
word** — `pf = word_bw / element_bits` of them, each a *lane*. That turns the channel width into a
*parallelism* knob, not just a transfer-time knob: a kernel can process all `pf` lanes per cycle. The
packing factor, the lane loop, and the unrolling pragmas are covered in the Vitis vectorization guide,
starting with [raw arrays](../vectorization/vitis_raw.md).

## See also

- [Code Generation](./codegen.md) — the files and classes that get generated.
- [Data Arrays](./dataarrays.md) — declaring arrays and the `struct` vs `raw` storage modes.
- [Vitis: raw arrays](../vectorization/vitis_raw.md) — the packing factor, lanes, and throughput loop.
