---
title: Schema and Array Steps
parent: Build System
nav_order: 3
---

# Schema and Array Steps

This page describes the three concrete build steps that generate C++ output from Python schema definitions.

---

## StreamUtilsStep

`StreamUtilsStep` copies the `streamutils` C++ helpers — `streamutils_hls.h` and `streamutils_tb.h` — into a chosen directory under the build root. Every `DataSchemaStep` and `ArrayUtilsStep` depends on these headers, so `StreamUtilsStep` must be added to the DAG first.

```python
from pysilicon.build.streamutils import StreamUtilsStep

dag.add(StreamUtilsStep(output_dir="include"))
```

| Parameter | Description |
|---|---|
| `output_dir` | Directory relative to `cfg.root_dir` where the headers are written. Defaults to `"."`. |

The step writes:
- `<output_dir>/streamutils_hls.h` — synthesizable helpers (AXI stream types, serialization primitives)
- `<output_dir>/streamutils_tb.h` — testbench helpers (file I/O, JSON)
- `<output_dir>/streamutils.cpp` — companion implementation file (Vitis < 2025.1 only)

---

## DataSchemaStep

`DataSchemaStep` generates a pair of C++ header files for one `DataSchema` class:

- `<include_dir>/<schema_name>.h` — synthesizable header (struct definition + serialization methods)
- `<include_dir>/<schema_name>_tb.h` — testbench header (file I/O, JSON helpers)

```python
from pysilicon.hw.dataschema import DataSchemaStep

dag.add(DataSchemaStep(
    PolyCmdHdr,
    word_bw_supported=[32, 64],
    include_dir="include",
))
```

| Parameter | Description |
|---|---|
| `schema_cls` | The `DataSchema` subclass to generate headers for |
| `word_bw_supported` | List of word widths (e.g. `[32, 64]`) to generate serialization methods for |
| `include_dir` | Directory relative to `cfg.root_dir` where the headers are written |
| `include_filename` | Override the default output filename (optional) |

### Dependency wiring

When added to a `BuildDag`, `DataSchemaStep` automatically:

1. Wires itself to the `StreamUtilsStep` already in the DAG (required — raises `ValueError` if none is found).
2. Wires itself to any `DataSchemaStep` instances for schema types it depends on (e.g. if `PolyCmdHdr` contains a `CoeffArray` field, it wires to `CoeffArrayStep`).

This means the `#include` paths in the generated headers automatically point to the correct relative locations.

### Adding steps in dependency order

Schema dependencies must be added before the schemas that reference them. For the polynomial example:

```python
dag.add(StreamUtilsStep(output_dir="include"))

# PolyErrorField and CoeffArray have no schema dependencies — add first
dag.add(DataSchemaStep(PolyErrorField, word_bw_supported=[32, 64], include_dir="include"))
dag.add(DataSchemaStep(CoeffArray,     word_bw_supported=[32, 64], include_dir="include"))

# PolyCmdHdr depends on CoeffArray; PolyRespFtr depends on PolyErrorField
dag.add(DataSchemaStep(PolyCmdHdr,  word_bw_supported=[32, 64], include_dir="include"))
dag.add(DataSchemaStep(PolyRespHdr, word_bw_supported=[32, 64], include_dir="include"))
dag.add(DataSchemaStep(PolyRespFtr, word_bw_supported=[32, 64], include_dir="include"))
```

If your `SCHEMA_CLASSES` list is already ordered correctly (leaf types before containers), the list-comprehension form is concise:

```python
schema_steps = [
    dag.add(DataSchemaStep(cls, word_bw_supported=WORD_BW_SUPPORTED, include_dir="include"))
    for cls in SCHEMA_CLASSES
]
```

### include_dir vs. class-level include_dir

The `include_dir` on a `DataSchemaStep` controls where the header is written. It takes precedence over the `include_dir` class attribute on the schema class itself. The recommended pattern is to **not** set `include_dir` on the schema class and instead pass it to each `DataSchemaStep`. This keeps the schema class free of build-system concerns:

```python
# Preferred: schema class has no include_dir
class PolyCmdHdr(DataList):
    elements = { ... }

# include_dir is specified at the step level
dag.add(DataSchemaStep(PolyCmdHdr, word_bw_supported=[32, 64], include_dir="include"))
```

---

## ArrayUtilsStep

`ArrayUtilsStep` generates packed-array helper headers for a scalar element type. These headers provide C++ functions for reading and writing arrays of the given type across AXI streams and arrays at any supported word width.

```python
from pysilicon.hw.arrayutils import ArrayUtilsStep

dag.add(ArrayUtilsStep(Float32, [32, 64]))
```

| Parameter | Description |
|---|---|
| `elem_type` | A `DataSchema` subclass for the scalar element type (e.g. `Float32`, `PixelField`) |
| `word_bw_supported` | List of word widths to generate helpers for |

The step writes:
- `<elem_type.include_dir>/<name>_array_utils.h` — synthesizable array helpers
- `<elem_type.include_dir>/<name>_array_utils_tb.h` — testbench array helpers

The output directory is read from `elem_type.include_dir`, so the element type's specialization should include `include_dir`:

```python
Float32 = FloatField.specialize(bitwidth=32, include_dir="include")
dag.add(ArrayUtilsStep(Float32, [32, 64]))
# writes to include/float32_array_utils.h
```

`ArrayUtilsStep` automatically wires to the `StreamUtilsStep` in the DAG.

---

## Complete example

The `gen_vitis_code` method in the polynomial demo pulls all three step types together:

```python
from pysilicon.build.build import BuildConfig, BuildDag
from pysilicon.build.streamutils import StreamUtilsStep
from pysilicon.hw.arrayutils import ArrayUtilsStep
from pysilicon.hw.dataschema import DataSchemaStep

def gen_vitis_code(example_dir, include_dir="include"):
    cfg = BuildConfig(root_dir=example_dir)
    dag = BuildDag()

    dag.add(StreamUtilsStep(output_dir=include_dir))

    schema_steps = [
        dag.add(DataSchemaStep(cls, word_bw_supported=[32, 64], include_dir=include_dir))
        for cls in [PolyErrorField, CoeffArray, PolyCmdHdr, PolyRespHdr, PolyRespFtr]
    ]

    dag.add(ArrayUtilsStep(Float32, [32, 64]))

    results = dag.run(cfg)
    return [results[step.name].artifacts["include"] for step in schema_steps]
```
