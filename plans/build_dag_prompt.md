# Claude CLI Prompt: BuildDag Target-Based Artifact Redesign

## Task

Refactor `pysilicon/build/build.py` and `examples/poly/poly_demo.py` to implement the
target-based artifact model described below.  The goal is a DAG where steps declare their
interface (consumes, produces, params, description) as class-level attributes, and the DAG
injects the right values into `run()` automatically — no manual `deps=[]`, no
`results.get("StepName")`, no `config.params.get("key", default)` inside `run()`.

---

## Files to modify

- `pysilicon/build/build.py` — core DAG framework
- `examples/poly/poly_demo.py` — poly accelerator pipeline
- `tests/examples/test_poly_demo.py` — verify tests still pass (minimal changes expected)

**Do NOT touch:**
- `pysilicon/hw/arrayutils.py`
- `pysilicon/hw/dataschema.py`
- Any other file not listed above

---

## Part 1: `pysilicon/build/build.py`

### `BuildStep`

Replace the current `BuildStep` with this new dataclass.  The four class-level attributes
(`description`, `consumes`, `produces`, `params`) are declared as dataclass fields on the
base with empty defaults.  Subclasses override them as plain class attributes (no type
annotation in the subclass body), which shadows the dataclass fields without adding
constructor parameters.

```python
@dataclass(kw_only=True)
class BuildStep(ABC):
    description: str = ""
    consumes: list = field(default_factory=list)   # artifact names read from store
    produces: list = field(default_factory=list)   # artifact names written to store
    params: dict = field(default_factory=dict)     # config.params keys → default values

    @property
    def name(self) -> str:
        return type(self).__name__

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        """Return file paths this step would write, keyed by artifact name.
        Only steps that produce file (Path) artifacts need to implement this.
        Default: no file outputs."""
        return {}

    @abstractmethod
    def run(self, config: BuildConfig, **kwargs) -> dict[str, Any]:
        """Execute the step.  kwargs contains consumed artifacts and resolved
        config params, injected by the DAG.  Return a dict mapping artifact
        names (must match self.produces) to their values.
        Raise RuntimeError(message) to signal failure."""
        ...
```

Remove from `BuildStep`: `deps`, `optional`, `resolve_deps`, `is_fresh`.

### `BuildResult`

Simplify — artifacts are raw values (no `FileArtifact`/`ObjectArtifact` wrappers):

```python
@dataclass
class BuildResult:
    success: bool
    message: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def object(self, name: str) -> Any:
        return self.artifacts[name]

    def path(self, name: str) -> Path:
        return self.artifacts[name]
```

### Remove entirely

- `FileArtifact`, `ObjectArtifact`, `source_artifact`
- `Buildable` subclass
- `BuildConfig.needs_legacy_streamutils_cpp` can stay; `BuildConfig` itself is unchanged

### `BuildDag`

Add two new internal dicts:
- `_artifact_owners: dict[str, str]` — artifact name → step name that produces it
- `_step_by_name: dict[str, BuildStep]` — step name → step object

Update `__init__`:
```python
def __init__(self) -> None:
    self._steps: list[BuildStep] = []
    self._names: set[str] = set()
    self._artifact_owners: dict[str, str] = {}
    self._step_by_name: dict[str, BuildStep] = {}
```

Replace `add()` — no longer accepts `deps=[]`; derives deps from consumes/produces:

```python
def add(self, step: BuildStep) -> BuildStep:
    if step.name in self._names:
        raise ValueError(f"A step named '{step.name}' already exists in this BuildDag.")

    # Validate no artifact name collision
    for name in step.produces:
        if name in self._artifact_owners:
            raise ValueError(
                f"Artifact '{name}' already claimed by '{self._artifact_owners[name]}', "
                f"cannot also be produced by '{step.name}'."
            )
        self._artifact_owners[name] = step.name

    # Auto-derive deps from consumes
    step_deps = []
    for name in step.consumes:
        if name not in self._artifact_owners:
            raise ValueError(
                f"Step '{step.name}' consumes '{name}' but no registered step produces it."
            )
        dep_name = self._artifact_owners[name]
        if dep_name not in [d.name for d in step_deps]:
            step_deps.append(self._step_by_name[dep_name])
    # Store deps internally for topological sort (not user-facing)
    object.__setattr__(step, '_deps', step_deps) if hasattr(step, '__dataclass_fields__') else setattr(step, '_deps', step_deps)

    self._steps.append(step)
    self._names.add(step.name)
    self._step_by_name[step.name] = step
    return step
```

Note: `_topological_sort` currently reads `step.deps` — update it to read `step._deps`
instead (the internal list set by `add()`).

Replace `_call_run()`:

```python
@staticmethod
def _call_run(step, config, artifact_store):
    missing = [n for n in step.consumes if n not in artifact_store]
    if missing:
        raise RuntimeError(f"Step '{step.name}': missing consumed artifacts: {missing}")
    inputs = {n: artifact_store[n] for n in step.consumes}

    resolved_params = {
        n: config.params.get(n, default)
        for n, default in step.params.items()
    }

    produced = step.run(config, **inputs, **resolved_params)

    for key in step.produces:
        if key not in produced:
            raise RuntimeError(
                f"Step '{step.name}' declared '{key}' in produces but did not return it."
            )

    artifact_store.update(produced)
    return produced
```

Update `run()` to thread `artifact_store` instead of `results` into each step.  Steps
that fail (raise `RuntimeError`) are caught; their step name goes into `failed`.  Steps
that depend on failed steps are skipped.  `run()` continues to return
`dict[str, BuildResult]` keyed by step name.

```python
def run(self, config, through=None, ...) -> dict[str, BuildResult]:
    order = self._topological_sort()
    # ... through= filtering unchanged ...
    artifact_store: dict[str, Any] = {}
    results: dict[str, BuildResult] = {}
    failed: set[str] = set()

    for step in order:
        dep_failed = any(d.name in failed for d in step._deps)
        if dep_failed:
            results[step.name] = BuildResult(success=False, message="Skipped: dependency failed")
            failed.add(step.name)
            continue
        try:
            produced = self._call_run(step, config, artifact_store)
            results[step.name] = BuildResult(success=True, artifacts=produced)
        except Exception as exc:
            results[step.name] = BuildResult(success=False, message=str(exc))
            failed.add(step.name)

    return results
```

Remove `skip_fresh`, `include_optional`, and `optional` step handling for now — they
can be re-added once the new model is stable.

### Add `results_status(config)`

```python
def results_status(self, config: BuildConfig) -> list[dict]:
    """Pre-build freshness status for every file artifact in the DAG.

    Walks steps in topological order.  For each step, calls expected_paths(config)
    to get the paths it would write.  Checks existence, mtime, and staleness.

    A file artifact is stale if:
      - it does not exist, OR
      - any file artifact it directly or transitively depends on is newer than it.

    Returns a list of dicts, one per file artifact:
      artifact, produced_by, path, exists, mtime (float|None), stale (bool),
      stale_because (list[str] of artifact names that are newer).
    """
    order = self._topological_sort()

    # Map artifact name → {path, mtime, stale, stale_because}
    file_status: dict[str, dict] = {}
    entries: list[dict] = []

    for step in order:
        paths = step.expected_paths(config)
        if not paths:
            continue

        # Collect all file artifacts consumed by this step (direct deps only)
        consumed_file_artifacts = [
            name for name in step.consumes if name in file_status
        ]

        for artifact_name, path in paths.items():
            exists = path.exists()
            mtime = path.stat().st_mtime if exists else None
            stale_because = []

            if not exists:
                stale = True
            else:
                for dep_name in consumed_file_artifacts:
                    dep = file_status[dep_name]
                    # Stale if dep is stale (transitive) or dep is newer
                    if dep["stale"]:
                        stale_because.append(dep_name)
                    elif dep["mtime"] is not None and dep["mtime"] > mtime:
                        stale_because.append(dep_name)
                stale = bool(stale_because)

            entry = {
                "artifact": artifact_name,
                "produced_by": step.name,
                "path": path,
                "exists": exists,
                "mtime": mtime,
                "stale": stale,
                "stale_because": stale_because,
            }
            file_status[artifact_name] = entry
            entries.append(entry)

    return entries
```

### Update `info()` and `describe()`

`info()` returns one dict per step with keys: `step`, `description`, `consumes`,
`produces`, `params`, and file artifact paths from `expected_paths` if available.

`describe()` markdown table columns: Step | Description | Consumes | Produces | Params.

Update `step_names()` to use `step._deps` instead of `step.deps`.

---

## Part 2: `examples/poly/poly_demo.py`

### Remove from imports

Remove `FileArtifact`, `ObjectArtifact` from the `pysilicon.build.build` import line.

### All nine step classes

Add `description`, `consumes`, `produces`, `params` as class-level attributes (no type
annotation — plain class attributes that shadow the dataclass fields).  Rewrite `run()`
to receive injected kwargs and return `dict[str, Any]`.  Remove all
`results.get(...)`, `config.params.get(...)`, `ObjectArtifact(...)`, `FileArtifact(...)`
calls.

Add `expected_paths(config)` to the five steps that produce file artifacts.

#### `BuildInputsStep`

```python
@dataclass(kw_only=True)
class BuildInputsStep(BuildStep):
    description = "Create the command header and input sample vector."
    consumes    = []
    produces    = ["cmd_hdr", "samp_in"]
    params      = {"nsamp": 100}

    def run(self, config: BuildConfig, nsamp) -> dict[str, Any]:
        coeffs = CoeffArray()
        coeffs.val = np.array([1.0, -2.0, -3.0, 4.0], dtype=np.float32)
        cmd_hdr = PolyCmdHdr()
        cmd_hdr.tx_id = 42
        cmd_hdr.coeffs = coeffs.val
        cmd_hdr.nsamp = nsamp
        samp_in = np.linspace(0.0, 1.0, nsamp, dtype=np.float32)
        return {"cmd_hdr": cmd_hdr, "samp_in": samp_in}
```

No `expected_paths` needed (object-only produces).

#### `GenCppStep`

```python
@dataclass(kw_only=True)
class GenCppStep(BuildStep):
    description = "Generate schema and utility headers needed for the Vitis flow."
    consumes    = []
    produces    = ["include_dir"]
    params      = {}
    include_dir: str = "include"   # dataclass field — this IS an instance field

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        return {"include_dir": config.root_dir / self.include_dir}

    def run(self, config: BuildConfig) -> dict[str, Any]:
        inner_dag = BuildDag()
        inner_dag.add(StreamUtilsStep(output_dir=self.include_dir))
        for cls in SCHEMA_CLASSES:
            inner_dag.add(DataSchemaStep(cls, word_bw_supported=WORD_BW_SUPPORTED,
                                         include_dir=self.include_dir))
        inner_dag.add(ArrayUtilsStep(Float32, WORD_BW_SUPPORTED))
        inner_results = inner_dag.run(config)
        failed = [n for n, r in inner_results.items() if not r.success]
        if failed:
            raise RuntimeError(f"Code generation failed: {failed}")
        return {"include_dir": config.root_dir / self.include_dir}
```

Note: `include_dir` is both a class-level attribute (shadowing the base's `params` field)
and an instance-level dataclass field.  The instance field `include_dir: str = "include"`
is fine because it is annotated — Python's dataclass machinery will treat it as a field.
The class-level `params = {}` (no annotation) overrides the inherited field default.

#### `PySimStep`

```python
@dataclass(kw_only=True)
class PySimStep(BuildStep):
    description = "Run the Python SimPy simulation and capture the result."
    consumes    = ["cmd_hdr", "samp_in"]
    produces    = ["sim_result", "log"]
    params      = {"clk_freq": 100e6, "in_bw": 32, "out_bw": 32,
                   "unroll_factor": 1, "log_file": "sim_log.csv"}

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        log_file = config.params.get('log_file', self.params['log_file'])
        return {"log": config.root_dir / log_file}

    @staticmethod
    def _connect(sim, tb, accel, clk):
        ...  # unchanged

    def run(self, config: BuildConfig,
            cmd_hdr, samp_in,
            clk_freq, in_bw, out_bw, unroll_factor, log_file,
            ) -> dict[str, Any]:
        sim = Simulation()
        clk = Clock(freq=clk_freq)
        log_path = config.root_dir / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = Logger(name="poly_log", sim=sim, file_path=log_path,
                        fields=["event", "job"])
        accel = PolyAccelComponent(
            name="poly_accel", sim=sim,
            in_bw=in_bw, out_bw=out_bw, unroll_factor=unroll_factor,
            clk=clk, logger=logger,
        )
        tb = PolyTB(name="poly_tb", sim=sim,
                    cmd_hdr=cmd_hdr, samp_in=samp_in, word_bw=in_bw)
        self._connect(sim, tb, accel, clk)
        sim.run_sim()
        sim_result = PolySimResult(
            cmd_hdr=cmd_hdr, samp_in=samp_in,
            resp_hdr=tb.resp_hdr, samp_out=tb.samp_out, resp_ftr=tb.resp_ftr,
        )
        return {"sim_result": sim_result, "log": log_path}
```

#### `ValidateTimingStep`

```python
@dataclass(kw_only=True)
class ValidateTimingStep(BuildStep):
    description = "Read the simulation log and verify timing events are present."
    consumes    = ["log"]
    produces    = ["durations"]
    params      = {}

    def run(self, config: BuildConfig, log) -> dict[str, Any]:
        import csv
        events: dict[str, float] = {}
        with open(log, newline="") as f:
            for row in csv.DictReader(f):
                ev = row["event"]
                if ev not in events:
                    events[ev] = float(row["time"])
        t_start = events.get("samp_read_begin")
        t_end = events.get("samp_out_write_end")
        if t_start is None or t_end is None:
            raise RuntimeError(f"Missing timing events in log: {list(events)}")
        return {"durations": {"samp_read_to_write_end": t_end - t_start}}
```

#### `WriteInputsStep`

```python
@dataclass(kw_only=True)
class WriteInputsStep(BuildStep):
    description = "Write binary test-vector files for the Vitis testbench."
    consumes    = ["cmd_hdr", "samp_in"]
    produces    = ["data_dir"]
    params      = {}

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        return {"data_dir": config.root_dir / "data"}

    def run(self, config: BuildConfig, cmd_hdr, samp_in) -> dict[str, Any]:
        out_dir = config.root_dir / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd_hdr.write_uint32_file(out_dir / "cmd_hdr_data.bin")
        write_uint32_file(samp_in, elem_type=Float32,
                          file_path=out_dir / "samp_in_data.bin",
                          nwrite=cmd_hdr.nsamp)
        return {"data_dir": out_dir}
```

#### `CSimStep`

```python
@dataclass(kw_only=True)
class CSimStep(BuildStep):
    description = "Invoke Vitis HLS C-simulation."
    consumes    = ["include_dir", "data_dir"]
    produces    = ["csim_data_dir"]
    params      = {"live_output": False}

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        return {"csim_data_dir": config.root_dir / "data"}

    def run(self, config: BuildConfig, include_dir, data_dir,
            live_output) -> dict[str, Any]:
        vitis_env = {"PYSILICON_POLY_COSIM": "0",
                     "PYSILICON_POLY_TRACE_LEVEL": "none"}
        try:
            result = toolchain.run_vitis_hls(
                config.root_dir / "run.tcl",
                work_dir=config.root_dir,
                capture_output=not live_output,
                env=vitis_env,
            )
            if result.stdout: print(result.stdout)
            if result.stderr: print(result.stderr)
        except Exception as exc:
            raise RuntimeError(str(exc))
        return {"csim_data_dir": data_dir}
```

#### `ValidateCSimStep`

```python
@dataclass(kw_only=True)
class ValidateCSimStep(BuildStep):
    description = "Compare Vitis C-sim outputs against the Python model."
    consumes    = ["sim_result", "csim_data_dir"]
    produces    = ["vitis_result"]
    params      = {}

    def run(self, config: BuildConfig, sim_result, csim_data_dir) -> dict[str, Any]:
        data_dir = csim_data_dir
        try:
            got_resp_hdr = PolyRespHdr().read_uint32_file(data_dir / "resp_hdr_data.bin")
            got_resp_ftr = PolyRespFtr().read_uint32_file(data_dir / "resp_ftr_data.bin")
            got_samp_out = np.asarray(
                read_uint32_file(data_dir / "samp_out_data.bin",
                                 elem_type=Float32,
                                 shape=int(sim_result.resp_ftr.nsamp_read)),
                dtype=np.float32,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to read Vitis outputs: {exc}")
        if not got_resp_hdr.is_close(sim_result.resp_hdr):
            raise RuntimeError("Response header mismatch after Vitis C-simulation.")
        if not got_resp_ftr.is_close(sim_result.resp_ftr):
            raise RuntimeError("Response footer mismatch after Vitis C-simulation.")
        if not np.allclose(got_samp_out, sim_result.samp_out[:got_samp_out.size],
                           rtol=1e-6, atol=1e-6):
            raise RuntimeError("Sample output mismatch after Vitis C-simulation.")
        sync_status_path = data_dir / "sync_status.json"
        if sync_status_path.exists():
            sync_status = json.loads(sync_status_path.read_text(encoding="utf-8"))
            expected_sync = {"resp_hdr_tlast": "tlast_at_end",
                             "samp_out_tlast": "tlast_at_end",
                             "resp_ftr_tlast": "tlast_at_end"}
            if sync_status != expected_sync:
                raise RuntimeError(
                    f"TLAST sync mismatch. Expected {expected_sync}, got {sync_status}.")
        vitis_result = PolySimResult(
            cmd_hdr=sim_result.cmd_hdr, samp_in=sim_result.samp_in,
            resp_hdr=got_resp_hdr, samp_out=got_samp_out, resp_ftr=got_resp_ftr,
        )
        return {"vitis_result": vitis_result}
```

#### `CSynthStep`

```python
@dataclass(kw_only=True)
class CSynthStep(BuildStep):
    description = "Run Vitis HLS C-synthesis and RTL co-simulation."
    consumes    = ["include_dir", "csim_data_dir"]
    produces    = ["report_dir"]
    params      = {"live_output": False}

    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        return {"report_dir": config.root_dir / "pysilicon_poly_proj" / "solution1"}

    def run(self, config: BuildConfig, include_dir, csim_data_dir,
            live_output) -> dict[str, Any]:
        vitis_env = {"PYSILICON_POLY_COSIM": "1",
                     "PYSILICON_POLY_TRACE_LEVEL": "none"}
        try:
            result = toolchain.run_vitis_hls(
                config.root_dir / "run.tcl",
                work_dir=config.root_dir,
                capture_output=not live_output,
                env=vitis_env,
            )
            if result.stdout: print(result.stdout)
            if result.stderr: print(result.stderr)
        except Exception as exc:
            raise RuntimeError(str(exc))
        report_dir = config.root_dir / "pysilicon_poly_proj" / "solution1"
        return {"report_dir": report_dir}
```

#### `InspectSynthStep`

```python
@dataclass(kw_only=True)
class InspectSynthStep(BuildStep):
    description = "Parse the Vitis HLS C-synthesis report and print resource/timing tables."
    consumes    = ["report_dir"]
    produces    = ["loop_df"]
    params      = {}

    def run(self, config: BuildConfig, report_dir) -> dict[str, Any]:
        # body unchanged except: use report_dir directly (not csyn_result.path(...))
        # raise RuntimeError(...) instead of returning BuildResult(success=False, ...)
        ...
        return {"loop_df": parser.loop_df}
```

### `build_poly_dag()`

```python
def build_poly_dag() -> BuildDag:
    dag = BuildDag()
    dag.add(BuildInputsStep())    # produces: cmd_hdr, samp_in
    dag.add(GenCppStep())         # produces: include_dir
    dag.add(PySimStep())          # consumes: cmd_hdr, samp_in      → produces: sim_result, log
    dag.add(ValidateTimingStep()) # consumes: log                   → produces: durations
    dag.add(WriteInputsStep())    # consumes: cmd_hdr, samp_in      → produces: data_dir
    dag.add(CSimStep())           # consumes: include_dir, data_dir → produces: csim_data_dir
    dag.add(ValidateCSimStep())   # consumes: sim_result, csim_data_dir → produces: vitis_result
    dag.add(CSynthStep())         # consumes: include_dir, csim_data_dir → produces: report_dir
    dag.add(InspectSynthStep())   # consumes: report_dir            → produces: loop_df
    return dag
```

No `deps=[...]` anywhere.

### `main()` — add `--status` flag

```python
parser.add_argument("--status", action="store_true",
                    help="Print pre-build freshness status for file artifacts and exit.")
...
if args.status:
    import time as _time
    now = _time.time()
    for entry in dag.results_status(config):
        age = f"{(now - entry['mtime']) / 3600:.1f}h ago" if entry['mtime'] else "—"
        exists_mark = "✓" if entry['exists'] else "✗"
        stale_note = f"  STALE ({', '.join(entry['stale_because'])} newer)" if entry['stale'] else ""
        print(f"  {entry['artifact']:<16} {entry['produced_by']:<22} "
              f"{exists_mark}  {age:<12}{stale_note}")
    return
```

---

## Part 3: `tests/examples/test_poly_demo.py`

The tests call `.object(name)` and `.path(name)` on `BuildResult` — these still work
(the simplified `BuildResult` keeps both methods).  The only expected change: the Vitis
test previously checked `results.get('CSimStep')` — it still does, but `CSimStep` now
produces `csim_data_dir` instead of `data_dir`.  Update any artifact name references
accordingly.

---

## Constraints

- Do not break `pytest tests/examples/test_poly_demo.py -m "not vitis"` — run this to verify.
- Do not touch `pysilicon/hw/arrayutils.py` or `pysilicon/hw/dataschema.py`.
- The inner DAG inside `GenCppStep.run()` uses `StreamUtilsStep`, `DataSchemaStep`,
  `ArrayUtilsStep` — these are `Buildable` subclasses that use `resolve_deps`.  They must
  continue to work unchanged.  The inner `BuildDag` that runs them must still support the
  legacy `resolve_deps` path OR `GenCppStep` must construct it using the old `BuildDag`
  API.  Simplest fix: keep a private `_LegacyBuildDag` that supports `resolve_deps` and
  use it only inside `GenCppStep`, OR keep `resolve_deps` support in the main `BuildDag`
  as a no-op (steps that don't override it just get empty deps, which is fine since
  `add()` now derives deps from consumes/produces anyway).
- Write no new comments beyond what already exists in the file.
- Prefer editing existing files over creating new ones.
