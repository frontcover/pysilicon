# Plan: `vitis_regmap_simp_fun` Teaching Interface Example

## 1) Purpose and teaching goals

Create a teaching-first AXI Lite interface example named `vitis_regmap_simp_fun` that demonstrates a `VitisRegMap` kernel and the full workflow students should learn:

1. Python simulation and functional check
2. RTL/codegen generation
3. Vitis HLS C-sim and C-synthesis
4. RTL co-simulation and timing extraction
5. Timing-diagram generation as a first-class build artifact

The arithmetic should be slightly more interesting than an adder (for example `y = relu(a*x + b)`), while keeping the exact function swappable later without redesigning the flow.

## 2) Proposed directory and file layout

### Example code

- `examples/interface/vitis_regmap_simp_fun/`
  - `README.md` — quick run instructions and artifact checklist
  - `simp_fun.py` — schemas, kernel component, optional Python TB helper(s)
  - `simp_fun_build.py` — `build_dag` orchestration (poly-style)
  - `timing_diagram.py` — timing post-processing + annotated diagram generation helper
  - `run.tcl` — Vitis HLS driver script for csim/csynth/cosim
  - `data/` — generated inputs (gitignored)
  - `gen/` — generated C++/headers (gitignored)
  - `results/` — logs/reports/diagrams (gitignored)

### Guide docs

- `docs/guide/interface/vitis_regmap_simp_fun/`
  - `index.md` — concept + register map + end-to-end flow
  - `simulation.md` — Python sim and expected behavior
  - `synthesis.md` — HLS/cosim/timing interpretation

## 3) Proposed code architecture

### Kernel component (`simp_fun.py`)

- Single `HwComponent` using `VitisRegMap` (AXI Lite).
- User-visible register fields (initial proposal):
  - inputs: `x`, `a`, `b`
  - output: `y`
  - status/debug (optional): `error_code` or `status`
- Uses Vitis control semantics (`ap_start`, `ap_done`, `ap_idle`) through `VitisRegMap`.
- Core scalar compute initially implemented as a simple function hook (default candidate: `relu(a*x + b)`).

### Testbench/simulation strategy (`simp_fun.py` and/or helper)

- Python simulation transaction writes regs, starts kernel, waits completion, reads result.
- Includes a few deterministic vectors to show clamp/threshold behavior (for relu-like function).
- Produces a compact machine-readable artifact (JSON) for expected output and cycle timing summary.

### Build script (`simp_fun_build.py`)

- Implements a `BuildDag` in the same spirit as `examples/poly/poly_build.py`.
- Keeps stage names explicit and listable from CLI (`--list-steps`, `--through`).
- Uses `BuildConfig.params` for clock and example parameters (e.g., bit-width/function mode).

### Timing analysis helper (`timing_diagram.py`)

- Reads timing sources (Py sim log + cosim timing/VCD-derived events as available).
- Produces:
  - structured timing JSON (cycle-level events)
  - annotated timing-diagram artifact (e.g., SVG/PNG/Markdown + table)
- Invoked by DAG step (not manual ad-hoc script usage).

### `run.tcl`

- Mirrors the poly flow pattern for Vitis execution.
- Controlled via environment variables from DAG steps (clock period, csim vs cosim mode, trace controls).

## 4) Proposed build DAG steps and produced artifacts

Suggested DAG groups and steps (names may be adjusted to match repo conventions):

1. **Python golden model**
   - `build_inputs` → `data/*.bin` or JSON input vectors
   - `py_sim` → `results/sim/` + `results/sim_log.csv`
   - `extract_py_timing` → `results/py_timing.json`

2. **HLS code generation**
   - `gen_include` (if needed) → `include/`
   - `gen_kernel` → generated kernel C++ in `gen/`
   - `gen_tb` (if testbench codegen is used) → generated TB C++ in `gen/`

3. **C-sim functional verification**
   - `csim` → Vitis csim outputs under `data/` or `results/vitis/`
   - `validate_csim` → `results/verify_csim.json`

4. **C-synth and RTL cosim**
   - `csynth` (with cosim enabled in flow) → `pysilicon_*_proj/solution1`
   - `inspect_synth` → parsed synth summary (CSV/JSON)

5. **Timing extraction + diagram generation (required integration)**
   - `extract_cosim_timing` → `results/cosim_timing.json`
   - `validate_timing` → `results/timing_verdict.json`
   - `generate_timing_diagram` (new integrated step) →
     - `results/timing_diagram.svg` (or `.png`)
     - `results/timing_diagram.json` (annotated event metadata)

**Key requirement vs `examples/poly`:** timing-diagram generation is part of DAG execution and produces committed, named artifacts from a normal build step.

## 5) Documentation structure for guide pages

### `index.md`

- What `VitisRegMap` teaches in this example
- Register map and control flow (`ap_start` to result ready)
- File map and build DAG overview
- Link to simulation and synthesis pages

### `simulation.md`

- Commands for Python simulation stage
- Step-by-step register transaction narrative
- Expected outputs and how to read sim logs

### `synthesis.md`

- Commands for HLS/csim/csynth/cosim stages
- How timing is extracted and compared
- How to read generated timing diagram and annotations

## 6) Open design questions / decisions to confirm

1. **Function definition:** keep `relu(a*x + b)` as default, or select another scalar function?
2. **Numeric type policy:** fixed-point vs integer vs float for teaching simplicity.
3. **Register map scope:** include only `x,a,b,y` or add explicit status/error/debug fields.
4. **Timing diagram format:** SVG only vs SVG + PNG + JSON metadata.
5. **Trace source for timing annotations:** cosim report only, VCD parsing, or both.
6. **Tolerance policy:** what cycle tolerance should `validate_timing` enforce for this example.

## 7) Suggested phased implementation order

1. **Scaffold example/docs directories** and add README + doc stubs.
2. **Implement kernel + Python simulation** and validate expected function behavior.
3. **Add build DAG baseline** (through `extract_py_timing`) with CLI controls.
4. **Add codegen + csim + csynth/cosim** steps using `run.tcl`.
5. **Integrate timing extraction and verdict** (py vs cosim cycle checks).
6. **Add `generate_timing_diagram` DAG step** and produce annotated diagram artifacts.
7. **Complete guide pages** with command snippets and artifact interpretation.
8. **Polish acceptance checks** and ensure full flow runs with one DAG command.

## 8) Acceptance criteria

Implementation is complete when all are true:

- New example exists at `examples/interface/vitis_regmap_simp_fun/` with a runnable poly-style `build_dag` entrypoint.
- New guide exists at `docs/guide/interface/vitis_regmap_simp_fun/` with `index.md`, `simulation.md`, and `synthesis.md`.
- Kernel demonstrates AXI Lite `VitisRegMap` control/data path with a simple scalar function more interesting than an adder.
- Build flow covers Python sim, codegen, csim, csynth/cosim, timing extraction, and timing validation.
- Timing-diagram generation runs as an explicit DAG step and emits annotated timing-diagram artifact(s).
- Example can be executed in staged mode (`--through`) and full mode, with clearly named outputs in `results/`.
- Docs map directly to the runnable commands and produced artifacts, suitable for teaching end-to-end flow.
