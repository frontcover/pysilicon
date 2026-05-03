---
title: BuildDag and BuildConfig
parent: Build System
nav_order: 2
---

# BuildDag and BuildConfig

## BuildConfig

`BuildConfig` is a lightweight container that holds the root directory for a build. All step output paths are resolved relative to this root.

```python
from pysilicon.build.build import BuildConfig
from pathlib import Path

cfg = BuildConfig(root_dir=Path("my_project"))
```

Every generated file — schema headers, streamutils sources, array-utils headers — is written under `cfg.root_dir`. Subdirectory structure within the root is controlled by each step's `output_dir` or `include_dir` parameter.

---

## BuildStep

`BuildStep` is the abstract base class for all build actions. Each step has:

- **`name`** — a unique string identifying the step within a DAG.
- **`run(config)`** — executes the step and returns a `BuildResult`.
- **`deps`** — a list of other steps this step depends on. Populated automatically by `resolve_deps()` when added to a `BuildDag`.

You do not need to subclass `BuildStep` directly for normal use. The concrete steps (`StreamUtilsStep`, `DataSchemaStep`, `ArrayUtilsStep`) cover the common cases.

### BuildResult

`run()` returns a `BuildResult`:

```python
@dataclass
class BuildResult:
    success: bool
    message: str = ""
    artifacts: dict[str, Path] = field(default_factory=dict)
```

The `artifacts` dict maps logical output names (e.g. `"include"`, `"tb_include"`) to the absolute paths of the files that were written.

---

## BuildDag

`BuildDag` manages a collection of steps. Steps are added one at a time with `add()`, which immediately calls `resolve_deps()` on the new step so it can wire itself to any compatible steps already in the DAG.

```python
from pysilicon.build.build import BuildDag

dag = BuildDag()
su_step = dag.add(StreamUtilsStep(output_dir="include"))
schema_step = dag.add(DataSchemaStep(PolyCmdHdr, word_bw_supported=[32, 64], include_dir="include"))
```

### Ordering and dependencies

Steps must be added in dependency order — a step's dependencies must already be in the DAG when it is added. In practice this means:

1. Add `StreamUtilsStep` first.
2. Add `DataSchemaStep` instances in dependency order (schemas that are fields of other schemas before the schemas that contain them).
3. Add `ArrayUtilsStep` instances last.

The DAG enforces that step names are unique and will raise `ValueError` if a duplicate is added.

### Running the DAG

```python
results = dag.run(cfg)
```

`run()` executes all steps in topological order and returns a `dict[str, BuildResult]` keyed by step name. If a step fails, every step that depends on it is skipped:

```python
results = dag.run(cfg)
for name, result in results.items():
    if not result.success:
        print(f"{name}: {result.message}")
```

### Retrieving output paths

Each `BuildResult` carries an `artifacts` dict. For schema steps the key `"include"` gives the path to the generated header:

```python
results = dag.run(cfg)
header_path = results["PolyCmdHdrStep"].artifacts["include"]
```

When building a list of paths to return from a helper function, collect the steps before running:

```python
schema_steps = [
    dag.add(DataSchemaStep(cls, word_bw_supported=[32, 64], include_dir="include"))
    for cls in SCHEMA_CLASSES
]
results = dag.run(cfg)
header_paths = [results[step.name].artifacts["include"] for step in schema_steps]
```

### Inspecting the DAG

`dag.info()` returns a list of dicts — one per step — with keys `"step"`, `"outputs"`, and `"deps"`. This is the machine-readable form, suitable for programmatic use or as an AI tool response:

```python
import json
print(json.dumps(dag.info(), indent=2))
```
```json
[
  {
    "step": "StreamUtilsStep",
    "outputs": ["include/streamutils_hls.h", "include/streamutils_tb.h"],
    "deps": []
  },
  {
    "step": "CoeffArrayStep",
    "outputs": ["include/coeff_array.h", "include/coeff_array_tb.h"],
    "deps": ["StreamUtilsStep"]
  },
  {
    "step": "PolyCmdHdrStep",
    "outputs": ["include/poly_cmd_hdr.h", "include/poly_cmd_hdr_tb.h"],
    "deps": ["StreamUtilsStep", "CoeffArrayStep"]
  }
]
```

`dag.describe()` renders the same data as a markdown table, useful for human review or embedding in documentation:

```python
print(dag.describe())
```

| Step | Outputs | Deps |
|---|---|---|
| StreamUtilsStep | include/streamutils_hls.h, include/streamutils_tb.h | — |
| CoeffArrayStep | include/coeff_array.h, include/coeff_array_tb.h | StreamUtilsStep |
| PolyCmdHdrStep | include/poly_cmd_hdr.h, include/poly_cmd_hdr_tb.h | StreamUtilsStep, CoeffArrayStep |
