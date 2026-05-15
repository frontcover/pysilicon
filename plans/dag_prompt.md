# Claude CLI Prompt: Poly BuildDag Refactor

This file preserves the Claude CLI task prompt used to refactor `poly_demo.py`
to use a single canonical `BuildDag` instead of scattered imperative code.

---

## Task prompt

Refactor `examples/poly/poly_demo.py` and `pysilicon/build/build.py` to implement
the design in `plans/build_plan.md`.  The goal is one canonical `build_poly_dag()`
function that covers the full poly flow, replacing all ad-hoc imperative code.

### Specific requirements

**In `pysilicon/build/build.py`:**

1. Add `BuildArtifact` (ABC), `FileArtifact`, `ObjectArtifact`, `source_artifact()`.
2. Change `BuildResult.artifacts` from `dict[str, Path]` to `dict[str, BuildArtifact]`.
   Add `BuildResult.timestamp`, `.object(name)`, `.path(name)` accessors.
3. Change `BuildStep.run(config)` → `run(config, results={})`.
   Add `BuildStep.optional: bool = False` and `is_fresh(config, results) -> bool`.
4. Change `BuildDag.run()` to:
   - Thread `results` dict into each step's `run()` call.
   - Accept `skip_fresh: bool = False` — skip steps whose `is_fresh()` returns True.
   - Accept `include_optional: list[str] | None = None` — opt-in optional steps.
   - Accept `through: str | None = None` — run only the named step and its
     transitive dependencies; raise `ValueError` if no such step exists.
5. Add `BuildDag.step_names() -> list[str]` returning step names in execution order.
6. Use `inspect.signature` in `BuildDag._call_run()` so that old `run(config)`
   callers do not break.

**In `examples/poly/poly_demo.py`:**

7. Create these `BuildStep` subclasses (each with `resolve_deps` and `run`):
   - `BuildInputsStep(nsamp)` — creates `PolyCmdHdr` and `samp_in`; no deps.
   - `GenCppStep(example_dir, include_dir)` — generates schema/utility headers;
     no deps; produces `FileArtifact(path=include_dir)`.
   - `PySimStep(log_file, in_bw, out_bw, unroll_factor)` — runs the SimPy sim;
     deps: `[BuildInputsStep]`; produces `ObjectArtifact(sim_result)` and
     optionally `FileArtifact(log)`.
   - `ValidateTimingStep(proc_latency, period)` — reads the log and checks timing;
     deps: `[PySimStep]`; produces `ObjectArtifact(durations)`.
   - `WriteInputsStep(example_dir, data_dir)` — writes binary test vectors;
     deps: `[BuildInputsStep, PySimStep]`; produces `FileArtifact(data_dir)`.
   - `CSimStep(example_dir, live_output)` — runs Vitis HLS csim;
     deps: `[GenCppStep, WriteInputsStep]`; produces `FileArtifact(data_dir)`.
   - `ValidateCSimStep()` — compares Vitis outputs against Python model;
     deps: `[PySimStep, CSimStep]`; produces `ObjectArtifact(vitis_result)`.
   - `CSynthStep(example_dir, live_output)` — runs Vitis HLS csynth;
     deps: `[GenCppStep]`; produces `FileArtifact(report_dir)`.
   - `InspectSynthStep()` — parses the csynth XML report;
     deps: `[CSynthStep]`; produces `ObjectArtifact(loop_df)`.

8. Create `build_poly_dag(nsamp, in_bw, out_bw, unroll_factor, log_file,
   example_dir, live_output) -> BuildDag` that registers all steps above in
   dependency order and returns the DAG.

9. Rewrite `PolyTest` as a thin shim over `build_poly_dag()`:
   - `simulate(log_file, in_bw, out_bw, unroll_factor)` →
     calls `build_poly_dag(...).run(config, through='ValidateTimingStep')`,
     extracts `sim_result` from `results['PySimStep'].object('sim_result')`,
     populates `self.cmd_hdr / samp_in / resp_hdr / samp_out / resp_ftr`,
     returns the `PolySimResult`.
   - `gen_vitis_code()` →
     calls `build_poly_dag(...).run(config, through='GenCppStep')`.
   - All other `PolyTest` methods (`write_input_files`, `read_vitis_outputs`,
     `report_synthesis`, `test_vitis`, `maybe_plot`, `analyze_timing`,
     `generate_vcd`) keep their existing implementations unchanged.

10. Rewrite `main()` to:
    - Accept `--through STEP` (default `"ValidateTimingStep"`).
    - Accept `--list-steps` (print step names and exit).
    - Accept `--nsamp`, `--in-bw`, `--out-bw`, `--unroll-factor`, `--log`,
      `--live-output`.
    - Call `build_poly_dag(...)` with the given arguments and
      `dag.run(config, through=args.through)`.
    - Print `"  {name}: OK"` or `"  {name}: FAILED: {message}"` for each result.

### Constraints

- Do NOT break `PolyTest.simulate()`, `PolyTest.test_vitis()`, or the
  existing `tests/examples/test_poly_demo.py` tests.
- Keep all existing `PolyTest` method signatures unchanged.
- Do not add any new dependencies (all imports already exist in the file).
- Prefer editing existing files over creating new ones.
- Write no comments beyond what already exists.
