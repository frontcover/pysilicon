---
title: Build System
parent: Guide
nav_order: 5
has_children: true
---

# Build System

The eventual goal of PySilicon's build system is to provide an incremental, stepwise pipeline through all stages of translating a Python design into Vitis HLS outputs. Steps are planned for Python simulation, performance evaluation, Vitis HLS code generation and C-simulation, and functional, timing, and resource validation. The goal is a single Python source of truth: define the model once, then simulate and synthesize automatically.

At the current time, the build system is implemented for [data schema](../schema/) code generation — translating Python schema definitions into the Vitis HLS C++ include files needed for synthesis and testbenches.

The framework is general purpose — any code generation task can be expressed as a `BuildStep`. PySilicon ships built-in steps for the most common tasks (schema headers, array helpers, stream utilities), and custom steps can be added by subclassing `Buildable`.

## Key abstractions

| Class | Purpose |
|---|---|
| `BuildConfig` | Holds the root output directory shared by all steps in a build |
| `BuildStep` | Abstract base for any unit of work that produces files |
| `Buildable` | Convenience subclass of `BuildStep` with default file-writing logic |
| `BuildDag` | Manages a set of steps, resolves dependencies, and runs them in order |
| `StreamUtilsStep` | Copies the `streamutils` C++ helpers into the output tree |
| `DataSchemaStep` | Generates C++ headers for one `DataSchema` class |
| `ArrayUtilsStep` | Generates packed-array helper headers for one element type |

## Key features

- **Automatic dependency wiring** — when a step is added to a `BuildDag`, it inspects the steps already registered and wires itself to its dependencies automatically. You list the steps once; the DAG handles ordering and `#include` path resolution.
- **Topological execution** — steps run in dependency order. A `StreamUtilsStep` always runs before the schema steps that reference it.
- **Failure propagation** — if a step fails, downstream steps that depend on it are skipped and marked as failed rather than producing partial output.
- **Artifact tracking** — each step returns a `BuildResult` with a dict of named output `Path` objects that can be inspected or passed to other tools.

## Quick example

The following generates all C++ headers for the [polynomial accelerator example](../../examples/poly/). The pattern is the same for any accelerator: add `StreamUtilsStep` first, then one `DataSchemaStep` per schema class (in dependency order), then any `ArrayUtilsStep` instances for packed scalar arrays.

```python
from pysilicon.build.build import BuildConfig, BuildDag
from pysilicon.build.streamutils import StreamUtilsStep
from pysilicon.hw.arrayutils import ArrayUtilsStep
from pysilicon.hw.dataschema import DataSchemaStep

cfg = BuildConfig(root_dir=example_dir)
dag = BuildDag()

dag.add(StreamUtilsStep(output_dir="include"))
for cls in [PolyErrorField, CoeffArray, PolyCmdHdr, PolyRespHdr, PolyRespFtr]:
    dag.add(DataSchemaStep(cls, word_bw_supported=[32, 64], include_dir="include"))
dag.add(ArrayUtilsStep(Float32, [32, 64]))

results = dag.run(cfg)
```

See [BuildDag and BuildConfig](./dag.md) and [Schema and Array Steps](./schema.md) for details.
