---
title: Code Generation
parent: Data Schemas
nav_order: 2
---

# Auto-generating Vitis HLS Files

A key feature of PySilicon's data schemas is that each schema class can **auto-generate Vitis HLS C++ headers** that mirror the Python definition exactly. For a full walkthrough of the build system, see the [Build System guide](../build/).

---

## What gets generated

For each schema class, two files are produced:

- `<schema_name>.h` — synthesizable header used in Vitis HLS kernel code. Defines the C++ struct and templated serialization/deserialization methods.
- `<schema_name>_tb.h` — testbench companion header with file I/O and JSON helpers for non-synthesizable code.

For example, `PolyCmdHdr` generates:

```cpp
// poly_cmd_hdr.h
struct PolyCmdHdr {
    ap_uint<16> tx_id;
    CoeffArray  coeffs;
    ap_uint<16> nsamp;

    template<int word_bw>
    void write_axi4_stream(hls::stream<streamutils::axi4s_word<word_bw>>& s,
                           bool tlast = true) const;

    template<int word_bw>
    void read_axi4_stream(hls::stream<streamutils::axi4s_word<word_bw>>& s,
                          streamutils::tlast_status& tl);
    // ... write_array, read_array, write_stream, read_stream ...
};
```

The C++ struct fields match the Python `elements` dict exactly, and the serialization methods are templated over word width so a single header works for 32-bit and 64-bit AXI streams.

---

## Serialization methods

Each generated header includes methods for all configured word widths and interface types:

| Method | Interface | Direction |
|---|---|---|
| `write_array` / `read_array` | `ap_uint<W>[]` array | kernel ↔ array |
| `write_stream` / `read_stream` | `hls::stream<ap_uint<W>>` | kernel ↔ plain HLS stream |
| `write_axi4_stream` / `read_axi4_stream` | `hls::stream<streamutils::axi4s_word<W>>` | kernel ↔ AXI4-Stream |

All methods are templated on `word_bw` so the same code works regardless of bus width. Changing `WORD_BW` from 32 to 64 requires no changes to the kernel source.

---

## Using the generated headers in Vitis HLS

Once the headers are generated, use them directly in your HLS kernel. The stream word type must match what the generated headers expect — `streamutils::axi4s_word<W>`:

```cpp
#include "include/poly_cmd_hdr.h"
#include "include/streamutils_hls.h"

static const int WORD_BW = 32;
using axis_word_t = streamutils::axi4s_word<WORD_BW>;

void poly(hls::stream<axis_word_t>& in_stream,
          hls::stream<axis_word_t>& out_stream) {
#pragma HLS INTERFACE axis port=in_stream
#pragma HLS INTERFACE axis port=out_stream
#pragma HLS INTERFACE ap_ctrl_none port=return

    PolyCmdHdr cmd_hdr;
    streamutils::tlast_status cmd_hdr_tlast;
    cmd_hdr.read_axi4_stream<WORD_BW>(in_stream, cmd_hdr_tlast);

    // ... computation ...

    PolyRespHdr resp_hdr;
    resp_hdr.tx_id = cmd_hdr.tx_id;
    resp_hdr.write_axi4_stream<WORD_BW>(out_stream, true);
}
```

Serialization is one line per struct. If the bus width changes, only `WORD_BW` changes — no manual bit-packing is needed.

---

## Generating headers with the build system

Headers are generated through the [Build System](../build/). The recommended approach uses a `BuildDag`:

```python
from pysilicon.build.build import BuildConfig, BuildDag
from pysilicon.build.streamutils import StreamUtilsStep
from pysilicon.hw.dataschema import DataSchemaStep

cfg = BuildConfig(root_dir=example_dir)
dag = BuildDag()
dag.add(StreamUtilsStep(output_dir="include"))
dag.add(DataSchemaStep(PolyCmdHdr, word_bw_supported=[32, 64], include_dir="include"))
dag.run(cfg)
```

See [Schema and Array Steps](../build/schema.md) for the full reference.
