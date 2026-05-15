# BuildDag Redesign: Target-Based Artifact Model

## Motivation

The current DAG is **step-based**: nodes are steps, edges are step-to-step dependencies.
Steps fish values out of a `results: dict[str, BuildResult]` dict by hardcoding peer step
names (`results.get("BuildInputsStep")`), which couples them to implementation rather than
contract.  Config params are similarly scattered — each step calls `config.params.get('nsamp', 100)`
internally, making the configuration surface invisible to introspection.

The better model — used by make, Ninja, Bazel — is **target-based**: nodes are *artifacts*,
edges are artifact-to-artifact dependencies.  Steps are just transformation rules that
declare what they consume and produce.

### Goals

1. Steps explicitly declare `consumes`, `produces`, `params`, and `description` as
   **class-level attributes** — the step's interface is visible without reading `run()`.
2. The DAG injects consumed artifacts and config params directly into `run()` as kwargs.
3. `run()` returns a plain `dict[str, Any]` — the artifacts it produced.
4. The DAG validates no two steps claim the same artifact name, and that every consumed
   artifact is produced by some earlier step.
5. `BuildDag.info()` returns a complete machine-readable picture: description, consumes,
   produces, params — useful for AI agents and `--list-steps` output.

---

## New `BuildStep` Interface

`consumes`, `produces`, `params`, and `description` are plain class attributes on each
step subclass — not dataclass fields, not auto-derived from the signature.  They describe
the step *type*, not any particular instance.  The base class provides empty defaults.

```python
@dataclass(kw_only=True)
class BuildStep(ABC):
    # Base defaults — subclasses override these as class-level attributes
    description: str = ""
    consumes: list[str] = []   # artifact names read from the global store
    produces: list[str] = []   # artifact names written to the global store
    params: dict[str, Any] = {}  # config.params keys → default values

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def run(self, config: BuildConfig, **kwargs) -> dict[str, Any]:
        """Execute the step.

        The DAG injects consumed artifacts and resolved config params as kwargs.
        Return a dict whose keys match self.produces.
        Raise RuntimeError(message) to signal failure.
        """
        ...
```

**Note:** because `consumes`, `produces`, `params`, and `description` are declared without
type annotations in subclasses (they shadow the dataclass fields), Python treats them as
plain class attributes — not constructor parameters.  Instances of different steps share
the same class-level values.

### Example step — before and after

**Before:**
```python
@dataclass(kw_only=True)
class BuildInputsStep(BuildStep):
    def run(self, config: BuildConfig, results: dict = {}) -> BuildResult:
        nsamp = config.params.get('nsamp', 100)
        ...
        return BuildResult(success=True, artifacts={
            "cmd_hdr": ObjectArtifact(value=cmd_hdr),
            "samp_in": ObjectArtifact(value=samp_in),
        })
```

**After:**
```python
@dataclass(kw_only=True)
class BuildInputsStep(BuildStep):
    description = "Create the command header and input sample vector."
    consumes    = []
    produces    = ["cmd_hdr", "samp_in"]
    params      = {"nsamp": 100}

    def run(self, config: BuildConfig, nsamp) -> dict[str, Any]:
        samp_in = np.linspace(0.0, 1.0, nsamp, dtype=np.float32)
        ...
        return {"cmd_hdr": cmd_hdr, "samp_in": samp_in}
```

**Before:**
```python
@dataclass(kw_only=True)
class PySimStep(BuildStep):
    def run(self, config: BuildConfig, results: dict = {}) -> BuildResult:
        inputs_result = results.get("BuildInputsStep")
        cmd_hdr = inputs_result.object("cmd_hdr")
        samp_in = inputs_result.object("samp_in")
        clk_freq = config.params.get('clk_freq', 100e6)
        in_bw    = config.params.get('in_bw', 32)
        ...
```

**After:**
```python
@dataclass(kw_only=True)
class PySimStep(BuildStep):
    description = "Run the Python SimPy simulation and capture the result."
    consumes    = ["cmd_hdr", "samp_in"]
    produces    = ["sim_result", "log"]
    params      = {"clk_freq": 100e6, "in_bw": 32, "out_bw": 32,
                   "unroll_factor": 1, "log_file": "sim_log.csv"}

    def run(self, config: BuildConfig,
            cmd_hdr, samp_in,           # consumed artifacts
            clk_freq, in_bw, out_bw,    # config params
            unroll_factor, log_file,
            ) -> dict[str, Any]:
        ...
        return {"sim_result": sim_result, "log": log_path}
```

The `run()` signature names the kwargs explicitly for IDE support and readability, but
the DAG uses `step.consumes` and `step.params` — not the signature — to decide what to
inject.

---

## `BuildDag._call_run()` Mechanics

```python
@staticmethod
def _call_run(step, config, artifact_store):
    # Inject consumed artifacts
    missing = [name for name in step.consumes if name not in artifact_store]
    if missing:
        raise RuntimeError(f"Missing artifacts for {step.name}: {missing}")
    inputs = {name: artifact_store[name] for name in step.consumes}

    # Inject config params, falling back to per-step defaults
    params = {
        name: config.params.get(name, default)
        for name, default in step.params.items()
    }

    produced = step.run(config, **inputs, **params)

    # Validate step produced what it declared
    for key in step.produces:
        if key not in produced:
            raise RuntimeError(
                f"Step '{step.name}' declared '{key}' in produces but did not return it"
            )

    artifact_store.update(produced)
    return produced
```

---

## Ordering / Dependency Derivation

`BuildDag.add()` no longer accepts `deps=[]`.  On each `add()` call the DAG looks up the
producing step for every name in `step.consumes` and records an implicit edge.  The order
of `add()` calls must be consistent with the artifact dependency graph (a step must be
added after all steps that produce its consumed artifacts).

```python
def add(self, step: BuildStep) -> BuildStep:
    # Validate no artifact name collision
    for name in step.produces:
        if name in self._artifact_owners:
            raise ValueError(
                f"Artifact '{name}' already produced by '{self._artifact_owners[name]}'"
            )
        self._artifact_owners[name] = step.name

    # Derive deps from consumes
    step_deps = []
    for name in step.consumes:
        if name not in self._artifact_owners:
            raise ValueError(
                f"Step '{step.name}' consumes '{name}' but no registered step produces it"
            )
        dep_name = self._artifact_owners[name]
        step_deps.append(self._step_by_name[dep_name])
    step._deps = step_deps  # internal edge list for topological sort

    self._steps.append(step)
    self._step_by_name[step.name] = step
    return step
```

`build_poly_dag()` needs no `deps=[...]` anywhere.  The consumes/produces declarations
on each class drive everything:

```python
def build_poly_dag() -> BuildDag:
    dag = BuildDag()
    dag.add(BuildInputsStep())    # produces: cmd_hdr, samp_in
    dag.add(GenCppStep())         # produces: include_dir
    dag.add(PySimStep())          # consumes: cmd_hdr, samp_in     → produces: sim_result, log
    dag.add(ValidateTimingStep()) # consumes: log                  → produces: durations
    dag.add(WriteInputsStep())    # consumes: cmd_hdr, samp_in  → produces: data_dir
    dag.add(CSimStep())           # consumes: include_dir, data_dir → produces: csim_data_dir
    dag.add(ValidateCSimStep())   # consumes: sim_result, csim_data_dir → produces: vitis_result
    dag.add(CSynthStep())         # consumes: include_dir, csim_data_dir → produces: report_dir
    dag.add(InspectSynthStep())   # consumes: report_dir           → produces: loop_df
    return dag
```

The comments are redundant with the class declarations but useful for readers scanning
the builder.

---

## `BuildDag.run()` Return Value

`run()` continues to return `dict[str, BuildResult]` keyed by step name so that existing
test assertions (`results['PySimStep'].success`) keep working.  `BuildResult` is
simplified — artifacts are raw values, no wrappers:

```python
@dataclass
class BuildResult:
    success: bool
    message: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)

    def object(self, name: str) -> Any:
        return self.artifacts[name]

    def path(self, name: str) -> Path:
        return self.artifacts[name]
```

`FileArtifact` and `ObjectArtifact` wrappers are removed.  Steps return raw Python
objects and `Path` values.

---

## Pre-Build Status: `BuildDag.results_status(config)`

Steps that produce file artifacts implement an `expected_paths(config) -> dict[str, Path]`
hook (default returns `{}`).  Object artifacts are ephemeral and have no on-disk presence,
so they are omitted from pre-build status.

```python
# In BuildStep base:
def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
    """Return paths this step would write, given config.  Default: no file outputs."""
    return {}

# Example implementation:
class PySimStep(BuildStep):
    def expected_paths(self, config: BuildConfig) -> dict[str, Path]:
        log_file = config.params.get('log_file', self.params['log_file'])
        return {"log": config.root_dir / log_file}
```

Steps that only produce object artifacts (`BuildInputsStep`, `ValidateTimingStep`,
`ValidateCSimStep`, `InspectSynthStep`) leave `expected_paths` as the default `{}`.

`BuildDag.results_status(config)` walks every step in topological order, calls
`expected_paths`, checks each path's existence and mtime, and determines staleness
recursively: a file artifact is **stale** if it is older than any direct consumed file
artifact, or if any direct consumed file artifact is itself stale.

```python
def results_status(self, config: BuildConfig) -> list[dict]:
    """Return pre-build freshness status for every file artifact in the DAG."""
```

Each entry in the returned list:

```python
{
    "artifact": "log",
    "produced_by": "PySimStep",
    "path": Path(".../sim_log.csv"),
    "exists": True,
    "mtime": 1747123456.0,   # None if not exists
    "stale": False,          # True if older than any (transitively) consumed file artifact
    "stale_because": [],     # list of artifact names that are newer
}
```

Example CLI output from `--status`:

```
include_dir   GenCppStep        ✓  built 2h ago   fresh
log           PySimStep         ✓  built 2h ago   STALE  (include_dir is newer)
data_dir      WriteInputsStep   ✗  missing
csim_data_dir CSimStep          ✗  missing
report_dir    CSynthStep        ✗  missing
```

### File artifacts per step (poly flow)

| Step | Artifact | Path formula |
|---|---|---|
| `GenCppStep` | `include_dir` | `config.root_dir / include_dir` |
| `PySimStep` | `log` | `config.root_dir / log_file` |
| `WriteInputsStep` | `data_dir` | `config.root_dir / "data"` |
| `CSimStep` | `csim_data_dir` | `config.root_dir / "data"` |
| `CSynthStep` | `report_dir` | `config.root_dir / "pysilicon_poly_proj/solution1"` |

`expected_paths` duplicates the path formula from `run()` — one line per file artifact.
This is the same trade-off `Buildable.build_outputs` made; the duplication is small and
localised to the step class.

---

## `BuildDag.info()` Output

Each entry includes all four class-level declarations:

```python
{
    "step": "PySimStep",
    "description": "Run the Python SimPy simulation and capture the result.",
    "consumes": ["cmd_hdr", "samp_in"],
    "produces": ["sim_result", "log"],
    "params": {
        "clk_freq": 100000000.0,
        "in_bw": 32,
        "out_bw": 32,
        "unroll_factor": 1,
        "log_file": "sim_log.csv"
    },
    "optional": false
}
```

`BuildDag.describe()` (markdown table) is updated accordingly.
`--list-steps` in the CLI prints `name: description` for each step.

---

## Changes Required

### `pysilicon/build/build.py`

- `BuildStep`: add `description`, `consumes`, `produces`, `params` as class-level
  attributes with empty/default values.  Remove `resolve_deps`, `optional`, `deps` field,
  `is_fresh`.  Change `run()` to `run(self, config, **kwargs) -> dict[str, Any]`.
- `BuildResult.artifacts`: change from `dict[str, BuildArtifact]` to `dict[str, Any]`.
  `.object()` and `.path()` return the raw value directly.
- Remove `FileArtifact`, `ObjectArtifact`, `source_artifact`.
- `BuildDag`: add `_artifact_owners: dict[str, str]` and `_step_by_name: dict[str, BuildStep]`.
  Update `add()` to auto-derive deps from consumes/produces (no `deps=[]` parameter).
  Update `_call_run()` as described above.  Update `info()` / `describe()`.
  Add `results_status(config) -> list[dict]` for pre-build freshness reporting.
- `BuildDag.run()`: drive execution via `artifact_store` dict; continue returning
  `dict[str, BuildResult]`.
- Remove `Buildable` subclass (not used in the poly flow; can be revisited later).

### `examples/poly/poly_demo.py`

All nine step classes: add `description`, `consumes`, `produces`, `params` as class
attributes.  Rewrite `run()` to accept injected kwargs, return `dict[str, Any]`.
Remove all `results.get(...)`, `config.params.get(...)`, `ObjectArtifact(...)`,
`FileArtifact(...)` calls.  Rewrite `build_poly_dag()` with no `deps=[...]`.
Add `expected_paths(config)` to the five steps that produce file artifacts
(`GenCppStep`, `PySimStep`, `WriteInputsStep`, `CSimStep`, `CSynthStep`).

### `tests/examples/test_poly_demo.py`

`.object(name)` and `.path(name)` continue to work.  No other test changes expected.

### `pysilicon/hw/arrayutils.py`, `pysilicon/hw/dataschema.py`

These use the legacy `Buildable` / `resolve_deps` pattern and are not part of this
redesign.  Leave as-is; migrate separately after `Buildable` is re-evaluated.

---

## Ephemeral vs Persistent Artifacts

Object artifacts (Python objects in the artifact store) are **ephemeral** — they exist
only for the lifetime of a single `dag.run()` call.  File artifacts (Path values) are
**persistent** — they survive between CLI runs and participate in freshness checking.

In the poly flow this means:

| Artifact | Type | Persistent? | Note |
|---|---|---|---|
| `cmd_hdr`, `samp_in` | object | ✗ | but already written as uint32 binary by `WriteInputsStep` |
| `sim_result` | object | ✗ | numpy arrays + schema objects; expensive to recompute |
| `durations` | object | ✗ | trivially serialisable (plain dict) |
| `vitis_result` | object | ✗ | same structure as sim_result |
| `loop_df` | object | ✗ | pandas DataFrame; trivially serialisable |
| `log` | file | ✓ | |
| `data_dir` | file | ✓ | contains cmd_hdr and samp_in as uint32 binaries |
| `include_dir` | file | ✓ | |
| `csim_data_dir` | file | ✓ | |
| `report_dir` | file | ✓ | |

**Consequence for CLI incremental builds:** steps that produce only object artifacts
(`BuildInputsStep`, `ValidateTimingStep`, `ValidateCSimStep`, `InspectSynthStep`) always
re-execute on every CLI run — there is nothing on disk to prove they are fresh.  For the
poly flow this is acceptable since these steps are fast.  `PySimStep` and `WriteInputsStep`
produce a mix: the simulation always re-runs, but `WriteInputsStep` is gated on its file
output's freshness.

**Test vectors are already files:** `cmd_hdr` and `samp_in` are object artifacts but are
immediately persisted as `data/cmd_hdr_data.bin` and `data/samp_in_data.bin` by
`WriteInputsStep`.  A future simplification: merge `BuildInputsStep` and `WriteInputsStep`
so that test vector generation produces file artifacts directly, making it skippable on
subsequent runs.

**Extension path — object serialisation:** steps could optionally implement
`serialize(name, value, path)` / `deserialize(name, path)`.  The DAG would transparently
cache object artifacts to disk (numpy `.npy`, JSON, pickle) and reload them on the next
run instead of re-executing the step.  For now, object-only steps always re-run; this is
deferred.

---

## Open Questions

1. **`Buildable`**: fold into `BuildStep` (step writes files and returns their `Path`s),
   or keep as a separate ABC?  Likely fold in — simpler.

2. **`optional` steps**: currently step-level.  In a target model, optionality is more
   naturally "this artifact may or may not exist; downstream steps check before consuming."
   Defer.

3. **`through=` parameter**: could accept an artifact name instead of a step name.
   Defer.

4. **Freshness / caching**: resolved via `expected_paths(config)` hook and
   `BuildDag.results_status(config)`.  The `skip_fresh` optimisation on `BuildDag.run()`
   can be re-implemented using `results_status` once this design lands.

5. **`dataschema` / `arrayutils` migration**: once `Buildable` is decided, migrate these
   to the new pattern.  Defer.
